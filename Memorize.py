try:
    from Utils import *
except ModuleNotFoundError:
    try:
        from .Utils import *
    except ModuleNotFoundError:
        print("No Utils")
        import sys
        sys.exit()

from re import compile as Regex, escape as re_escape, MULTILINE, IGNORECASE
from fsrs import State, Rating, Scheduler, Card as _Card
from json import loads as json
from shutil import copyfileobj
from hashlib import sha1
from zipfile import ZipFile
from os.path import commonprefix
from sqlite3 import register_adapter
from datetime import datetime, timedelta, timezone

class Context(Gtk.PopoverMenu):
    __gtype_name__ = "Context"

    widget = GObject.Property(type=GObject.Object)

    def __init__(self):
        super().__init__(has_arrow=False, width_request=100)

    @staticmethod
    def activate(a, s, window): Context.action(window, widget=window.context.widget if window.context else None, action=s.unpack().strip("'"))

    @staticmethod
    def action(window, widget, action=""):
        if action == "Review":
            if not isinstance(widget, DeckWidget): return
            e = widget.deck.get_review()
            if e: return e.get_root().present()
            p = Adw.NavigationPage.new(Review(widget.deck), "Review")
            p.get_child().bind_property("name", p, "title", DEFAULT_BIND)
            widget.get_root().view.push(p)
        if action == "Edit":
            if isinstance(widget, Card):
                if hasattr(window, "get_selected"):
                    l = window.get_selected()
                    if not widget in l: l.append(widget)
                    CardEditor(cards=l, deck=window.deck, root=window)
                else: CardEditor(cards=[widget], deck=window.deck, root=window.get_root())
            if isinstance(widget, DeckWidget): DeckEditor(widget.deck)
        if action == "Stats":
            if isinstance(widget, DeckWidget):
                p = Adw.NavigationPage.new(Stats(widget.deck), "Stats")
                p.get_child().bind_property("name", p, "title", DEFAULT_BIND)
                widget.get_root().view.push(p)
        if action == "Import Cards":
            deck = False
            if isinstance(window, CardsList):
                deck = window.deck
            if isinstance(widget, DeckWidget):
                deck = widget.deck
            if deck: Gtk.FileDialog().open(window.get_root(), None, ImportCards.do_open, (deck, window.get_root()))
        if action.startswith("Export"):
            if isinstance(widget, DeckWidget): Gtk.FileDialog(initial_name=widget.deck.name).save(window.get_root(), None, Deck.dialog_export, (widget.deck, "Data" in action))

class Deck(ListObject):
    __gtype_name__ = "Deck"

    id = GObject.Property(type=int, nick="ID", default=0)

    @GObject.Property(type=str)
    def name(self): return getattr(self, "_name", "")

    @name.setter
    def name(self, value):
        old_name = self.name
        self._name = value
        if old_name:
            App.default.data.update(table=self.__class__.__gtype_name__ + "s", column="Name", value=self.name, conditions="Name = ?", conds=(old_name,))
            Task.invoke(App.default.decks.do_dict)

    cover_paintable = GObject.Property(type=Gdk.Paintable)
    cover = GObject.Property(type=str)
    hidden = GObject.Property(type=bool, default=False)
    new_cards_per_day = GObject.Property(type=int, nick="New Cards/Day", default=20)
    new_cards_review_order = GObject.Property(type=str, default="After Due")
    new_cards_review_orders = ["After Due", "Before Due"]
    desired_retention = GObject.Property(type=float, default=0.9)
    learning_steps = GObject.Property(type=object)
    relearning_steps = GObject.Property(type=object)
    maximum_interval = GObject.Property(type=int, default=36500)
    enable_fuzzing = GObject.Property(type=bool, default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.id == 0:
            self.changing = True
            self.id = App.default.data.read(table="Decks", column="ID", conditions="Name = ?", conds=(self.name,))
            self.changing = False

    def cards_init(self):
        if getattr(self, "cards_inited", False): return
        c = App.default.data.read(table="Cards", conditions="Deck = ?", conds=(self.name,), return_dict=True, force_tuple=True)
        now = GLib.DateTime.new_now_utc()
        for i in c:
            i["Deck"] = self
            i["Due"] = GLib.DateTime.new_from_unix_utc(i["Due"]) if isinstance(i["Due"], int) else now
        App.default.cards.extend(c)
        self.cards_inited = True

    def adapter(self): return self.name

    def learning_steps_from(self, v): return v.split(" ") if v else None
    def relearning_steps_from(self, v): return v.split(" ") if v else None

    def get_review_cards(self, cards=False, reviews=False, show_hidden=False):
        if cards == False:
            cards = self.get_cards()
        if not cards: return []
        if not show_hidden:
            cards = [i for i in cards if not i.hidden]
        new_cards = [i for i in cards if not i.last_review]
        now = GLib.DateTime.new_now_utc()
        to_review = sorted([i for i in cards if not i in new_cards and i.due.compare(now) != 1], key=due_sort)
        new_cards_today = self.new_cards_per_day
        if new_cards and new_cards_today:
            new_cards_today -= self.get_new_cards_today(reviews)
            new_cards_today = max(0, new_cards_today)
            if new_cards_today:
                new_cards = new_cards[:new_cards_today]
            else:
                new_cards = []
        else:
            new_cards = []
        if new_cards:
            if self.new_cards_review_order == "After Due": to_review.extend(new_cards)
            if self.new_cards_review_order == "Before Due":
                new_cards.extend(to_review)
                to_review = new_cards
        return to_review

    def get_new_cards_today(self, reviews, timestamp=False):
        if reviews == False:
            reviews = self.get_reviews()
        n = {}
        now = GLib.DateTime.new_now_utc().to_unix()
        for i in reviews:
            if not i["ID"] in n or n[i["ID"]] > i["Review Time"]:
                n[i["ID"]] = i["Review Time"]
        if timestamp: return min([i for i in n.values() if GLib.TIME_SPAN_DAY / 1e6 > abs(now - i)])
        v = 0
        for i in n.values():
            if GLib.TIME_SPAN_DAY / 1e6 > abs(now - i):
                v += 1
        return v

    def get_cards(self):
        self.cards_init()
        return sorted((i for i in App.default.cards if i.deck == self), key=id_sort)

    def get_review(self):
        e = [i for i in Review.get_all() if i.deck == self]
        if e: return e[0]

    def get_reviews(self): return App.default.data.read(table="Reviews", return_dict=True, force_tuple=True, conditions="Deck = ?", conds=(self.name,))

    @staticmethod
    def dialog_export(d, r, data):
        deck, with_data = data
        try:
            file = d.save_finish(r)
        except: return
        Task(deck.export, file, with_data=with_data).start()

    def export(self, file, with_data=False):
        Task.invoke(Toast, f"Exporting {self.name} to {file.get_path()}", timeout=0)
        path = Path(CACHE, self.name)
        if exists(path): delete(path)
        data = Data(path, DATA_INIT)
        d = App.default.data.read(table="Decks", conditions="Name = ?", conds=(self.name,), return_dict=True)
        d["ID"] = 1
        data.new(table="Decks", names=tuple(d.keys()), values=tuple(d.values()))
        for i in App.default.data.read(table="Cards", conditions="Deck = ?", conds=(self.name,), return_dict=True, force_tuple=True):
            if not with_data:
                del i["State"]
                del i["Step"]
                del i["Stability"]
                del i["Difficulty"]
                del i["Due"]
                del i["Last Review"]
            data.new(table="Cards", names=tuple(i.keys()), values=tuple(i.values()))
        if with_data:
            for i in self.get_reviews(): data.new(table="Reviews", names=tuple(i.keys()), values=tuple(i.values()))
        data.data.close()
        copy(path, file.get_path())
        delete(path)
        Task.invoke(Toast, f"Exported {self.name} to {file.get_path()}", timeout=0)

    @staticmethod
    def changed(self, p):
        if p.get_name() == "cover-paintable": return
        if getattr(self, "changing", False): return
        name = p.get_name().replace("-", "_")
        v = getattr(self, name, None)
        p = p.get_nick() or name.title().replace("_", " ")
        t = self.__class__.__gtype_name__ + "s"
        c = "Name = ?"
        if p == "Cover":
            self.cover_paintable = None
        if p == "Hidden":
            if Window.current: Window.current.do_search()
        if isinstance(v, list):
            v = " ".join(v)
        App.default.data.update(table=t, column=p, value=v, conditions=c, conds=(self.name,))
        e = self.get_review()
        if e: e.start()

    def delete(self):
        e = self.get_review()
        if e:
            view = e.get_ancestor(Adw.NavigationView)
            if view:
                view.get_visible_page().set_child(None)
                view.pop()
            else: e.get_root().close()
        App.default.data.delete(table="Cards", conditions="Deck = ?", conds=(self.name,))
        App.default.data.delete(table="Reviews", conditions="Deck = ?", conds=(self.name,))
        App.default.cards.replace([i for i in App.default.cards if not i.deck == self])
        App.default.data.delete(table="Decks", conditions="Name = ?", conds=(self.name,))
        App.default.decks.remove(self)

    @staticmethod
    def items_changed(l, *_):
        if Window.current:
            for i in Window.current.catalog:
                if not i.deck in l: i.unparent()
            Task.invoke(Window.current.do_search)

    def get_scheduler(self):
        kwargs = {
            "desired_retention": self.desired_retention if self.desired_retention else 0.9,
            "maximum_interval": self.maximum_interval if self.maximum_interval else 36500,
            "enable_fuzzing": self.enable_fuzzing
        }
        for l in ("re", ""):
            name = f"{l}learning_steps"
            l = getattr(self, name, "")
            if isinstance(l, str) and l and all(i.isdigit() for i in l.split(" ")):
                kwargs[name] = tuple(timedelta(seconds=int(i)) for i in l.split(" "))
        return Scheduler(**kwargs)

class DeckEditor(PopupDialog):
    __gtype_name__ = "DeckEditor"
    deck = GObject.Property(type=Deck)
    rows = GObject.Property(type=object)
    new_cards_review_order_model = Gtk.StringList.new(Deck.new_cards_review_orders)
    def __init__(self, deck=None):
        group = Adw.PreferencesGroup()
        page = GroupsPage()
        page.add(group)
        toolbar = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        nav = Adw.NavigationView()
        p = Adw.NavigationPage.new(toolbar, deck.name if deck else "New Deck")
        nav.add(p)
        super().__init__(content=nav, dialog_kwargs={"content_width": 600, "content_height": 600}, window_kwargs={"default_width": 600, "default_height": 600})
        self.rows = []
        self.deck = deck
        name_row = Adw.EntryRow(title="Name")
        cover = URIRow(title="Cover", multiple=False)
        hidden = Adw.SwitchRow(title="Hidden")
        for i in (name_row, cover, hidden):
            self.rows.append(i)
            group.add(i)
        new_cards_per_day = Adw.SpinRow.new_with_range(0, 1e3, 1)
        new_cards_per_day.set_properties(title="New Cards/Day", value=20)
        new_cards_review_order = Adw.ComboRow(title="New Cards Review Order", model=self.__class__.new_cards_review_order_model)
        self.rows.append(new_cards_review_order)
        group = Adw.PreferencesGroup()
        page.add(group)
        for i in (new_cards_per_day, new_cards_review_order):
            self.rows.append(i)
            group.add(i)
        if deck:
            name_row.set_text(deck.name)
            name_row.connect("changed", self.__class__.name_changed)
            deck.bind_property("hidden", hidden, "active", SYNC_BIND)
            deck.bind_property("cover", cover, "text", SYNC_BIND)
            deck.bind_property("new-cards-per-day", new_cards_per_day, "value", SYNC_BIND)
            deck.bind_property("new-cards-review-order", new_cards_review_order, "selected", SYNC_BIND, self.__class__.review_order_from, self.__class__.review_order_to)
            deck.bind_property("name", self, "title", DEFAULT_BIND)
            self.bind_property("title", p, "title", DEFAULT_BIND)
            group.add(Button(t=Adw.ButtonRow, callback=self.__class__.push_cards, title="Cards", start_icon_name="folder-documents"))
            header.pack_start(Button(callback=self.__class__.do_delete, icon_name="user-trash", tooltip_text="Delete", css_classes=("flat", "destructive-action",)))
        else: header.pack_start(Button(icon_name="list-add", tooltip_text="Add", callback=self.__class__.do_add))
        header.pack_end(PopupDialogButton())
        self.do_advanced(page)
        self.present()

    @staticmethod
    def review_order_from(b, v): return Deck.new_cards_review_orders.index(v)

    @staticmethod
    def review_order_to(b, v): return Deck.new_cards_review_orders[v]

    def do_advanced(self, page):
        group = Adw.PreferencesGroup()
        a = Adw.ExpanderRow(title="Advanced")
        group.add(a)
        page.add(group)
        rows = []
        r = Adw.SpinRow.new_with_range(0, 1, 0.1)
        r.set_properties(value=0.9, title="Desired Retention")
        rows.append(r)
        rows.append(TagRow(allow_duplicates=True, tags=["60", "600"], title="Learning Steps", tag="Step"))
        rows.append(TagRow(allow_duplicates=True, tags=["600"], title="Relearning Steps", tag="Step"))
        r = Adw.SpinRow.new_with_range(0, 36500, 1)
        r.set_properties(value=36500, title="Maximum Interval")
        rows.append(r)
        rows.append(Adw.SwitchRow(title="Enable Fuzzing", active=True))
        for i in rows:
            self.rows.append(i)
            if self.deck: self.deck.bind_property(i.get_title().lower().replace(" ", "_"), i, "tags" if isinstance(i, TagRow) else "active" if isinstance(i, Adw.SwitchRow) else "value", SYNC_BIND, convert_bind if isinstance(i, TagRow) else None, convert_bind if isinstance(i, TagRow) else None)
            a.add_row(i)
        if self.deck:
            r = Button(t=Adw.ButtonRow, callback=self.__class__.reset_review_log, title="Reset Review Log", start_icon_name="edit-clear-all")
            r.add_css_class("destructive-action")
            a.add_row(r)

    @staticmethod
    def do_actually_reset_review_log(d, r):
        if r != "confirm":
            del d.editor
            return
        App.default.data.delete(table="Reviews", conditions="Deck = ?", conds=(d.editor.deck.name,))
        del d.editor

    @staticmethod
    def reset_review_log(b):
        self = DeckEditor.get_self(b)
        if not self: return
        p = Prompt(heading="Reset Review Log?", confirm_callback=False, callback=self.__class__.do_actually_reset_review_log)
        p.editor = self
        p.present(self.get_root())

    @staticmethod
    def push_cards(b):
        self = DeckEditor.get_self(b)
        p = Adw.NavigationPage.new(CardsList(self.deck), "Cards")
        self.get_content().push(p)

    @staticmethod
    def name_changed(w, p=None):
        self = DeckEditor.get_self(w)
        if not self: return
        n = App.default.decks[self.get_title()]
        if not w.get_text(): return Toast("Name can't be empty")
        if w.get_text() == n.name: return
        if App.default.decks[w.get_text()]: return Toast(f"{w.get_text()} already exists!")
        n.name = w.get_text()

    @staticmethod
    def do_actually_delete(d, r):
        if r != "confirm":
            del d.editor
            return
        t = App.default.decks[d.editor.get_title()]
        if t: t.delete()
        d.editor.close()
        del d.editor

    @staticmethod
    def do_delete(b):
        self = DeckEditor.get_self(b)
        if not self: return
        p = Prompt(heading=f"Delete {self.get_title()}?", confirm_callback=False, callback=self.__class__.do_actually_delete)
        p.editor = self
        p.present(self.get_root())

    @staticmethod
    def do_add(b):
        self = DeckEditor.get_self(b)
        if not self.rows[0].get_text(): return Toast("Name cannot be empty")
        if App.default.decks[self.rows[0].get_text()]: return Toast(f"{self.rows[0].get_text()} already exists")
        v = {"ID": max((i.id for i in App.default.decks), default=0) + 1}
        for i in self.rows:
            if isinstance(i, Adw.EntryRow) or isinstance(i, URIRow):
                v[i.get_title()] = i.get_text()
            elif isinstance(i, Adw.SwitchRow):
                v[i.get_title()] = i.get_active()
            elif isinstance(i, Adw.SpinRow):
                v[i.get_title()] = i.get_value()
            elif isinstance(i, TagRow):
                v[i.get_title()] = " ".join(i.tags)
        App.default.data.new(table="Decks", names=tuple(v.keys()), values=tuple(v.values()))
        App.default.decks.add(v)
        if Window.current: Window.current.do_search()
        self.close()

class DeckWidget(Gtk.Button):
    __gtype_name__ = "DeckWidget"
    deck = GObject.Property(type=Deck)
    def __init__(self, deck=None):
        avatar = Adw.Avatar(size=250, show_initials=True)
        avatar.bind_property("text", avatar, "tooltip-text", DEFAULT_BIND)
        super().__init__(overflow=Gtk.Overflow.HIDDEN, child=avatar)
        if Window.current: Window.current.breakpoint.add_setter(avatar, "size", 130)
        self.add_css_class("flat")
        self.add_css_class("circular")
        self.add_css_class("deck")
        self.deck = deck
        if self.deck:
            self.deck.bind_property("name", avatar, "text", DEFAULT_BIND)
            self.deck.bind_property("cover-paintable", avatar, "custom-image", DEFAULT_BIND)
            if not self.deck.cover_paintable:
                cover = self.deck.cover
                if cover:
                    c = get_cover(cover)
                    self.deck.cover_paintable = Adw.SpinnerPaintable.new(self) if c == True else c
                    if c == True:
                        self.deck.cover_paintable.uri = cover

    @staticmethod
    def do_clicked(self): Context.action(self.get_root(), widget=self, action="Review")

class ImportCards:
    model = Gtk.StringList.new(("New Line", "Tab", "Space", ";", "|", ","))
    def __init__(self, file, deck, root=None):
        super().__init__()
        try:
            t = file.query_info("*", Gio.FileQueryInfoFlags.NONE).get_content_type()
        except:
            t = ""
        if file.peek_path().endswith((".apkg", ".colpkg")): AnkiImport(file, deck, root)
        elif t.startswith("text/"):
            d = Prompt(callback=self.__class__.do, heading="Select Separator", extra_child=Gtk.DropDown(model=self.__class__.model))
            d.deck = deck
            d.file = file
            d.present(root)

    @staticmethod
    def do(self):
        r = get_contents(self.file).decode()
        self.file = None
        split = self.get_extra_child().get_selected_item().get_string()
        if split == "Space":
            split = " "
        if split == "New Line":
            split = "\n"
        if split == "Tab":
            split = "\t"
        cards = []
        for i in Regex(rf"(?<!\\){split}").split(r):
            if not cards or "Back" in cards[-1]:
                cards.append({"Front": i})
                continue
            if "Front" in cards[-1]:
                cards[-1]["Back"] = i
        n = len(self.deck.get_cards())
        for i in cards:
            n += 1
            v = {"Deck": self.deck.name, "ID": n, "Front": i.get("Front", ""), "Back": i.get("Back", "")}
            App.default.data.new(table="Cards", names=tuple(v.keys()), values=tuple(v.values()))
            App.default.cards.add(v)
        Toast(f"Added one card to {self.deck.name}" if len(cards) == 1 else f"Added {len(cards)} cards to {self.deck.name}")
        self.deck = None

    @staticmethod
    def do_open(d, r, da):
        try:
            f = d.open_finish(r)
        except:
            f = False
        if f: ImportCards(f, *da)

class Card(ListObject):
    __gtype_name__ = "Card"

    deck = GObject.Property(type=Deck)
    states = ["Learning", "Review", "Relearning"]

    @GObject.Property(type=int, nick="ID")
    def id(self): return getattr(self, "_id", 0)

    @id.setter
    def id(self, value):
        old_id = self.id
        self._id = value
        if old_id: App.default.data.update(table=self.__class__.__gtype_name__ + "s", column="ID", value=self.id, conditions="Deck = ? AND ID = ?", conds=(self.deck.name, old_id,))

    front = GObject.Property(type=str, default="")
    back = GObject.Property(type=str, default="")
    state = GObject.Property(type=str, default="Learning")
    step = GObject.Property(type=object)
    stability = GObject.Property(type=object)
    difficulty = GObject.Property(type=object)
    due = GObject.Property(type=GLib.DateTime)
    last_review = GObject.Property(type=GLib.DateTime)
    hidden = GObject.Property(type=bool, default=False)

    def deck_from(self, v): return v if isinstance(v, Deck) else App.default.decks[v] if isinstance(v, str) else None
    def due_from(self, v): return v if isinstance(v, GLib.DateTime) else GLib.DateTime.new_from_unix_utc(v) if isinstance(v, int) else GLib.DateTime.new_now_utc()
    def last_review_from(self, v): return v if isinstance(v, GLib.DateTime) else GLib.DateTime.new_from_unix_utc(v) if isinstance(v, int) else None

    @staticmethod
    def changed(self, p):
        if p.get_name() == "deck": return
        name = p.get_name().replace("-", "_")
        v = getattr(self, name, None)
        p = p.get_nick() or name.title().replace("_", " ")
        t = self.__class__.__gtype_name__ + "s"
        if p == "Hidden":
            for i in CardsList.get_all():
                if i.deck == self.deck: i.hidden_filter.changed(Gtk.FilterChange.DIFFERENT)
        c = "Deck = ? AND ID = ?"
        App.default.data.update(table=t, column=p, value=v, conditions=c, conds=(self.deck.name, self.id))

    def delete(self):
        App.default.cards.remove(self)
        App.default.data.delete(table="Reviews", conditions="Deck = ? AND ID = ?", conds=(self.deck.name, self.id))
        App.default.data.delete(table="Cards", conditions="Deck = ? AND ID = ?", conds=(self.deck.name, self.id))
        r = self.deck.get_review()
        if r and r.cards and self in r.cards: r.start()

    def answer(self, answer, review_duration=None):
        rating = getattr(Rating, answer, None)
        if rating == None: return
        card = self.to_card()
        new, log = self.deck.get_scheduler().review_card(card, rating, review_duration=(GLib.DateTime.new_now_utc().difference(review_duration) / GLib.TIME_SPAN_MILLISECOND) if review_duration else None)
        log = {"Deck": self.deck.name, "ID": self.id, "Rating": answer, "Review Time": GLib.DateTime.new_from_iso8601(log.review_datetime.isoformat(), GLib.TimeZone.new_utc()).to_utc().to_unix(), "Review Duration": log.review_duration}
        c = [self, card, log]
        App.default.undo.insert(0, c)
        App.default.lookup_action("undo").set_enabled(True)
        Task.invoke(self.from_card, new)
        App.default.data.new(table="Reviews", names=tuple(log.keys()), values=tuple(log.values()))

    def to_card(self):
        state = getattr(State, self.state if self.state else "Learning", None)
        if state == None:
            state = State.Learning
        return _Card(card_id=self.id, state=state, step=self.step, stability=self.stability, difficulty=self.difficulty, due=datetime.fromtimestamp(self.due.to_unix(), timezone.utc) if self.due else None, last_review=datetime.fromtimestamp(self.last_review.to_unix(), timezone.utc) if self.last_review else None)

    def from_card(self, card):
        self.state = card.state.name
        self.step = card.step
        self.stability = card.stability
        self.difficulty = card.difficulty
        self.due = GLib.DateTime.new_from_iso8601(card.due.isoformat(), GLib.TimeZone.new_utc()) if card.due else None
        self.last_review = GLib.DateTime.new_from_iso8601(card.last_review.isoformat(), GLib.TimeZone.new_utc()) if card.last_review else None

class AnkiImport(Adw.Dialog):
    __gtype_name__ = "AnkiImport"
    card = GObject.Property(type=object)
    deck = GObject.Property(type=Deck)
    file = GObject.Property(type=Gio.File)
    pattern = Regex(r"{{((?:\\}|[^}])*)}}")
    data = GObject.Property(type=Data)
    decks = GObject.Property(type=object)
    apkg = GObject.Property(type=object)
    types = GObject.Property(type=object)
    drop_down = GObject.Property(type=Gtk.DropDown)
    deck_drop_down = GObject.Property(type=Gtk.DropDown)
    def __init__(self, file, deck, root=None):
        page = GroupsPage()
        toolbar = Adw.ToolbarView(content=page)
        super().__init__(title="Import Cards", child=toolbar, content_width=540, content_height=540)
        actions = Gio.SimpleActionGroup()
        self.insert_action_group("importcards", actions)
        actions.add_action(Gio.SimpleAction.new("add-field", get_variant("str").get_type()))
        actions.lookup_action("add-field").connect("activate", self.__class__.add_field, self)
        self.deck = deck
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.apkg = f = ZipFile(file.peek_path())
        name = "collection.anki21"
        file = Path(CACHE, name)
        if exists(file): delete(file)
        if "collection.anki21b" in f.namelist():
            f1 = Path(CACHE, name + "b")
            if exists(f1): delete(f1)
            f.extract(name + "b", path=CACHE)
            GLib.spawn_command_line_sync(f"zstd -d \"{f1}\" -o \"{file}\"")
            delete(f1)
        else: f.extract(name, path=CACHE)
        group = Adw.PreferencesGroup()
        page.add(group)
        group2 = Adw.PreferencesGroup()
        page.add(group2)
        self.data = Data(file, readonly=True)
        self.types = {}
        if "notetypes" in self.data.tables():
            self.data.data.create_collation("unicase", self.__class__.unicase)
            decks = dict((i["name"].split("\x1f", 1)[-1], i["id"]) for i in self.data.read(table="decks", return_dict=True, force_tuple=True))
            fields = self.data.read(table="fields", return_dict=True)
            for i in self.data.read(table="notetypes", return_dict=True, force_tuple=True):
                self.types[i["name"]] = {"ID": i["id"], "Fields": dict((it["name"], it["ord"]) for it in fields if it["ntid"] == i["id"])}
        else:
            decks = {}
            for i in self.data.read(table="col", column="decks", force_tuple=True):
                i = json(i)
                for it in i.values():
                    decks[it["name"].split("\x1f", 1)[-1]] = it["id"]
            for i in self.data.read(table="col", column="models", force_tuple=True):
                i = json(i)
                _id = next(iter(i.keys()), False)
                if _id:
                    self.types[i[_id]["name"]] = {"ID": _id, "Fields": dict((it["name"], it["ord"]) for it in i[_id]["flds"])}
        self.decks = dict(decks)
        for i, v in decks.items():
            card = self.data.read(column="id", table="cards", conditions="did = ? LIMIT 1", conds=(v,))
            if not card:
                del self.decks[i]
        if not self.decks:
            Toast("No Cards")
            return
        self.drop_down = Gtk.DropDown(css_classes=["raised"], model=Gtk.StringList.new(sorted(self.types.keys(), key=alphabetical_sort)))
        header.pack_start(Button(callback=self.__class__.start_import, css_classes=["suggested-action"], tooltip_text="Import", icon_name="document-send"))
        header.pack_end(Button(callback=self.__class__.set_media_location, tooltip_text="Media Location", icon_name="applications-multimedia"))
        b = Gtk.Box(css_classes=["linked"])
        bottom_bar = Adw.HeaderBar(title_widget=b, show_start_title_buttons=False, show_end_title_buttons=False)
        toolbar.add_bottom_bar(bottom_bar)
        if len(self.types) > 1: b.append(self.drop_down)
        self.decks_drop_down = Gtk.DropDown(css_classes=["raised"], model=Gtk.StringList.new(sorted(self.decks.keys(), key=alphabetical_sort)))
        if len(self.decks) > 1: b.append(self.decks_drop_down)
        self.connect("closed", self.__class__.do_close)
        self.present(root)
        bu = Button(t=Gtk.MenuButton, icon_name="document-new", tooltip_text="Add Field")
        if self.drop_down.get_parent() or self.decks_drop_down.get_parent(): b.prepend(bu)
        else:
            toolbar.remove(bottom_bar)
            header.pack_end(bu)
        self.drop_down.bind_property("selected-item", bu, "menu-model", DEFAULT_BIND, self.__class__.fields_menu)
        for i in ("front", "back"):
            r = TextRow(title=i.title())
            setattr(self, i, r)
            group.add(r)
            r2 = TextRow(title=i.title() + " Preview")
            setattr(self, i + "_preview", r2)
            r2.text_widget.set_sensitive(False)
            r.bind_property("text", r2, "text", DEFAULT_BIND, self.__class__.preview_bind)
            group2.add(r2)

    @staticmethod
    def varint(b, i):
        n = s = 0
        while True:
            x = b[i]
            i += 1
            n |= (x & 127) << s
            if x < 128: return n, i
            s += 7

    @staticmethod
    def fields(b):
        i = 0
        while i < len(b):
            key, i = AnkiImport.varint(b, i)
            num, wire = key >> 3, key & 7
            if wire == 0:
                value, i = AnkiImport.varint(b, i)
            elif wire == 2:
                size, i = AnkiImport.varint(b, i)
                value, i = b[i:i + size], i + size
            else:
                raise ValueError(f"Unsupported wire type: {wire}")
            yield num, value

    @staticmethod
    def set_media_location(b):
        self = b.get_ancestor(AnkiImport)
        if not self: return
        Gtk.FileDialog().select_folder(self.get_root(), None, AnkiImport.do_set_media_location, self)

    @staticmethod
    def do_set_media_location(d, r, self):
        try: setattr(self, "file", d.select_folder_finish(r))
        except: setattr(self, "file", None)

    @staticmethod
    def start_import(b):
        self = b.get_ancestor(AnkiImport)
        if not self: return
        if not self.drop_down.get_selected_item(): return self.close()
        self.set_child(Adw.Spinner())
        Task(self.do_import).start()

    def do_import(self):
        v = self.types[self.drop_down.get_selected_item().get_string()]
        n, c = 0, max((i.id for i in self.deck.get_cards()), default=0)
        for n, i in enumerate(self.data.read(force_tuple=True, table="cards", column="nid", conditions="did = ? ORDER BY id", conds=(self.decks[self.decks_drop_down.get_selected_item().get_string()],))):
            fields = self.data.read(column="flds", table="notes", conditions="id = ? AND mid = ?", conds=(i, v["ID"]))
            n += 1
            card = {"Deck": self.deck.name, "ID": c + n, "Front": self.fields_to_side(fields, v["Fields"], self.front.text), "Back": self.fields_to_side(fields, v["Fields"], self.back.text)}
            App.default.data.new(table="Cards", names=tuple(card.keys()), values=tuple(card.values()))
            App.default.cards.add(card)
        names = self.apkg.namelist()
        if self.file and "media" in names:
            ex = False
            try:
                if "collection.anki21b" in names:
                    f1 = Path(CACHE, "media")
                    if exists(f1): delete(f1)
                    self.apkg.extract("media", path=CACHE)
                    f2 = Path(CACHE, "media2")
                    GLib.spawn_command_line_sync(f"zstd -d \"{f1}\" -o \"{f2}\"")
                    delete(f1)
                    entries = []
                    for _, entry_data in self.fields(get_contents(f2)):
                        entry = dict(self.fields(entry_data))
                        entries.append((entry[1].decode("utf-8"), entry[2], entry[3], entry.get(255)))
                    delete(f2)
                    for index, (filename, size, expected_hash, legacy_name) in enumerate(entries):
                        member = str(legacy_name) if legacy_name is not None else str(index)
                        if member not in names: continue
                        data = self.apkg.read(member)
                        if len(data) > size:
                            header_size = len(data) - size
                        else:
                            header_size = 0
                        payload = data[header_size:]
                        if sha1(payload, usedforsecurity=False).digest() != expected_hash:
                            payload = data
                        try:
                            with open(self.file.get_child(filename).get_path(), "wb") as w: w.write(payload)
                            ex = True
                        except Exception as e: Task.invoke(Toast, f"Couldn't import {filename}: {e}")
                else:
                    for k, v in json(self.apkg.open("media").read().decode()).items():
                        try:
                            with self.apkg.open(k) as a, open(self.file.get_child(v).get_path(), "wb") as b: copyfileobj(a, b)
                            ex = True
                        except Exception as e: Task.invoke(Toast, f"Couldn't import {v}: {e}")
            except Exception as e: Task.invoke(Toast, f"Couldn't import media {e}")
            if ex: Task.invoke(Toast, f"Imported media to {self.file.get_path()}")
        Task.invoke(self.close)
        Task.invoke(Toast, f"Added one card to {self.deck.name}" if n == 1 else f"Added {n} cards to {self.deck.name}")

    @staticmethod
    def do_close(self):
        self.data.close()
        self.apkg.close()
        delete(self.data.path)
        self.file = self.types = self.deck = self.apkg = self.data = None

    @staticmethod
    def unicase(a, b):
        a, b = a.casefold(), b.casefold()
        return (a > b) - (a < b)

    @staticmethod
    def parse_fields(s):
        pos = 0
        for match in AnkiImport.pattern.finditer(s):
            if match.start() > pos: yield None, s[pos:match.start()]
            yield match.group(1).replace(r"\}", "}"), None
            pos = match.end()
        if pos < len(s): yield None, s[pos:]

    @staticmethod
    def preview_bind(b, v):
        if not v: return ""
        self = b.get_property("source").get_ancestor(AnkiImport)
        if not self: return
        t = self.drop_down.get_selected_item().get_string() if self.drop_down.get_selected_item() else ""
        if b.get_property("source").get_title() == "Back":
            if getattr(self, "card", None): return self.fields_to_side(self.card, self.types[t]["Fields"], v)
            else: return
        else:
            self.card = None
        if t and t in self.types:
            self.card = self.data.read(column="flds", table="notes", conditions="mid = ? ORDER BY RANDOM() LIMIT 1", conds=(self.types[t]["ID"],))
            if self.card:
                self.back_preview.text = self.fields_to_side(self.card, self.types[t]["Fields"], self.back.text)
                return self.fields_to_side(self.card, self.types[t]["Fields"], v)

    @staticmethod
    def fields_to_side(card_fields, fields, pattern):
        if not pattern: return ""
        if not ("{{" in pattern and "}}" in pattern): return pattern
        card_fields = card_fields.split("\x1f")
        r = ""
        for field, text in AnkiImport.parse_fields(pattern):
            if field and field in fields and len(card_fields) > fields[field]:
                r += card_fields[fields[field]]
            elif field:
                r += "{{" + field + "}}"
            elif text:
                r += text
        return r

    @staticmethod
    def fields_menu(b, v):
        if not v: return
        v = v.get_string()
        return Menu(dict((i, f"importcards.add-field::{i}") for i in b.get_property("target").get_ancestor(AnkiImport).types[v]["Fields"]))

    @staticmethod
    def add_field(a, s, d):
        s = "{{" + s.unpack().strip("'") + "}}"
        d.front.text += s
        d.back.text += s

class CardsList(Adw.Bin):
    __gtype_name__ = "CardsList"
    columns = {}
    deck = GObject.Property(type=Deck)
    view = GObject.Property(type=Gtk.ColumnView)
    filter = GObject.Property(type=Gtk.EveryFilter)
    hidden_filter = GObject.Property(type=Gtk.BoolFilter)
    context = GObject.Property(type=Context)
    drop_target_gtypes = (object,)
    def __init__(self, deck=None):
        if deck: deck.cards_init()
        view = Gtk.ColumnView(css_classes=("navigation-sidebar", "view"), model=Gtk.MultiSelection())
        view.connect("activate", self.__class__.activate_view)
        _filter = f = Gtk.FilterListModel(filter=Gtk.EveryFilter())
        f.connect("items-changed", self.__class__.do_status, self)
        search = Gtk.SearchEntry(placeholder_text="Search")
        search.connect("stop-search", self.__class__.stop_search)
        ff = Gtk.CustomFilter()
        ff.set_filter_func(self.__class__.deck_filter, deck)
        f.get_filter().append(ff)
        ff = Gtk.AnyFilter()
        f.get_filter().append(ff)
        for i in ("Front", "Back"):
            s = Gtk.StringFilter.new(Gtk.PropertyExpression.new(Card, None, i.lower()))
            search.bind_property("text", s, "search", DEFAULT_BIND)
            ff.append(s)
        s = Gtk.SortListModel.new(f, None)
        view.get_model().set_model(s)
        view.bind_property("sorter", s, "sorter", DEFAULT_BIND)
        for k, v in self.__class__.columns.items():
            c = Gtk.ColumnViewColumn.new(k, v)
            c.set_properties(expand=True, resizable=True)
            if k in ("ID", "Hidden"): c.set_sorter(Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(Card, None, k.lower())))
            if k in ("Front", "Back", "State"): c.set_sorter(Gtk.StringSorter(expression=Gtk.PropertyExpression.new(Card, None, k.lower())))
            if k in ("Due", "Last Review"): c.set_sorter(Gtk.NumericSorter(expression=Gtk.ClosureExpression.new(int, datetime_adapter, [Gtk.PropertyExpression.new(Card, None, "due")])))
            if k == "Hidden":
                App.default.lookup_action("show-hidden").bind_property("state", c, "visible", DEFAULT_BIND, unpack_bind)
                c.connect("notify::visible", self.__class__.hidden_changed)
            view.append_column(c)
        toolbar = Adw.ToolbarView(content=Gtk.Overlay(child=Gtk.ScrolledWindow(child=view)))
        super().__init__(child=toolbar)
        self.add_css_class(self.__gtype_name__.lower())
        self.view = view
        actions = Gio.SimpleActionGroup()
        self.insert_action_group(self.__gtype_name__.lower(), actions)
        actions.add_action(Gio.SimpleAction.new("context", get_variant("str").get_type()))
        actions.lookup_action("context").connect("activate", Context.activate, self)
        self.filter = f.get_filter()
        self.hidden_filter = Gtk.BoolFilter(invert=True, expression=Gtk.PropertyExpression.new(Card, None, "hidden"))
        self.hidden_changed()
        Window.controller_init(self)
        self.view = view
        self.status = Adw.StatusPage(visible=False)
        self.status.bind_property("visible", self.view, "visible", INVERT_BIND)
        toolbar.get_content().add_overlay(self.status)
        self.deck = deck
        header = Adw.HeaderBar()
        header.pack_start(Button(icon_name="document-new", tooltip_text="New Card", callback=self.__class__.new_card))
        header.pack_end(PopupDialogButton())
        toolbar.add_top_bar(header)
        self.search = search
        search = Gtk.Revealer(css_name="find", transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=300, halign=Gtk.Align.CENTER, child=search, valign=Gtk.Align.START)
        header.pack_end(Button(t=Gtk.ToggleButton, icon_name="edit-find", tooltip_text="Search", bindings=[(None, "active", search, "reveal-child", SYNC_BIND)]))
        toolbar.get_content().add_overlay(search)
        _filter.set_model(App.default.cards)
        self.do_status()

    @staticmethod
    def on_click(e, n, x, y, signal="none"):
        self = e.get_widget()
        b = e.get_current_button()
        if self.context and self.context.get_mapped(): return self.context.popdown()
        if signal != "released": return
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if not w: return
        card = self.widget_to_card(w)
        if b == 3:
            self.context = self.context or Context()
            self.context.widget = card if card else self
            menu = {"!a": Menu({"Edit": "cardslist.context::Edit"})} if card else {}
            menu["!b"] = Menu({"Import Cards": "cardslist.context::Import Cards"})
            self.context.set_menu_model(Menu(menu))
            Window.show_popover_at_widget(self, self.context, self, x, y)

    @staticmethod
    def on_drag_source_prepare(e, x, y):
        self = e.get_widget()
        Window.clear_drop(self)
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        e.f = []
        c = None
        card = self.widget_to_card(w)
        if card:
            l = [i for i in self.get_selected()]
            if not card in l: self.set_selected(card.id)
            e.f = l
        c = Gdk.ContentProvider.new_for_value(e.f) if e.f else None
        e.set_actions((Gdk.DragAction.COPY | Gdk.DragAction.MOVE) if e.f else Gdk.DragAction.NONE)
        return c

    @staticmethod
    def on_drag_source_drag_begin(e, d):
        self = e.get_widget().get_root()
        if e.f:
            l = [i for i in e.f]
            for n, i in enumerate(l):
                l[n] = f"Card {i.id}"
            Gtk.DragIcon.get_for_drag(d).set_child(Gtk.Label(margin_top=4, margin_start=4, label=list_to_str(l), css_classes=("title-4",), ellipsize=Pango.EllipsizeMode.END))

    @staticmethod
    def on_drag_source_drag_end(e, d, de):
        e.f = []
        e.set_content(None)
        e.set_actions(Gdk.DragAction.NONE)
        Window.clear_drop(e.get_widget())

    @staticmethod
    def on_drop_target_motion(e, x, y):
        self = e.get_widget()
        Window.clear_drop(self)
        a = Gdk.DragAction.NONE
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        v = e.get_value()
        b = None
        if isinstance(v, list) and v:
            w, card = self.widget_to_card(w, widget=True)
            if card and not any(i == card for i in v):
                if any(i.deck != self.deck for i in v):
                    a = Gdk.DragAction.COPY
                else:
                    a = Gdk.DragAction.MOVE
                self.highlight = w
            else:
                if any(i.deck != self.deck for i in v):
                    a = Gdk.DragAction.COPY
        if self.highlight: self.highlight.add_css_class("highlight")
        return a

    @staticmethod
    def on_drop_target_drop(e, v, x, y):
        self = e.get_widget()
        cards = self.deck.get_cards()
        if not self.highlight or any(i.deck != self.deck for i in v):
            n = len(cards)
            for i in v:
                if i.deck == self.deck: continue
                n += 1
                v = i.to_dict()
                v["ID"] = n
                v["Deck"] = self.deck
                App.default.data.new(table="Cards", names=tuple(v.keys()), values=tuple(v.values()))
                App.default.cards.add(v)
                Toast(f"Card {n} added to {self.deck.name}")
            e = self.deck.get_review()
            if e: e.start()
            return True
        l = dict((i, n) for n, i in enumerate(cards))
        start = l[self.widget_to_card(self.highlight)]
        end = max(0, max((l[i] for i in v), default=0))
        ll = list(l.keys())
        if start > end:
            for i in v:
                ll.remove(i)
                ll.insert(max(start - 1, 0), i)
        else:
            for i in v:
                ll.remove(i)
                ll.insert(start, i)
        for n, i in enumerate(ll):
            l[i] = n + 1
        for i in ll:
            i.id = -l[i]
        for i in ll:
            i.id = l[i]
        Window.clear_drop(self)
        self.view.get_model().get_model().set_sorter(None)
        self.view.get_model().get_model().set_sorter(self.view.get_sorter())
        return True

    def get_selected(self):
        selection = self.view.get_model()
        return sorted((selection.get_item(i) for i in range(selection.get_property("n-items")) if selection.is_selected(i)), key=id_sort)

    def find(self, a):
        selection = self.view.get_model()
        n = selection.get_property("n-items")
        for i in range(n):
            s = selection.get_item(i).id
            if s == a: return i

    def set_selected(self, a=""):
        selection = self.view.get_model()
        n = self.find(a)
        if n != None: selection.select_item(n, True)
        else: selection.unselect_all()

    def widget_to_card(self, w=None, widget=False):
        card = False
        if w and w.get_ancestor(Gtk.ColumnView):
            while w.__class__.__name__ != "GtkColumnViewRowWidget":
                w = w.get_parent()
                if not w: break
            if not w or w.get_first_child().__class__.__name__ != "GtkColumnViewCellWidget":
                if widget: return (w, card)
                return card
            card = int(w.get_first_child().get_first_child().get_label())
            for i in self.deck.get_cards():
                if i.id == card:
                    card = i
                    break
        if widget: return (w, card)
        return card

    @staticmethod
    def get_all():
        l = []
        for i in DeckEditor.get_all():
            for p in i.get_content():
                if isinstance(p, Adw.NavigationPage) and isinstance(p.get_child(), CardsList) and not p.get_child() in l: l.append(p.get_child())
        return l

    def hidden_changed(column, a=None):
        if isinstance(column, CardsList):
            self = column
        else:
            self = column.get_column_view().get_ancestor(CardsList)
        n = None
        for ni, i in enumerate(self.filter):
            if i == self.hidden_filter:
                n = ni
                break
        if App.default.lookup_action("show-hidden").get_state().unpack():
            if n != None: self.filter.remove(n)
        else: self.filter.append(self.hidden_filter)

    @staticmethod
    def activate_view(view, p):
        selection = view.get_model()
        selected = [selection.get_item(i) for i in range(selection.get_property("n-items")) if selection.is_selected(i)]
        CardEditor(cards=selected, root=view.get_root())

    @staticmethod
    def on_key_pressed(e, v, kc, s):
        self = e.get_widget()
        if s == Gdk.ModifierType.CONTROL_MASK and v == Gdk.KEY_f:
            revealer = self.search.get_ancestor(Gtk.Revealer)
            revealer.set_reveal_child(not revealer.get_reveal_child())
            if revealer.get_reveal_child(): self.search.grab_focus()
        if v == Gdk.KEY_Escape: self.search.get_ancestor(Gtk.Revealer).set_reveal_child(False)
        if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_n: CardEditor(deck=self.deck, root=self.get_root())
        if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_e:
            s = self.get_selected()
            if s: CardEditor(deck=self.deck, cards=s, root=self.get_root())

    @staticmethod
    def stop_search(s): s.get_ancestor(Gtk.Revealer).set_reveal_child(False)

    @staticmethod
    def deck_filter(i, d): return i.deck == d

    @staticmethod
    def new_card(b):
        self = b.get_ancestor(CardsList)
        CardEditor(deck=self.deck, root=b.get_root())

    def do_status(self, a=None, b=None, c=None, d=None):
        if d:
            self = d
        if not hasattr(self, "status"): return
        self.status.set_visible(1 > self.view.get_model().get_property("n-items"))
        if self.status.get_visible():
            if self.search.get_text(): self.status.set_properties(icon_name="edit-find-symbolic", title="No Results")
            else: self.status.set_properties(icon_name="document-new-symbolic", title="No Cards")

    @staticmethod
    def setup(f, l): l.set_child(Gtk.Label(single_line_mode=True, ellipsize=Pango.EllipsizeMode.END))

    @staticmethod
    def bind(f, l):
        l.bind = l.get_property("item").bind_property(f.title.lower().replace(" ", "-"), l.get_child(), "label", DEFAULT_BIND, CardsList.column_bind)

    @staticmethod
    def unbind(f, l):
        if hasattr(l, "bind"):
            l.bind.unbind()
            del l.bind

    @staticmethod
    def column_bind(b, v, t=""):
        if t:
            v = getattr(v, t)
        if isinstance(v, Deck):
            v = v.name
        if isinstance(v, GLib.DateTime):
            v = v.to_local().format("%x %T")
        if type(v) == int:
            if b.get_property("source-property") == "state":
                v = CardEditor.state_model.get_item(v - 1).get_string()
            else:
                v = str(v)
        if type(v) == bool:
            v = str(v)
        return v

    @staticmethod
    def teardown(f, l): l.set_child(None)

    for i in ("ID", "Front", "Back", "State", "Due", "Last Review", "Hidden"):
        factory = Gtk.SignalListItemFactory.new()
        setattr(factory, "title", i)
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        factory.connect("teardown", teardown)
        columns[i] = factory

    factory = None

class CardEditor(PopupDialog):
    __gtype_name__ = "CardEditor"

    deck = GObject.Property(type=Deck)
    cards = GObject.Property(type=object)
    rows = GObject.Property(type=object)
    find = GObject.Property(type=Gtk.SearchEntry)
    replace = GObject.Property(type=Gtk.Entry)

    state_model = Gtk.StringList.new(Card.states)
    def __init__(self, cards=None, root=None, deck=None):
        if not cards:
            cards = []
        page = GroupsPage()
        toolbar = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        header.pack_end(PopupDialogButton())
        toolbar.add_top_bar(header)
        super().__init__(title=f"Edit {len(cards)} Cards" if len(cards) > 1 else "Edit Card" if cards else "New Card", content=toolbar, dialog_kwargs={"content_width": 580, "content_height": 500}, window_kwargs={"default_width": 580, "default_height": 500})
        self.rows = []
        for i in (self.window, self.dialog): Window.controller_init(self, widget=i)
        if cards:
            box = Gtk.Box(css_name="search")
            find = Gtk.SearchEntry(hexpand=True, placeholder_text="Find")
            replace = Gtk.Entry(hexpand=True, primary_icon_tooltip_text="Replace", primary_icon_name="edit-find-replace-symbolic", placeholder_text="Replace")
            b2 = Gtk.Box(css_classes=["linked"])
            b = Button(t=Gtk.MenuButton, menu_model=Menu({"!a": Menu({"Front": "front", "Back": "back"}), "!b": Menu({"Regex": "regex", "Whole Words": "whole-words", "Case Sensitive": "case-sensitive"})}), icon_name="view-more", tooltip_text="Options")
            for i in (replace, b): b2.append(i)
            for i in (find, b2): box.append(i)
            r = Gtk.SearchBar(child=box)
            toolbar.add_top_bar(r)
            header.pack_end(Button(t=Gtk.ToggleButton, bindings=[(None, "active", r, "search-mode-enabled", SYNC_BIND)], icon_name="edit-find-replace", tooltip_text="Find and Replace"))
            self.find = find
            self.replace = replace
            self.replace.connect("activate", self.__class__.replace_activate)
            self.replace.connect("icon-press", self.__class__.icon_press)
        group = Adw.PreferencesGroup()
        front = TextRow(title="Front")
        back = TextRow(title="Back")
        for i in (front, back):
            self.rows.append(i)
            group.add(i)
        page.add(group)
        group = Adw.PreferencesGroup()
        page.add(group)
        hidden = Adw.SwitchRow(title="Hidden")
        self.rows.append(hidden)
        group.add(hidden)
        self.cards = cards
        self.deck = deck
        self.do_advanced(page)
        if cards:
            header.pack_start(Button(callback=self.__class__.do_delete, icon_name="user-trash", tooltip_text="Delete", css_classes=("flat", "destructive-action",)))
            if len(cards) == 1:
                card = cards[0]
                card.bind_property("front", front, "text", SYNC_BIND)
                card.bind_property("back", back, "text", SYNC_BIND)
                card.bind_property("hidden", hidden, "active", SYNC_BIND)
            else:
                for i in self.rows:
                    if i.get_title() in ("Front", "Back"): i.set_text("".join(self.__class__.split_common([(getattr(it, i.get_title().lower(), "") or "") for it in self.cards])))
                    i.connect("notify::" + ("text" if isinstance(i, TextRow) else "active" if isinstance(i, Adw.SwitchRow) else "value" if isinstance(i, Adw.SpinRow) else "selected" if isinstance(i, Adw.ComboRow) else "date"), self.__class__.changed)
        else: header.pack_start(Button(icon_name="list-add", tooltip_text="Add", callback=self.__class__.do_add))
        header.pack_start(Button(icon_name="applications-multimedia", tooltip_text="Add Media", callback=self.__class__.add_media))
        self.present(root)
        front.text_widget.grab_focus()

    @staticmethod
    def changed(r, p=None):
        self = CardEditor.get_self(r)
        if r.get_title() in ("Front", "Back"):
            p = r.get_title().lower()
            prefix, d, suffix = self.__class__.split_common([getattr(i, p, "") or "" for i in self.cards])
            if d and not d in r.get_text():
                Toast(f"Nothing done! {d} not found!")
                return
            if d:
                nprefix, nsuffix = r.get_text().split(d, 1)
            else:
                nprefix, nsuffix = r.get_text(), ""
            for n, i in enumerate(self.cards): setattr(i, p, self.__class__.get_new(getattr(i, p), prefix, nprefix, suffix, nsuffix, d))
            return
        v = r.get_property(p.get_name())
        if r.get_title() == "State":
            v = self.__class__.state_to(None, v)
        p = r.get_title().lower().replace(" ", "_")
        for i in self.cards: setattr(i, p, v)

    @staticmethod
    def get_new(o, prefix, nprefix, suffix, nsuffix, d):
        if d:
            no = o
            if prefix:
                no = no.split(prefix,1)[-1]
            if nprefix:
                no = nprefix + no
            if suffix:
                no = no.rsplit(suffix, 1)[0]
            if nsuffix:
                no += nsuffix
            return no
        else: return nprefix + nsuffix

    @staticmethod
    def split_common(strings):
        prefix = commonprefix(strings)
        max_suffix_len = min(len(s) - len(prefix) for s in strings)
        suffix = ""
        for i in range(max_suffix_len):
            chars = {s[-1-i] for s in strings}
            if len(chars) != 1: break
            suffix = next(iter(chars)) + suffix
        d = "" if all(i == suffix + prefix for i in strings) else "{{Different}}"
        return [prefix, d, suffix]

    def do_advanced(self, page):
        group = Adw.PreferencesGroup()
        page.add(group)
        a = Adw.ExpanderRow(title="Advanced")
        group.add(a)
        rows = []
        r = Adw.ComboRow(title="State", model=self.__class__.state_model)
        rows.append(r)
        r = Adw.SpinRow.new_with_range(0, 1e3, 1)
        r.set_properties(value=0, title="Step")
        rows.append(r)
        r = Adw.SpinRow.new_with_range(0.001, 100.0, 0.5)
        r.set_properties(value=0, title="Stability")
        rows.append(r)
        r = Adw.SpinRow.new_with_range(1.0, 10.0, 0.1)
        r.set_properties(value=0, title="Difficulty")
        rows.append(r)
        rows.append(DateRow(title="Due"))
        rows.append(DateRow(title="Last Review"))
        for i in rows:
            if self.cards and len(self.cards) == 1:
                card = self.cards[0]
                card.bind_property(i.get_title().lower().replace(" ", "-"), i, "value" if isinstance(i, Adw.SpinRow) else "date" if isinstance(i, DateRow) else "selected", SYNC_BIND, self.__class__.state_from if i.get_title() == "State" else convert_bind, self.__class__.state_to if i.get_title() == "State" else convert_bind if not i.get_title() in ("State", "Due") else None)
            elif not self.cards:
                if not i.get_title() in ("State", "Due"):
                    i.set = False
                    i.connect(f"notify::{'value' if isinstance(i, Adw.SpinRow) else 'date'}", self.__class__.apply_value)
                else:
                    i.set = True
            self.rows.append(i)
            a.add_row(i)

    @staticmethod
    def state_from(b, v): return Card.states.index(v)

    @staticmethod
    def state_to(b, v): return Card.states[v]

    @staticmethod
    def apply_value(w, p):
        w.set = True

    def do_replace(self):
        find = rf"{self.find.get_text()}"
        if not App.default.lookup_action("regex").get_state().unpack():
            find = re_escape(find)
            def replace(*_): return self.replace.get_text()
        else:
            replace = self.replace.get_text()
        if App.default.lookup_action("whole-words").get_state().unpack():
            find = fr"\b{find}\b"
        flag = MULTILINE
        if not App.default.lookup_action("case-sensitive").get_state().unpack():
            flag = flag | IGNORECASE
        regex = Regex(find, flag)
        n = 0
        sides = []
        if App.default.lookup_action("front").get_state().unpack(): sides.append("front")
        if App.default.lookup_action("back").get_state().unpack(): sides.append("back")
        for c in self.cards:
            rd = False
            for i in sides:
                t = getattr(c, i, "")
                new = regex.sub(replace, t)
                if new != t:
                    rd = True
                    setattr(c, i, new)
            if rd:
                n += 1
        Toast("Replaced one card" if n == 1 else f"Replaced {n} cards")

    @staticmethod
    def replace_activate(s):
        self = CardEditor.get_self(s)
        if self: self.do_replace()

    @staticmethod
    def icon_press(s, b):
        self = CardEditor.get_self(s)
        if self and b == 0: self.do_replace()

    @staticmethod
    def do_add_media(p, r):
        if r != "confirm":
            del p.editor
            return
        uri = p.get_extra_child().get_row(0).get_text()
        a = "/" + uri_decode(uri).strip("/")
        if has_prefix(a, App.default.data_folder.get_path()):
            uri = uri_encode("/" + a.split(App.default.data_folder.get_path(), 1)[-1].strip("/")).replace("///", "//")
        self = p.editor
        for i in (self.rows[0], self.rows[1]): i.set_text((i.get_text().strip() + f"\n[{uri}]").strip())
        del p.editor

    @staticmethod
    def add_media(b):
        self = CardEditor.get_self(b)
        group = Adw.PreferencesGroup()
        group.add(URIRow(multiple=False))
        p = Prompt(callback=self.__class__.do_add_media, confirm_callback=False, heading="Add URI", extra_child=group)
        p.editor = self
        p.present(self.get_root())

    @staticmethod
    def on_key_pressed(e, v, kc, s):
        self = CardEditor.get_self(e.get_widget())
        if self.find and s == Gdk.ModifierType.CONTROL_MASK and v == Gdk.KEY_f:
            revealer = self.find.get_ancestor(Gtk.SearchBar)
            revealer.set_search_mode(not revealer.get_search_mode())
            if revealer.get_search_mode(): self.find.grab_focus()
            return True
        if self.find and v == Gdk.KEY_Escape and self.find.get_mapped():
            self.find.get_ancestor(Gtk.SearchBar).set_search_mode(False)
            return True

    @staticmethod
    def do_actually_delete(d, r):
        if r != "confirm":
            del d.editor
            return
        self = d.editor
        ids_to_delete = [i.id for i in self.cards]
        min_id = min(ids_to_delete)
        chunk_size = 500
        for i in range(0, len(ids_to_delete), chunk_size):
            chunk = ids_to_delete[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            App.default.data.delete(table="Cards", conditions=f"Deck = ? AND ID IN ({placeholders})", conds=(self.deck.name, *chunk))
            App.default.data.delete(table="Reviews", conditions=f"Deck = ? AND ID IN ({placeholders})", conds=(self.deck.name, *chunk))
        for i in reversed([i for i, card in enumerate(App.default.cards) if card in self.cards]): App.default.cards.remove(i)
        l = [i for i in self.deck.get_cards() if i.id > min_id]
        if l:
            n = min([i.id for i in l]) - 1
            for i in l:
                i.id = n
                n += 1
        d.editor.close()
        del d.editor

    @staticmethod
    def do_delete(b):
        self = CardEditor.get_self(b)
        if not self: return
        p = Prompt(heading=f"{self.get_title().replace('Edit', 'Delete')}?", confirm_callback=False, callback=self.__class__.do_actually_delete)
        p.editor = self
        p.present(self.get_root())

    @staticmethod
    def do_add(b):
        self = CardEditor.get_self(b)
        l = self.deck.get_cards()
        for i in l:
            i.id = -i.id
        n = 0
        for i in l:
            n += 1
            i.id = n
        n += 1
        v = {"Deck": self.deck.name, "ID": n}
        for i in self.rows:
            if i.get_ancestor(Adw.ExpanderRow) and not i.set: continue
            if isinstance(i, Adw.EntryRow) or isinstance(i, URIRow) or isinstance(i, TextRow):
                v[i.get_title()] = i.get_text()
            elif isinstance(i, Adw.SwitchRow):
                v[i.get_title()] = i.get_active()
            elif isinstance(i, Adw.SpinRow):
                v[i.get_title()] = i.get_value()
            elif isinstance(i, DateRow):
                v[i.get_title()] = i.get_date()
            elif isinstance(i, Adw.ComboRow):
                v[i.get_title()] = i.get_selected_item().get_string()
        App.default.data.new(table="Cards", names=tuple(v.keys()), values=tuple(v.values()))
        App.default.cards.add(v)
        Toast(f"Card {n} added to {self.deck.name}")
        e = self.deck.get_review()
        if e: e.start()

class Review(Adw.BreakpointBin):
    __gtype_name__ = "Review"
    title = GObject.Property(type=Gtk.Label)
    deck = GObject.Property(type=Deck)
    status = GObject.Property(type=Adw.StatusPage)
    context = GObject.Property(type=Context)
    card = GObject.Property(type=Adw.Bin)
    side = GObject.Property(type=str, default="Front")
    cards = GObject.Property(type=object)
    review_duration = GObject.Property(type=GLib.DateTime)
    acts = Gtk.StringList.new(("Good", "Hard", "Easy", "Again", "Toggle Side"))
    automatic_mode_seconds = GObject.Property(type=int, default=1, minimum=1)
    automatic_mode_action = GObject.Property(type=str, default="Toggle Side")
    automatic_mode = GObject.Property(type=bool, default=False)

    def __init__(self, deck):
        status = Adw.StatusPage(focusable=True)
        card = Adw.Bin(css_name="card", focusable=True, hexpand=True, vexpand=True)
        card.connect("map", self.__class__.card_map)
        status.bind_property("visible", card, "visible", INVERT_BIND)
        toolbar = Adw.ToolbarView(content=Gtk.Overlay(child=Gtk.ScrolledWindow(child=Gtk.Viewport(child=card))))
        toolbar.get_content().add_overlay(status)
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        bottom = Adw.HeaderBar(title_widget=Button(css_classes=["flat", "circular"], callback=self.__class__.button_change_sides), show_end_title_buttons=False, show_start_title_buttons=False, show_back_button=False)
        bottom.get_title_widget().bind_property("icon-name", bottom.get_title_widget(), "tooltip-text", DEFAULT_BIND, self.__class__.button_bind_tooltip)
        for n, d in enumerate(({"Good": "thumbs-up", "Easy": "face-cool"}, {"Again": "face-tired", "Hard": "thumbs-down"})):
            b = Gtk.Box(css_classes=["linked"])
            if n == 0: bottom.pack_end(b)
            else: bottom.pack_start(b)
            for k, v in d.items():
                i = Button(icon_name=v, tooltip_text=k, callback=self.__class__.button_answer)
                i.add_css_class(k.lower())
                b.append(i)
        toolbar.add_bottom_bar(bottom)
        card.bind_property("visible", toolbar, "reveal-bottom-bars", DEFAULT_BIND)
        super().__init__(css_name="review", child=toolbar, height_request=1, width_request=1)
        self.connect("notify::root", self.__class__.root_changed)
        b = Adw.Breakpoint.new(Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 800, Adw.LengthUnit.PX))
        b.add_setter(card.get_parent(), "vscroll-policy", Gtk.ScrollablePolicy.NATURAL)
        for i in ("apply", "unapply"): b.connect(i, self.update_size)
        self.add_breakpoint(b)
        self.title = Gtk.Label(css_classes=["title"], use_markup=True)
        for i in ("name", "cards"): self.connect(f"notify::{i}", self.__class__.set_title)
        header.set_title_widget(self.title)
        self.card = card
        self.bind_property("side", bottom.get_title_widget(), "icon-name", DEFAULT_BIND, self.__class__.button_bind)
        Window.controller_init(self)
        self.actions = actions = Gio.SimpleActionGroup()
        self.insert_action_group(self.__gtype_name__.lower(), actions)
        actions.add_action(Gio.SimpleAction.new("context", get_variant("str").get_type()))
        actions.lookup_action("context").connect("activate", Context.activate, self)
        for i in ("edit-deck", "new-card", "pop-out"):
            actions.add_action(Gio.SimpleAction.new(i, None))
            actions.lookup_action(i).connect("activate", self.__class__.action, self)
        for i in ("show-hidden", "automatic-mode"):
            a = Gio.SimpleAction.new_stateful(i, None, get_variant(False))
            a.connect("notify::state", getattr(self.__class__, f"{i.replace('-', '_')}_changed"), self)
            actions.add_action(a)
        self.card.bind_property("visible", a, "enabled", DEFAULT_BIND)
        header.pack_end(Button(t=Gtk.MenuButton, icon_name="open-menu", tooltip_text="Menu", menu_model=Menu({
                "!a": Menu({"Undo": "app.undo"}),
                "!b": Menu({
                    "Autoplay": "app.autoplay",
                    "Automatic Mode": "review.automatic-mode",
                    "Show Hidden": "review.show-hidden",
                    "Create Window": "review.pop-out"
                    }),
                "!c": Menu({
                    "New Card": "review.new-card",
                    "Edit Deck": "review.edit-deck"
                })
            })))
        b = TimedButton(callback=self.do_automatic_action, tooltip_text="Reset Time")
        self.bind_property("automatic-mode-seconds", b, "seconds", DEFAULT_BIND)
        self.bind_property("automatic-mode", b, "visible", DEFAULT_BIND)
        header.pack_end(b)
        self.status = status
        self.deck = deck
        self.deck.bind_property("name", self, "name", DEFAULT_BIND)
        self.start()

    def update_size(self, b):
        if not self.get_current_breakpoint(): Task.invoke(self.add_css_class, "small")
        else: Task.invoke(self.remove_css_class, "small")

    @staticmethod
    def do_automatic_action(b):
        self = b.get_ancestor(Review)
        if not self: return b.stop()
        if not self.card.get_mapped():
            b.stop()
            return self.actions.lookup_action("automatic-mode").set_state(get_variant(False))
        if self.automatic_mode_action == "Toggle Side": Task.invoke(self.change_sides)
        else: Task.invoke(self.answer, self.automatic_mode_action)

    @staticmethod
    def card_map(c):
        self = c.get_ancestor(Review)
        if self:
            self.review_duration = GLib.DateTime.new_now_utc()

    @staticmethod
    def root_changed(self, p=None): self.pause()

    @staticmethod
    def set_title(self, p=None):
        l = GLib.markup_escape_text(self.deck.name)
        if self.cards:
            l += f" <span alpha='50%'>({len(self.cards)})</span>"
        self.title.set_label(l)

    @staticmethod
    def show_hidden_changed(a, c, self): self.start()

    @staticmethod
    def automatic_mode_changed(a, c, self):
        if a.get_state().unpack() and self.cards:
            g = Adw.PreferencesGroup()
            r = Adw.SpinRow.new_with_range(1, 1e3, 1)
            r.set_title("Seconds")
            self.bind_property("automatic-mode-seconds", r, "value", SYNC_BIND)
            g.add(r)
            r = Adw.ComboRow(title="Action", model=self.__class__.acts)
            self.bind_property("automatic-mode-action", r, "selected", SYNC_BIND, selected_item_bind, selected_item_bind)
            g.add(r)
            d = Prompt(callback=self.__class__.do_automatic_dialog, heading="Automatic Mode", extra_child=g, confirm_callback=False)
            d.review = self
            d.present(self.get_root())
        else:
            self.automatic_mode = False

    @staticmethod
    def do_automatic_dialog(d, r):
        self = d.review
        del d.review
        if r == "confirm":
            self.automatic_mode = True
        else:
            self.automatic_mode = False
            self.actions.lookup_action("automatic-mode").set_state(get_variant(False))

    @staticmethod
    def button_change_sides(b):
        self = b.get_ancestor(Review)
        if self: self.change_sides()

    def change_sides(self):
        self.side = "Front" if self.side == "Back" else "Back"
        self.populate()

    def populate(self):
        if not self.cards: return
        b = getattr(self, "binding", False)
        if b:
            if b.get_property("source") == self.cards[0] and b.get_property("source-property") == self.side.lower(): return
            b.unbind()
        self.binding = self.cards[0].bind_property(self.side.lower(), self.card, "child", DEFAULT_BIND, self.__class__.populate_card)

    @staticmethod
    def button_answer(b):
        self = b.get_ancestor(Review)
        if self: self.answer(b.get_tooltip_text())

    def answer(self, answer):
        if self.cards:
            self.pause()
            c = list(self.cards)
            c[0].answer(answer, review_duration=self.review_duration)
            c.remove(c[0])
            self.cards = c
            if self.cards:
                self.side = "Front"
                self.populate()
            else: Task.invoke(self.start)
        else: Task.invoke(self.start)

    @staticmethod
    def button_bind(b, v): return "go-down-symbolic" if v == "Front" else "go-up-symbolic"
    @staticmethod
    def button_bind_tooltip(b, v): return "Front" if v and "up" in v else "Back"

    @staticmethod
    def on_click(e, n, x, y, signal="none"):
        self = e.get_widget()
        b = e.get_current_button()
        if self.context and self.context.get_mapped(): return self.context.popdown()
        if signal != "released": return
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if w and b == 1 and w.get_ancestor(Adw.Bin) == self.card: self.card.grab_focus()
        if self.cards and b == 3:
            self.context = self.context or Context()
            self.context.widget = self.cards[0]
            menu = {"Edit": "review.context::Edit"}
            self.context.set_menu_model(Menu(menu))
            Window.show_popover_at_widget(self, self.context, self, x, y)

    def start(self):
        self.pause()
        self.changing = True
        self.cards = []
        self.side = "Front"
        cards = self.deck.get_cards()
        if not cards:
            b = getattr(self, "binding", False)
            if b: b.unbind()
            self.status.set_properties(title="No Cards", description="Add a card to review", icon_name="document-new-symbolic", visible=True)
            return
        reviews = self.deck.get_reviews()
        to_review = self.deck.get_review_cards(cards=cards, reviews=reviews, show_hidden=self.actions.lookup_action("show-hidden").get_state().unpack())
        if to_review:
            self.status.set_visible(False)
            self.cards = to_review
            Task.invoke(self.populate)
            self.changing = False
        else:
            b = getattr(self, "binding", False)
            if b: b.unbind()
            n = False
            for i in cards:
                if not i.due or not i.last_review: continue
                t = i.due.to_unix()
                if n == False or n > t:
                    n = t
            if reviews:
                n = min(n, self.deck.get_new_cards_today(reviews=reviews, timestamp=True) + (GLib.TIME_SPAN_DAY / 1e6))
            now = GLib.DateTime.new_now_utc().to_unix()
            d = ""
            if n > now:
                d = format_duration(n - now)
            self.status.set_properties(title="Good Job", description=f"{d} until more cards" if d else "", icon_name="brain-augmented-symbolic", visible=True)

    @staticmethod
    def action(a, s, self):
        if a.get_name() == "edit-deck": DeckEditor(self.deck)
        if a.get_name() == "new-card": CardEditor(deck=self.deck)
        if a.get_name() == "pop-out":
            self.actions.lookup_action("pop-out").set_enabled(False)
            view = self.get_ancestor(Adw.NavigationView)
            view.get_visible_page().set_child(None)
            view.pop()
            w = Adw.ApplicationWindow(application=App.default, title="Review", content=Adw.ToastOverlay(child=self))
            self.bind_property("name", w, "title", DEFAULT_BIND)
            w.present()

    @staticmethod
    def edit_deck(a, p, w):
        if isinstance(w, Window): DeckEditor(w.view.get_visible_page().get_child().deck)
        else: DeckEditor(w.get_content().get_child().deck)

    @staticmethod
    def get_all():
        l = []
        if Window.current:
            for i in Window.current.view:
                if isinstance(i, Adw.NavigationPage) and isinstance(i.get_child(), Review):
                    if not i.get_child() in l: l.append(i.get_child())
        for i in Gtk.Window.list_toplevels():
            if isinstance(i, Adw.ApplicationWindow) and not isinstance(i, Window) and isinstance(i.get_content().get_child(), Review):
                if not i.get_content().get_child() in l: l.append(i.get_content().get_child())
        return l

    @staticmethod
    def on_key_pressed(e, v, kc, s):
        self = e.get_widget()
        if not self: return
        f = self.get_root().get_focus()
        if Gdk.ModifierType.CONTROL_MASK in s and Gdk.ModifierType.SHIFT_MASK in s and v == Gdk.KEY_a: self.actions.lookup_action("autoplay").activate()
        elif Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_a: self.actions.lookup_action("automatic-mode").activate()
        if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_h: self.actions.lookup_action("show-hidden").activate()
        if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_n: self.actions.lookup_action("new-card").activate()
        if self.card.get_mapped():
            if v == Gdk.KEY_1:
                Task.invoke(self.answer, "Good")
                return True
            if v == Gdk.KEY_2:
                Task.invoke(self.answer, "Hard")
                return True
            if v == Gdk.KEY_3:
                Task.invoke(self.answer, "Easy")
                return True
            if v == Gdk.KEY_4:
                Task.invoke(self.answer, "Again")
                return True
            if Gdk.ModifierType.CONTROL_MASK in s and v in (Gdk.KEY_e, Gdk.KEY_E):
                if Gdk.ModifierType.SHIFT_MASK in s: self.actions.lookup_action("edit-deck").activate()
                else: Context.action(self, self.cards[0], action="Edit")
                return True
            if not isinstance(f, Gtk.Button):
                if v == Gdk.KEY_space:
                    self.change_sides()
                    return True
            return
        if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_e: self.actions.lookup_action("edit-deck").activate()

    pattern = Regex(r"(?<!\\)\[([^\s\]\\]+://[^\s\]\\]+)(?:\s+((?:\\.|[^\]\\])*))?(?<!\\)\]")

    uri_pattern = Regex(r"(?<!\\)([^\s\\]+://[^\s\\]+)(?:\s+((?:\\.|[^\\\s])*))?")

    @staticmethod
    def play(media=None):
        if hasattr(media, "media_stream"):
            media = media.media_stream
        if hasattr(media, "media_streams") and media.get_ancestor(Review) and all(isinstance(i, MediaStream) for i in media.media_streams):
            media.get_ancestor(Review).pause()
            Review.play_next(media.get_ancestor(Review), medias=list(media.media_streams))
            return
        if isinstance(media, MediaStream) and media.duration:
            if media.position != 0: media.position = 0
            media.volume = 1
            media.play()

    def autoplay(self):
        autoplay = App.default.lookup_action("autoplay").get_state().unpack()
        if not autoplay: return
        box = self.card.get_child()
        if not box: return
        medias = []
        for i in self.card.get_child():
            if hasattr(i, "media_stream"): medias.append(i.media_stream)
            if hasattr(i, "media_streams"): medias.extend(i.media_streams)
        if not medias: return
        self.pause()
        if self.card.get_mapped() and all(isinstance(i, MediaStream) for i in medias):
            medias = [i for i in medias if i.duration]
            if len(medias) == 1: Review.play(medias[0])
            elif medias: self.play_next(medias)

    def check_eos(self, e, media, d, p):
        if e.is_set() or not hasattr(self, "sig"): return
        if media.playing and media.position == p and media._duration == d:
            if media in self.sig[-1]: self.sig[-1].remove(media)
            GLib.timeout_add(500, self.play_next)

    def play_next(self, medias=False):
        if medias == False:
            if not hasattr(self, "sig"): return
            medias = self.sig[-1]
            self.clear_sig()
        else:
            medias = [i for i in medias if i.duration]
        if not medias: return
        medias[0].pause()
        self.sig = [medias[0], medias[0].connect("notify::position", self.__class__.start_autoplay, self), medias]
        Review.play(medias[0])

    @staticmethod
    def start_autoplay(media, p=None, self=None):
        if 0.1 > (media.duration - media.position) / 1e9:
            previous = getattr(self, "event", False)
            if previous: previous.set()
            self.event = Event()
            GLib.timeout_add(200, self.check_eos, self.event, media, media.duration, media.position)

    def clear_sig(self):
        if not hasattr(self, "sig"): return
        self.sig[0].disconnect(self.sig[1])
        del self.sig

    def get_medias(self):
        l = []
        if self.card.get_child():
            for i in self.card.get_child():
                if not (hasattr(i, "media_stream") or hasattr(i, "media_streams")): continue
                ll = [i.media_stream] if hasattr(i, "media_stream") else i.media_streams
                for it in ll:
                    if isinstance(it, MediaStream) and not i in l: l.append(it)
        return l

    def pause(self):
        self.clear_sig()
        if self.card.get_child():
            for i in self.get_medias(): i.pause()

    @staticmethod
    def populate_card(b, v):
        if not v: return
        self = b.get_property("target").get_ancestor(Review)
        if not self: return
        self.pause()
        for i in self.get_medias():
            if not i.uri in App.default.medias: MediaStream.destroy_pipeline(i)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, valign=Gtk.Align.CENTER)
        Task.invoke(self.do_box, box, v)
        return box

    def chunks(s, pattern=None):
        if not pattern:
            pattern = Review.pattern
        pos = 0
        for match in pattern.finditer(s):
            if match.start() > pos: yield None, s[pos:match.start()]
            yield match.group(1), match.group(2)
            pos = match.end()
        if pos < len(s): yield None, s[pos:]

    def do_box(self, box=None, v=""):
        if box != self.card.get_child(): return
        if not box or not v: return
        self.review_duration = GLib.DateTime.new_now_utc()
        for uri, text in self.__class__.chunks(v):
            if text and text.strip():
                text = text.strip()
                if uri:
                    while "://" in text:
                        te = ""
                        if not isinstance(uri, list):
                            uri = [uri]
                        for u, t in self.__class__.chunks(text, pattern=self.__class__.uri_pattern):
                            if u and not u in uri: uri.append(u)
                            te += t
                        text = te.strip()
                text = Gtk.Label(selectable=True, wrap=True, label=text, justify=Gtk.Justification.CENTER, use_markup=True)
            else:
                text = False
            if uri:
                if not isinstance(uri, list):
                    uri = [uri]
                uri = [uri_relative_encode(i) for i in uri]
                if len(uri) == 1:
                    uri = uri[0]
            if uri and text:
                w = Button(halign=Gtk.Align.CENTER, callback=Review.play, child=text)
                w.add_css_class("flat")
                if isinstance(uri, list):
                    w.uris = uri
                    w.media_streams = [PaintableTask(func=finish_func, uri=i, media_stream_args={"loop": False, "playing": False, "volume": 1}) for i in w.uris]
                else:
                    w.uri = uri
                    w.media_stream = PaintableTask(func=finish_func, uri=w.uri, media_stream_args={"loop": False, "playing": False, "volume": 1})
                Task.invoke(box.append, w)
            elif uri:
                if not isinstance(uri, list):
                    uri = [uri]
                if len(uri) == 1:
                    b = box
                else:
                    b = Gtk.Box()
                    Task.invoke(box.append, b)
                for i in uri:
                    w = Media(zoom=False, vexpand=True, hexpand=True, overflow=Gtk.Overflow.HIDDEN)
                    w.picture.set_halign(Gtk.Align.CENTER)
                    w.uri = i
                    Task.invoke(b.append, w)
                    c = get_cover(i)
                    w.media_stream = Adw.SpinnerPaintable.new(w) if isinstance(c, PaintableTask) else c
            elif text: Task.invoke(box.append, text)
        Task.invoke(box.grab_focus)
        Task.invoke(self.autoplay)

class Stats(Adw.BreakpointBin):
    __gtype_name__ = "Stats"
    deck = GObject.Property(type=Deck)
    box = GObject.Property(type=Gtk.Box)
    def __init__(self, deck):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        toolbar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box))
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        super().__init__(css_name="stats", child=toolbar, height_request=1, width_request=1)
        b = Adw.Breakpoint.new(Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 1000, Adw.LengthUnit.PX))
        for i in ("apply", "unapply"): b.connect(i, self.update_size)
        self.add_breakpoint(b)
        self.box = box
        self.deck = deck
        self.deck.bind_property("name", self, "name", DEFAULT_BIND)
        self.start()

    def update_size(self, b):
        for i in self.box:
            if isinstance(i, Gtk.Box) and isinstance(i.get_last_child(), BarChart): i.get_last_child().set_property("height-request", 120 if not self.get_current_breakpoint() else 300)
        if not self.get_current_breakpoint(): Task.invoke(self.add_css_class, "small")
        else: Task.invoke(self.remove_css_class, "small")

    def start(self):
        while self.box.get_first_child(): self.box.get_first_child().unparent()
        cards = self.deck.get_cards()
        show_hidden = App.default.lookup_action("show-hidden").get_state().unpack()
        if not show_hidden:
            cards = [i for i in cards if not i.hidden]
        if not cards:
            self.box.append(Adw.StatusPage(valign=Gtk.Align.CENTER, vexpand=True, title="No Cards", icon_name="document-new-symbolic"))
            return
        reviews = self.deck.get_reviews()
        hour = calendar = all_cards = future = None
        now = GLib.DateTime.new_now_local()
        now_t = now.to_unix()
        _years = {}
        _hour = dict((f"{n:02d}:00", 0) for n in range(24))
        for i in reviews:
            d = GLib.DateTime.new_from_unix_utc(i["Review Time"]).to_local()
            _hour[f"{d.get_hour():02d}:00"] += 1
            y = str(d.get_year())
            if not y in _years:
                _years[y] = {}
                da = GLib.DateTime.new(GLib.TimeZone.new_utc(), d.get_year(), 1, 1, 0, 0, 0)
                while da.get_year() == d.get_year():
                    _years[y].setdefault(da.format("%A %B %d"), 0)
                    da = da.add_days(1)
            k = d.format("%A %B %d")
            _years[y].setdefault(k, 0)
            _years[y][k] += 1
        _future = {}
        for n in range(30):
            d = now.add_days(n) if n else now
            k = "Today" if n == 0 else "Tomorrow" if n == 1 else d.format("%A %B %d")
            _future[k] = 0
        d = {"New": 0, "Learning": 0, "Relearning": 0, "Learned": 0}
        for card in cards:
            if not card.due or not card.last_review:
                d["New"] += 1
                continue
            _due = card.due.to_local()
            due = _due.to_unix()
            if (GLib.TIME_SPAN_DAY * 30) / 1e6 > abs(due - now_t):
                if now_t > due or _due.get_year() == now.get_year() and _due.get_day_of_year() == now.get_day_of_year():
                     _future["Today"] += 1
                else:
                    k = _due.format("%A %B %d")
                    _future[k if k in _future else "Tomorrow"] += 1
            if card.state == "Relearning":
                v["Relearning"] += 1
            elif abs(due - card.last_review.to_local().to_unix()) > (GLib.TIME_SPAN_DAY * 40) / 1e6:
                d["Learned"] += 1
            else:
                d["Learning"] += 1
        total = len(cards)
        raw = {k: (v / total) * 100 for k, v in d.items()}
        d = {k: int(v) for k, v in raw.items()}
        remainder = 100 - sum(d.values())
        fractions = []
        for k, v in raw.items():
            fractions.append((v - int(v), k))
        fractions.sort(reverse=True)
        for _, k in fractions[:remainder]:
            d[k] += 1
        if _future and not sum(_future.values()) == 0:
            future = [Gtk.Label(label="Future", css_classes=["title-2"]), BarChart([{"title": f"{k} - {v} cards", "value": v} for k, v in _future.items()])]
        for year, values in _years.items():
            if not isinstance(calendar, list):
                calendar = []
            calendar.append(Gtk.Label(label=year, css_classes=["title-2"], tooltip_text=f"{sum(i for i in values.values())} reviews"))
            calendar.append(CalendarHeatmap([{"title": k + f" - {v} reviews", "value": v} for k, v in values.items()], year=int(year)))
        if _hour and not sum(_hour.values()) == 0:
            hour = [Gtk.Label(label="Hourly", css_classes=["title-2"]), BarChart([{"title": f"{k} - {v} reviews", "value": v} for k, v in _hour.items()])]
        d = DonutChart(d, colors=[RGBA(i) for i in ("#62a0ea", "#57e389", "#ffa348", "#26a269")])
        l = Gtk.Box(css_name="legend", halign=Gtk.Align.END, valign=Gtk.Align.END, orientation=Gtk.Orientation.VERTICAL)
        l.append(Gtk.Label(halign=Gtk.Align.START, label="1 card" if len(cards) == 1 else f"{len(cards)} cards"))
        l.append(DonutChartLegend(d))
        o = Gtk.Overlay(halign=Gtk.Align.CENTER, child=d)
        o.add_overlay(l)
        all_cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i in (Gtk.Label(label="Cards", css_classes=["title-2"]), o): all_cards.append(i)
        for i in (future, calendar, all_cards, hour):
            if isinstance(i, list):
                box = Gtk.Box(css_name="item", orientation=Gtk.Orientation.VERTICAL)
                for it in i: box.append(it)
                self.box.append(box)
            elif i: self.box.append(i)

class Window(Window):
    __gtype_name__ = "Window"
    context = GObject.Property(type=Context)
    drop_target_gtypes = (DeckWidget,)
    def __init__(self):
        super().__init__()
        self.breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 400, Adw.LengthUnit.PX))
        self.add_breakpoint(self.breakpoint)
        for i in ("Default Width", "Default Height", "Maximized"): App.default.lookup_action(i.lower().replace(" ", "-")).bind_property("state", self, i.lower().replace(" ", "-"), SYNC_BIND, unpack_bind, variant_bind)
        self.add_actions(("review-edit-deck", Review.edit_deck))
        self.add_action(Gio.SimpleAction.new("context", get_variant("str").get_type()))
        self.lookup_action("context").connect("activate", Context.activate, self)
        self.view = Adw.NavigationView()
        self.catalog = Adw.WrapBox(child_spacing=10, line_spacing=10)
        scrolled = Gtk.ScrolledWindow(child=self.catalog)
        scrolled.connect("edge-reached", self.__class__.load_more)
        toolbar = Adw.ToolbarView(content=Gtk.Overlay(child=scrolled))
        self.status = Adw.StatusPage(visible=False)
        toolbar.get_content().add_overlay(self.status)
        header = Adw.HeaderBar()
        m = Menu({
            "New Deck": "new-deck",
            "Import Data": "import-data",
        })
        header.pack_start(Button(t=Gtk.MenuButton, menu_model=m, icon_name="list-add", tooltip_text="Add"))
        m = Menu({
            "Show Hidden": "show-hidden",
            "Undo Last Review": "undo",
            "!a": App.default.default_menu
        })
        header.pack_end(Button(t=Gtk.MenuButton, menu_model=m, icon_name="open-menu", tooltip_text="Menu"))
        toolbar.add_top_bar(header)
        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.search.connect("activate", self.__class__.do_search)
        search = Gtk.Revealer(css_name="find", transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=300, halign=Gtk.Align.CENTER, child=self.search, valign=Gtk.Align.START)
        header.pack_end(Button(t=Gtk.ToggleButton, icon_name="edit-find", tooltip_text="Search", bindings=[(None, "active", search, "reveal-child", SYNC_BIND)]))
        toolbar.get_content().add_overlay(search)
        self.decks_page = Adw.NavigationPage.new(toolbar, "Decks")
        self.view.add(self.decks_page)
        self.set_content(self.view)
        self.do_search()
        self.present()

    def do_search(*_):
        self = Window.current
        self._catalog = []
        t = self.search.get_text().lower()
        for i in sorted(App.default.decks, key=id_sort):
            if not App.default.lookup_action("show-hidden").get_state().unpack() and i.hidden or t and not t in i.name.lower(): continue
            self._catalog.append(i)
        while self.catalog.get_first_child(): self.catalog.remove(self.catalog.get_first_child())
        self.load_more()
        self.do_status()

    def do_status(self):
        if not self.catalog.get_first_child(): self.status.set_properties(title="No Decks", icon_name="folder-open-symbolic", visible=True)
        elif self.search.get_text() and not any(i.get_visible() for i in self.catalog): self.status.set_properties(title="No Results", icon_name="edit-find-symbolic", visible=True)
        else: self.status.set_visible(False)

    @staticmethod
    def on_click(*args):
        self, e, n, x, y, signal, b, state = Window.click_to_args(*args)
        if self.context and self.context.get_mapped(): return self.context.popdown()
        if signal != "released": return
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if not w: return
        deck = w.get_ancestor(DeckWidget)
        if deck and b == 3:
            self.context = self.context or Context()
            self.context.widget = deck
            menu = {"Edit": "win.context::Edit", "Stats": "win.context::Stats", "Export": "win.context::Export", "Export with Review Data": "win.context::Export with Data", "Import Cards": "win.context::Import Cards"}
            self.context.set_menu_model(Menu(menu))
            self.show_popover_at_widget(self.context, self.get_content(), x, y)

    @staticmethod
    def on_key_released(e, v, kc, s):
        self = e.get_widget()
        if not self: return
        if self.catalog.get_mapped():
            if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_e:
                if isinstance(self.get_focus(), DeckWidget): Context.action(self, self.get_focus(), "Edit")
            if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_n: App.default.lookup_action("new-deck").activate()
            if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_h: App.default.lookup_action("show-hidden").activate()
            if v == Gdk.KEY_Escape and self.search.get_mapped():
                self.search.get_ancestor(Gtk.Revealer).set_reveal_child(False)
                return True
            if Gdk.ModifierType.CONTROL_MASK in s and v == Gdk.KEY_f:
                self.search.get_ancestor(Gtk.Revealer).set_reveal_child(not self.search.get_ancestor(Gtk.Revealer).get_reveal_child())
                if self.search.get_ancestor(Gtk.Revealer).get_reveal_child(): self.search.grab_focus()
                return True

    @staticmethod
    def load_more(w=None, p=3):
        if p != 3: return
        self = Window.current
        l = getattr(self, "_catalog", [])
        n = 0
        for i in [i for i in l]:
            n += 1
            l.remove(i)
            self.catalog.append(DeckWidget(i))
            if n == 28: break
        self._catalog = l

    def clear_drop(self):
        if getattr(self, "highlight", False): self.highlight.remove_css_class("highlight")
        self.highlight = None

    @staticmethod
    def on_drag_source_prepare(e, x, y):
        self = e.get_widget().get_root()
        self.clear_drop()
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        e.f = None
        c = None
        if w and w.get_ancestor(DeckWidget):
            w = w.get_ancestor(DeckWidget)
            e.f = w
            c = Gdk.ContentProvider.new_for_value(e.f)
        e.set_actions(Gdk.DragAction.MOVE if c else Gdk.DragAction.NONE)
        return c

    @staticmethod
    def on_drag_source_drag_begin(e, d):
        self = e.get_widget().get_root()
        if e.f: Gtk.DragIcon.get_for_drag(d).set_child(Gtk.Picture(paintable=Gtk.WidgetPaintable.new(e.f)))

    @staticmethod
    def on_drag_source_drag_end(e, d, de):
        e.w = None
        e.f = []
        e.set_content(None)
        e.set_actions(Gdk.DragAction.NONE)
        e.get_widget().get_root().clear_drop()

    @staticmethod
    def on_drop_target_drop(e, v, x, y):
        self = e.get_widget().get_root()
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        b = None
        if w:
            w = w.get_ancestor(DeckWidget)
        if not w: return
        elif isinstance(v, DeckWidget):
            l = getattr(self.get_application(), "decks", None)
            if not l: return
            ll = sorted(l, key=id_sort)
            start = ll.index(w.deck)
            ll.remove(v.deck)
            ll.insert(start, v.deck)
            for n, i in enumerate(ll):
                if i.id != n + 1:
                    i.id = n + 1
            self.do_search()
        self.clear_drop()
        return True

    @staticmethod
    def on_drop_target_motion(e, x, y):
        self = e.get_widget().get_root()
        self.clear_drop()
        a = Gdk.DragAction.MOVE
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        v = e.get_value()
        if w:
            w = w.get_ancestor(DeckWidget) or w
        if isinstance(v, DeckWidget):
            if not v == w and isinstance(w, DeckWidget):
                self.highlight = w
            if not self.highlight: return Gdk.DragAction.NONE
        if self.highlight:  self.highlight.add_css_class("highlight")
        return a

def activate(a):
    if a.get_active_window(): return a.get_active_window().present()
    Window()

def new_deck(*_): DeckEditor()

def do_import_data(d, r):
    try:
        files = d.open_multiple_finish(r)
    except: return
    for file in files:
        try:
            data = Data(file.get_path(), readonly=True)
            cards = data.read(table="Cards", return_dict=True, force_tuple=True)
            reviews = data.read(table="Reviews", return_dict=True, force_tuple=True)
            decks = sorted(data.read(table="Decks", return_dict=True, force_tuple=True), key=id_sort)
        except Exception as e:
            Toast(f"Could not import {basename(file)} {e}")
            continue
        for i in decks:
            del i["ID"]
            n, nn = i["Name"], 0
            while App.default.decks[i["Name"]]:
                nn += 1
                i["Name"] = f"{n} {nn}"
                for l in (cards, reviews):
                    for it in l:
                        if it["Deck"] == n:
                            it["Deck"] = i["Name"]
            i["ID"] = max((it.id for it in App.default.decks), default=0) + 1
            App.default.data.new(table="Decks", names=tuple(i.keys()), values=tuple(i.values()))
            App.default.decks.add(i)
        for i in cards: App.default.data.new(table="Cards", names=tuple(i.keys()), values=tuple(i.values()))
        for i in reviews: App.default.data.new(table="Reviews", names=tuple(i.keys()), values=tuple(i.values()))

def import_data(*_): Gtk.FileDialog().open_multiple(Window.current, None, do_import_data)

def id_sort(i): return i.id if hasattr(i, "id") else i.get("ID", 1)

def due_sort(i): return datetime_sort(i.due if hasattr(i, "due") else i.get("Due", 1))

def undo(*_):
    if App.default.undo:
        c = App.default.undo[0]
        App.default.undo.remove(c)
        card, old, log = c
        del log["Review Duration"]
        App.default.data.delete(table="Reviews", conditions=" AND ".join(f'"{i}" = ?' if " " in i else f"{i} = ?" for i in log.keys()), conds=tuple(log.values()))
        card.from_card(old)
        r = card.deck.get_review()
        if r: Task.invoke(r.start)
        else: Toast(f"{card.deck.name} Card {card.id} review undone")
    App.default.lookup_action("undo").set_enabled(bool(App.default.undo))

def window_removed(a, w):
    if not isinstance(w, Window) and not Window.current: Window()

def get_cover(uri):
    if not uri: return None
    if uri in App.default.medias:
        r = App.default.medias[uri]
        return None if isinstance(r, PaintableTask) else r
    App.default.medias[uri] = PaintableTask(func=finish_func, uri=uri, media_stream_args={"loop": False, "playing": False, "volume": 1})
    return App.default.medias[uri]

def finish_func(p):
    if p.uri in App.default.medias:
        App.default.medias[p.uri] = p
        for i in App.default.decks:
            if i.cover == p.uri: Task.invoke(i.set_property, "cover-paintable", p)
    autoplay = App.default.lookup_action("autoplay").get_state().unpack()
    for r in Review.get_all():
        if not r.card.get_first_child(): return
        for i in r.card.get_first_child():
            if hasattr(i, "uri") and i.uri == p.uri:
                Task.invoke(setattr, i, "media_stream", p)
                if p.duration and autoplay: Task.invoke(r.autoplay)
            if hasattr(i, "media_streams"):
                for n, it in enumerate(i.media_streams):
                    if it.uri == p.uri:
                        i.media_streams[n] = p
                        if p.duration and autoplay: Task.invoke(r.autoplay)

DATA_INIT = f"""
CREATE TABLE IF NOT EXISTS Decks (ID INTEGER, Name TEXT NOT NULL PRIMARY KEY, Cover TEXT, Hidden INTEGER NOT NULL DEFAULT 0, 'New Cards/Day' INTEGER NOT NULL DEFAULT 20, 'New Cards Review Order' TEXT NOT NULL DEFAULT 'After Due', 'Desired Retention' REAL NOT NULL DEFAULT 0.9, 'Learning Steps' TEXT DEFAULT '60 600', 'Relearning Steps' TEXT DEFAULT '600', 'Maximum Interval' INTEGER NOT NULL DEFAULT 36500, 'Enable Fuzzing' INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS Cards (Deck TEXT NOT NULL, ID INTEGER NOT NULL, Front TEXT, Back TEXT, State TEXT NOT NULL DEFAULT 'Learning', Step INTEGER, Stability REAL, Difficulty REAL, Due INTEGER, 'Last Review' INTEGER, Hidden INTEGER NOT NULL DEFAULT 0, UNIQUE ('Deck', 'ID'), FOREIGN KEY (Deck) REFERENCES Decks(Name) ON DELETE CASCADE ON UPDATE CASCADE);
CREATE TABLE IF NOT EXISTS Reviews (Deck TEXT NOT NULL, ID INTEGER NOT NULL, Rating TEXT NOT NULL DEFAULT 'Good', 'Review Time' INTEGER NOT NULL DEFAULT (unixepoch()), 'Review Duration' INTEGER, FOREIGN KEY (Deck, ID) REFERENCES Cards(Deck, ID) ON DELETE CASCADE ON UPDATE CASCADE);
"""

INFO = {
    "ID": "io.github.kirukomaru11.Memorize",
    "Developer": "きるこ丸11号",
    "Comment": "Memorize things",
    "Issue URL": "https://github.com/kirukomaru11/Memorize/issues",
    "Website": "https://github.com/kirukomaru11/Memorize",
    "Keywords": ["Flashcards", "Study", "Learn"],
    "Categories": ["Education"],
    "Branding": ["#d5ddea", "#f7f9fb"],
    "MimeTypes": [],
}

APP_CSS = """
card { font-size: 50px; margin: 0px 6px; }
.small card { font-size: 38px; }
card button { font-weight: normal; }
card media picture { margin: 8px 0px; }
card media:first-child picture { margin: 0px 0px 8px 0px; }
card media:last-child picture { margin: 8px 0px 0px 0px; }
card media:only-child picture { margin: 0px; }
card media graphicsoffload > mediacontrols { margin: 0px; }
navigation-view-page > toolbarview > overlay > scrolledwindow > viewport > wrap-box { margin: 10px; }
searchbar search { border-spacing: 6px; padding: 0px 18px; }
find entry.search { border-radius: 9px; backdrop-filter: blur(20px) brightness(0.9); }
:drop(active) .highlight { box-shadow: 0 0 0 2px var(--accent-bg-color); }
widget.cardslist:drop(active) { border: none; outline: none; box-shadow: none; }
.easy { color: white; background-color: var(--blue-4); }
.good { color: white; background-color: var(--green-5); }
.hard { color: white; background-color: var(--orange-3); }
.again { color: white; background-color: var(--red-3); }
stats toolbarview > scrolledwindow > viewport > box { border-spacing: 40px; padding-bottom: 10px; }
stats viewport > box > item { border-spacing: 10px; }
stats overlay > legend { backdrop-filter: blur(20px); border-radius: 10px; }
stats overlay > legend > label:first-child { font-weight: bold; margin-left: 6px; margin-top: 5px; }
stats.small calendar-heatmap { border-spacing: 2px; }
stats.small calendar-heatmap .day,
stats.small calendar-heatmap .pad,
stats.small calendar-heatmap .empty { min-height: 8px; min-width: 8px; border-radius: 2px; }
stats donut-chart { min-width: 460px; min-height: 460px; padding: 0px 150px; }
stats.small donut-chart { min-width: 200px; min-height: 200px; }
stats item:first-child bar-chart { border-spacing: 2px; }
stats.small item:first-child bar-chart bar { min-width: 16px; }
stats item:last-child bar-chart { border-spacing: 0px; }
stats.small item:last-child bar-chart bar { min-width: 20px; }
stats bar-chart bar { border-radius: 4px 4px 0px 0px; }
button.circular.deck > widget { border-radius: 100px; }
"""

def datetime_adapter(d, b=None):
    if isinstance(b, GLib.DateTime):
        d = b
    return d.to_utc().to_unix() if isinstance(d, GLib.DateTime) else None
register_adapter(GLib.DateTime, datetime_adapter)
register_adapter(Deck, Deck.adapter)

def main():
    App(info=INFO,
        shortcuts={
            "Card List": {
                "Find": "<primary>f",
                "New Card": "<primary>n",
                "Edit Selected Cards": "<primary>e",
                "Stop Search": "Escape"
            },
            "Card Editor": {
                "Find and Replace": "<primary>f",
                "Stop Search": "Escape"
            },
            "Review": {
                "Answer Good": "1",
                "Answer Bad": "2",
                "Answer Easy": "3",
                "Answer Again": "4",
                "Toggle Card Side": "space",
                "Automatic Mode": "<primary>a",
                "Autoplay": "<primary><shift>a",
                "Show Hidden": "<primary>h",
                "Undo": "<primary>z",
                "New Card": "<primary>n",
                "Edit Card/Deck": "<primary>e",
                "Edit Deck": "<primary><shift>e"
            },
            "Window": {
                "New Deck": "<primary>n",
                "Edit Selected Deck": "<primary>e",
                "Show Hidden": "<primary>h",
                "Search": "<primary>f",
                "Stop Search": "Escape",
                "Undo": "<primary>z"
            }
        },
        activate=activate,
        data=DATA_INIT + "CREATE TABLE IF NOT EXISTS Settings (Key TEXT PRIMARY KEY NOT NULL, Value TEXT);",
        window_removed=window_removed,
        actions=[
            ("new-deck", new_deck),
            ("import-data", import_data),
            ("undo", undo)
        ])
    for i, v in {"Default Width": 360, "Default Height": 600, "Maximized": False, "Autoplay": False, "Show Hidden": False, "Regex": False, "Whole Words": False, "Case Sensitive": True, "Front": True, "Back": True}.items(): App.default.data.persist_action(App.default, i, (i, v), "Settings", "Value", "Key = ?", (i,))
    App.default.set_accels_for_action("app.undo", ["<primary>z"])
    App.default.lookup_action("show-hidden").connect("notify::state", Window.do_search)
    App.default.lookup_action("undo").set_enabled(False)
    App.default.undo, App.default.medias = [], {}
    App.default.decks = List(Deck, unique="name", list=App.default.data.read(table="Decks", return_dict=True, force_tuple=True), static=True)
    App.default.cards = List(Card)
    App.default.main(css=APP_CSS)

if __name__ == "__main__": main()