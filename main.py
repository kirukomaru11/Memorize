#!/usr/bin/python3
from re import compile as regex
from re import IGNORECASE, NOFLAG
from csv import reader as csv
from json import loads as json
from json import dumps as json_string

from AppUtils import *

css = """
flowbox { padding: 4px; }
flowbox flowboxchild { border-radius: 100px; }

.preferences preferencesgroup:first-child .boxed-list { background: none; box-shadow: none; }

card viewport > box > controls,
card viewport > box > media { margin: 6px; }
card viewport > box > label { font-size: 30px; margin: 6px; }

edit searchbar box > box { border-spacing: 6px; }
edit textview { padding: 10px; font-size: 20px; }
edit toolbarview > box { padding: 10px; border-spacing: 10px; }
cards row { padding: 0px; margin-bottom: 6px; }
cards row:selected label { font-weight: bold; }
cards row > box { border-spacing: 10px; border-radius: 8px; }
cards row > box > box > label:nth-child(2) { font-size: 12px; }
cards row > box > label { padding: 12px 4px 12px 16px; }
cards row > box > box > label { padding-right: 14px; }
.hidden { color: var(--card-bg-color); background-color: var(--card-fg-color); }
"""

decks_sort = ("Alphabetical Ascending", "Alphabetical Descending", "Reviewed Ascending", "Reviewed Descending")
cards_sort = ("Deck Ascending", "Deck Descending", "Date Ascending", "Date Descending")
review_sort = ("Random", "Ease Ascending", "Ease Descending")

app = App(shortcuts={"General": (("Keyboard Shortcuts", "app.shortcuts"), ("Search", "app.search"))},
          application_id="io.github.kirukomaru11.Memorize",
          style=css,
          data={
            "Window": { "default-height": 600, "default-width": 600, "maximized": False, "hide-on-close": False, },
            "Sort": { "decks-sort": decks_sort[3], "cards-sort": cards_sort[0], "review-sort": review_sort[1], },
            "Edit": { "show-hidden": True, "front": False, "back": False, "regex": False, "case-sensitive": False, },
            "Review": { "autoplay": False, },
            "Decks": {}
          })
app.to_play, app.timer_id, app.modifying = [], 0, False

_breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 777, Adw.LengthUnit.PX))
app.window.add_breakpoint(_breakpoint)

flowbox = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START, min_children_per_line=2, max_children_per_line=8, row_spacing=16)
_breakpoint.add_setter(flowbox, "row-spacing", 6)
def flow_sort(*args):
    if "Alphabetical" in app.lookup_action("decks-sort").get_state().unpack():
        a, e = tuple(alphabetical_sort(i.get_child().get_text()) for i in args)
    else:
        a, e = tuple(app.data["Decks"][i.get_child().get_text()]["Reviews"][-1][0] for i in args)
    if "Ascending" in app.lookup_action("decks-sort").get_state().unpack(): return (a > e) - (a < e)
    else: return (a < e) - (a > e)
    return 0
flowbox.set_sort_func(flow_sort)
def child_activated(f, c, p=True):
    app.deck = c
    review_page.set_title(c.get_child().get_tooltip_text())
    if p: view.push(review_page)
flowbox.connect("child-activated", child_activated)
def add_deck(i):
    a = Adw.Avatar(show_initials=True, size=200, text=i)
    get_paintable(i, a)
    _breakpoint.add_setter(a, "size", 168)
    a.bind_property("text", a, "tooltip-text", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
    flowbox.append(a)
    a.get_parent().set_halign(Gtk.Align.CENTER)
    do_search()
    event = Gtk.GestureLongPress()
    event.connect("pressed", lambda e, *_: (child_activated(flowbox, a.get_parent(), False), deck_edit()))
    a.add_controller(event)
    return False
overlay = Gtk.Overlay(child=Gtk.ScrolledWindow(child=flowbox))
overlay.add_overlay(Adw.StatusPage())
main_page = Adw.NavigationPage(title=app.about.get_application_name(), child=Adw.ToolbarView(content=overlay))
header = Adw.HeaderBar()
search = Gtk.SearchEntry(placeholder_text="Search", hexpand=True)
search.connect("stop-search", lambda *_: search_bar.set_search_mode(False))
def do_search(*_):
    overlay.get_last_child().set_visible(False)
    for i in flowbox: i.set_visible(search.get_text().lower() in f"{i.get_child().get_text()} {app.data['Decks'][i.get_child().get_text()]['Tags']}".lower())
    if not flowbox.get_first_child() or not any(i.get_visible() for i in flowbox):
        overlay.get_last_child().set_visible(True)
        overlay.get_last_child().set_icon_name("document-new-symbolic" if not flowbox.get_first_child() else "edit-find-symbolic")
        overlay.get_last_child().set_title("Add a Deck" if not flowbox.get_first_child() else "No Results")
search.connect("search-changed", do_search)
search_bar = Gtk.SearchBar(child=Adw.Clamp(maximum_size=300, child=search), key_capture_widget=main_page.get_child())
search_bar.connect_entry(search)
Action("search", lambda *_: search_bar.set_search_mode(not search_bar.get_search_mode_enabled()), "<primary>f")
for i in (header, search_bar): main_page.get_child().add_top_bar(i)

for i in (Button(t=Gtk.MenuButton, icon_name="open-menu", tooltip_text="Menu", menu_model=Menu((("Open Media Folder", "media-folder"), ("Run in Background", "hide-on-close")),
            ("Sort Decks", ("decks-sort", decks_sort)),
            app.default_menu,
            )), Button(t=Gtk.ToggleButton, icon_name="edit-find", tooltip_text="Search", bindings=((None, "active", search_bar, "search-mode-enabled", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE),))): header.pack_end(i)

header.pack_start(Button(t=Gtk.MenuButton, icon_name="list-add", tooltip_text="Add", menu_model=Menu((("New Deck", "new"), ("Import Deck", "import-deck")),)))

delete_dialog = Adw.AlertDialog(default_response="cancel")
def delete(d, r):
    if r != "confirm": return
    app.modifying = True
    if "Cards" in d.get_heading():
        selected_cards = get_selected_cards()
        app.data["Decks"][review_page.get_title()]["Cards"] = [i for n, i in enumerate(app.data["Decks"][review_page.get_title()]["Cards"]) if not n in selected_cards]
        app.modifying = False
        page_changed()
        edit_page.get_child().get_child().set_show_content(False)
    else:
        flowbox.remove(app.deck)
        do_search()
        del app.data["Decks"][review_page.get_title()]
        edit_deck.close()
        view.pop()
        app.modifying = False
    
delete_dialog.connect("response", delete)
for i in ("cancel", "confirm"): delete_dialog.add_response(i, i.title())
delete_dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)

card_factory = Gtk.SignalListItemFactory.new()
def card_drop(d, v, x, y):
    de = review_page.get_title()
    starting_point = int(d.get_widget().get_first_child().get_label()) - 1
    selected = get_selected_cards()
    if len(selected) == 1:
        app.data["Decks"][de]["Cards"][starting_point], app.data["Decks"][de]["Cards"][selected[0]] = app.data["Decks"][de]["Cards"][selected[0]], app.data["Decks"][de]["Cards"][starting_point]
    else:
        items = [app.data["Decks"][de]["Cards"][i] for i in selected]
        kept = [x for i, x in enumerate(app.data["Decks"][de]["Cards"]) if i not in selected]
        insert_pos = sum(1 for i in range(starting_point + 1) if i not in selected)
        app.data["Decks"][de]["Cards"] = kept[:insert_pos] + items + kept[insert_pos:]
    page_changed()
    return True 
def setup_card(f, l):
    l.bindings = []
    box, labels = Gtk.Box(), Gtk.Box(valign=Gtk.Align.CENTER, orientation=Gtk.Orientation.VERTICAL)
    drag_source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
    drag_source.connect("prepare", lambda e, x, y: (n := e.get_widget().get_first_child().get_label(), None if int(n) - 1 in get_selected_cards() else cards.get_model().select_item(int(n) - 1, True), Gdk.ContentProvider.new_for_value(Gtk.StringObject.new(n)))[-1])
    drag_source.connect("drag-begin", lambda e, d: Gtk.DragIcon.get_for_drag(d).set_child(Gtk.Label(margin_top=10, margin_start=10, label=f"{len(get_selected_cards())} Cards", css_classes=["title-4"])))
    box.add_controller(drag_source)
    n, front, back = Gtk.Label(css_classes=["dimmed"]), Gtk.Label(single_line_mode=True, halign=Gtk.Align.START, ellipsize=Pango.EllipsizeMode.END), Gtk.Label(single_line_mode=True, halign=Gtk.Align.START, ellipsize=Pango.EllipsizeMode.END, css_classes=["dimmed"])
    for i in (front, back): labels.append(i)
    for i in (n, labels): box.append(i)
    l.bindings = (l.bind_property("item", n, "label", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: str(int(v.get_string()) + 1) if v else ""),
                  l.bind_property("item", box, "css-classes", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: ["horizontal", "hidden"] if v and app.data["Decks"][review_page.get_title()]["Cards"][int(v.get_string())]["Hidden"] else ["horizontal"]),
                  l.bind_property("item", front, "label", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: app.data["Decks"][review_page.get_title()]["Cards"][int(v.get_string())]["Card"][0] if v else ""),
                  l.bind_property("item", back, "label", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: app.data["Decks"][review_page.get_title()]["Cards"][int(v.get_string())]["Card"][1] if v else ""),
                  front.bind_property("label", front, "visible", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: v != ""),
                  back.bind_property("label", back, "visible", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: v != ""),)
    d = Gtk.DropTarget(preload=True, actions=Gdk.DragAction.MOVE, formats=Gdk.ContentFormats.parse("GtkStringObject"))
    d.connect("drop", card_drop)
    box.add_controller(d)
    l.set_child(box)
def teardown_card(f, l):
    for i in l.bindings: i.unbind()
    if l.get_child():
        while l.get_child().get_first_child(): l.get_child().get_first_child().unparent()
    l.set_child(None)
card_factory.connect("setup", setup_card)
card_factory.connect("teardown", teardown_card)

edit_filter = Gtk.CustomFilter()
def filter_edit(i):
    case = app.lookup_action("case-sensitive").get_state().unpack()
    if app.data["Decks"][review_page.get_title()]["Cards"][int(i.get_string())]["Hidden"] and not app.lookup_action("show-hidden").get_state().unpack(): return False
    s = cards_find.get_text()
    if not case:
        s = s.lower()
    reg = GLib.Regex.new(s, GLib.RegexCompileFlags.DEFAULT if case else GLib.RegexCompileFlags.CASELESS, GLib.RegexMatchFlags.DEFAULT) if app.lookup_action("regex").get_state().unpack() else None
    for n, it in enumerate(("front", "back")):
        if app.lookup_action(it).get_state().unpack():
            c = app.data["Decks"][review_page.get_title()]["Cards"][int(i.get_string())]["Card"][n]
            if reg:
                if not reg.match(c, GLib.RegexMatchFlags.DEFAULT)[0]: return False
            else:
                if not case:
                    c = c.lower()
                if s not in c: return False
    return True
edit_filter.set_filter_func(filter_edit)
cards = Gtk.ListView(vscroll_policy=Gtk.ScrollablePolicy.NATURAL, css_name="cards", css_classes=["navigation-sidebar"], factory=card_factory, valign=Gtk.Align.START, model=Gtk.MultiSelection(model=Gtk.SortListModel(sorter=Gtk.NumericSorter(), model=Gtk.FilterListModel(filter=edit_filter, model=Gtk.StringList.new()))))

cards_toolbar, card_toolbar = Adw.ToolbarView(content=Gtk.Overlay(child=Gtk.ScrolledWindow(child=cards))), Adw.ToolbarView(content=Gtk.Box(homogeneous=True, orientation=Gtk.Orientation.VERTICAL))
def finish_func(avatar, texture):
    texture.colors = palette(texture)
    if not avatar.a: app.deck.get_child().set_custom_image(texture)
def get_paintable(d, r, a=True):
    if a:
        d = app.data_folder.get_child(d)
        if not os.path.exists(d.peek_path()): return None
    else:
        d = d.open_finish(r)
        n = app.data_folder.get_child(review_page.get_title())
        d.copy(n, Gio.FileCopyFlags.NONE)
        d = n
        r = deck_avatar
    r.a = a
    app.thread.submit(load_image, r, d, mimetype="image", finish_func=finish_func)
def select_cover(*_):
    if deck_edits[5].get_title() == "Select Cover":
        Gtk.FileDialog(default_filter=Gtk.FileFilter(name="Image", mime_types=("image/*",))).open(app.window, None, get_paintable, False)
    else:
        deck_avatar.set_custom_image(None)
        app.deck.get_child().set_custom_image(None)
        app.data_folder.get_child(review_page.get_title()).delete()
Action("select-cover", select_cover)

edit_deck = Adw.PreferencesDialog(follows_content_size=True)
edit_deck.add(Adw.PreferencesPage())
deck_avatar = Adw.Avatar(size=200, show_initials=True)
edit_deck.get_visible_page().add((g := Adw.PreferencesGroup(), g.add(Adw.PreferencesRow(activatable=False, sensitive=False, child=deck_avatar)), g)[-1])
nco = ("Random", "Deck Ascending", "Deck Descending")
deck_edits = (Adw.EntryRow(title="Name", show_apply_button=True), TagRow(), Adw.ComboRow(model=Gtk.StringList.new(nco), title="New Cards Order"), Adw.SpinRow.new_with_range(0, 100, 1), Adw.SpinRow.new_with_range(0, 100, 0.2), Adw.ButtonRow(action_name="app.select-cover"), Adw.ButtonRow(css_classes=("button", "activatable", "destructive-action"), title="Delete", action_name="app.delete"))
def deck_changed(*_):
    if app.modifiying: return
    if deck_edits[0].get_text() != app.deck.get_child().get_text():
        if deck_edits[0].get_text() in app.data["Decks"]: return Toast(f"{deck_edits[0].get_text()} already exists!")
        c = app.data_folder.get_child(review_page.get_title())
        if os.path.exists(c.peek_path()): c.move(app.data_folder.get_child(deck_edits[0].get_text()), Gio.FileCopyFlags.NONE)
        app.data["Decks"][deck_edits[0].get_text()] = app.data["Decks"].pop(app.deck.get_child().get_text())
        app.deck.get_child().set_text(deck_edits[0].get_text())
        review_page.set_title(app.deck.get_child().get_text())
    for n, i in enumerate(edit_values):
        v = deck_edits[n + 1].get_property(i)
        app.data["Decks"][deck_edits[0].get_text()][deck_edits[n + 1].get_title() if deck_edits[n + 1].get_title() else "Tags"] = v.get_string() if hasattr(v, "get_string") else v
deck_edits[0].connect("apply", deck_changed)
edit_values = ("tags", "selected-item", "value", "value")
for n, i in enumerate(edit_values): deck_edits[n + 1].connect(f"notify::{i}", deck_changed)
deck_edits[0].bind_property("text", deck_avatar, "text", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
deck_edits[5].bind_property("title", deck_edits[5], "css-classes", GObject.BindingFlags.DEFAULT, lambda b, v: ("button", "activatable", "suggested-action" if v == "Select Cover" else "destructive-action",))
deck_avatar.bind_property("custom-image", deck_edits[5], "title", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: "Erase Cover" if v else "Select Cover")
deck_edits[3].set_title("New Cards/Day")
deck_edits[4].set_title("Ease Multiplier")
edit_deck.get_visible_page().add((g := Adw.PreferencesGroup(), tuple(g.add(i) for i in deck_edits), g)[-1])
def deck_edit(*_):
    app.modifiying = True
    deck_avatar.set_custom_image(app.deck.get_child().get_custom_image())
    deck_edits[0].set_text(app.deck.get_child().get_text())
    for n, i in enumerate(edit_values):
        v = app.data["Decks"][deck_edits[0].get_text()][deck_edits[n + 1].get_title() if deck_edits[n + 1].get_title() else "Tags"]
        if i == "selected-item":
            i, v = "selected", nco.index(v)
        deck_edits[n + 1].set_property(i, v)
    app.modifiying = False
    edit_deck.present(app.window)
Action("edit-deck", lambda *_: deck_edit() if carousel.get_mapped() else None, "<primary>d")

c_status = Adw.StatusPage()
cards.get_model().bind_property("n-items", c_status, "icon-name", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: "edit-find-symbolic" if v == 0 and cards.get_model().get_model().get_model().get_model().get_n_items() != 0 else "document-new-symbolic")
cards.get_model().bind_property("n-items", c_status, "visible", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: v == 0)
c_status.bind_property("icon-name", c_status, "title", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: "No Results" if v == "edit-find-symbolic" else "Add a Card")

cards_toolbar.get_content().add_overlay(c_status)

for _ in range(2): card_toolbar.get_content().append(Gtk.ScrolledWindow(child=Gtk.TextView(justification=Gtk.Justification.CENTER, wrap_mode=Gtk.WrapMode.CHAR, css_classes=["view", "document", "card"])))
edit_page = Adw.NavigationPage(child=Adw.Bin(css_name="edit", child=Adw.NavigationSplitView(min_sidebar_width=400, max_sidebar_width=450, sidebar=Adw.NavigationPage(child=cards_toolbar), content=Adw.NavigationPage(child=card_toolbar, title="Card"))))
Action("edit-cards", lambda *_: view.push(edit_page) if carousel.get_mapped() else None, "<primary>e")
_breakpoint.add_setter(edit_page.get_child().get_child(), "collapsed", True)
cards.connect("activate", lambda l, p: edit_page.get_child().get_child().set_show_content(True))

get_selected_cards = lambda: tuple(int(cards.get_model().get_item(i).get_string()) for i in range(cards.get_model().get_n_items()) if cards.get_model().is_selected(i))

def card_select(*_):
    if app.modifying: return
    selected_cards = get_selected_cards()
    app.modifying = True
    if len(selected_cards) == 1:
        c = app.data["Decks"][review_page.get_title()]["Cards"][selected_cards[0]]
        for n, i in enumerate(card_sides): i.set_text(c["Card"][n])
        ease_button.set_value(c["Ease"])
        hide_button.set_tooltip_text("Unhide" if c["Hidden"] else "Hide")
        date_button.get_popover().get_child().set_date(GLib.DateTime.new_from_unix_utc(c["Date"]).to_local())
    app.modifying = False
cards.get_model().connect("selection-changed", card_select)

def new_card(*_):
    app.data["Decks"][review_page.get_title()]["Cards"].append({"Card": ("Front", "Back"), "Date": GLib.DateTime.new_now_utc().to_unix(), "Hidden": False, "Ease": 0})
    cards.get_model().get_model().get_model().get_model().append(str(len(app.data["Decks"][review_page.get_title()]["Cards"]) - 1))
cards_header = Adw.HeaderBar()
cards_header.pack_start(Button(t=Gtk.MenuButton, menu_model=Menu((("New Card", "new"), ("Import Cards", "import-cards")),), icon_name="list-add", tooltip_text="Add"))

card_sides = tuple(i.get_child().get_buffer() for i in card_toolbar.get_content())
ease_button = Gtk.SpinButton.new_with_range(0, 200, 1)
ease_button.set_tooltip_text("Ease")
date_button = Button(t=Gtk.MenuButton, halign=Gtk.Align.START, icon_name="month", tooltip_text="Date", popover=Gtk.Popover(child=Gtk.Calendar()))
Action("delete", lambda *_: (delete_dialog.set_heading("Delete Deck?" if edit_deck.get_mapped() else "Delete Selected Cards?"), delete_dialog.present(app.window)))
delete_button = Button(action_name="app.delete", icon_name="user-trash", tooltip_text="Delete")
hide_button = Button(callback=lambda b: b.set_tooltip_text("Hide" if b.get_tooltip_text() == "Unhide" else "Unhide"), tooltip_text="Hide", bindings=((None, "tooltip-text", None, "icon-name", None, lambda b, v: "view-conceal-symbolic" if v == "Unhide" else "view-reveal-symbolic"),))
def card_changed(b, *_):
    if app.modifying: return
    selected_cards = get_selected_cards()
    for i in selected_cards:
        if isinstance(b, Gtk.TextBuffer):
            app.data["Decks"][review_page.get_title()]["Cards"][i]["Card"] = (card_sides[0].get_property("text"), card_sides[1].get_property("text"))
        app.data["Decks"][review_page.get_title()]["Cards"][i].update({"Date": date_button.get_popover().get_child().get_date().to_utc().to_unix(), "Hidden": hide_button.get_tooltip_text() == "Unhide", "Ease": int(ease_button.get_value())})
hide_button.connect("notify::tooltip-text", card_changed)
ease_button.connect("notify::value", card_changed)
date_button.get_popover().get_child().connect("notify::date", card_changed)
for i in card_sides: i.connect("notify::text", card_changed)

def do_replace(*_):
    deck = review_page.get_title()
    selected_cards = get_selected_cards()
    s = cards_find.get_text() if app.lookup_action("regex").get_state().unpack() else GLib.Regex.escape_string(cards_find.get_text(), len(cards_find.get_text()))
    re = regex(s, flags=NOFLAG if app.lookup_action("case-sensitive").get_state().unpack() else IGNORECASE)
    for i in selected_cards:
        for n, it in enumerate(("front", "back")):
            if app.lookup_action(it).get_state().unpack():
                new = re.sub(cards_replace.get_text(), app.data["Decks"][deck]["Cards"][i]["Card"][n])
                other = app.data["Decks"][deck]["Cards"][i]["Card"][0 if n == 1 else 1]
                app.data["Decks"][deck]["Cards"][i]["Card"] = (other if n == 1 else new, other if n == 0 else new)
    page_changed()

cards_find, cards_replace = Gtk.SearchEntry(placeholder_text="Find", hexpand=True), Gtk.Entry(placeholder_text="Replace", primary_icon_name="edit-find-replace-symbolic", hexpand=True)
cards_find.connect("search-changed", lambda *_: edit_filter.changed(Gtk.FilterChange.DIFFERENT))
cards_find.connect("stop-search", lambda *_: cards_search_bar.set_search_mode(False))
cards_search_bar = Gtk.SearchBar(child=Gtk.Box(), key_capture_widget=cards_toolbar)
for _ in range(2): cards_search_bar.get_child().append(Gtk.Box(orientation=Gtk.Orientation.VERTICAL))
for i in (cards_find, cards_replace):
    cards_search_bar.get_child().get_first_child().append(i)
for i in (Button(t=Gtk.MenuButton, menu_model=Menu((("Show Hidden", "show-hidden"), ("Regular Expressions", "regex"), ("Case Sensitive", "case-sensitive"), ("Front", "front"), ("Back", "back")),), tooltip_text="Options", icon_name="applications-system"), Button(callback=do_replace, tooltip_text="Replace Selected", icon_name="document-edit")): cards_search_bar.get_child().get_last_child().append(i)
cards_search_bar.connect_entry(search)
for i in (cards_header, cards_search_bar): cards_toolbar.add_top_bar(i)
search_bar.bind_property("search-mode-enabled", cards_search_bar, "search-mode-enabled", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

card_header = Adw.HeaderBar(show_back_button=False)
_breakpoint.add_setter(card_header, "show-back-button", True)
_breakpoint.add_setter(card_header, "show-title", False)
for i in (delete_button, hide_button): card_header.pack_end(i)
card_toolbar.add_top_bar(card_header)
for i in (date_button, ease_button, ): card_header.pack_start(i)

carousel = Adw.Carousel(allow_scroll_wheel=False)
add_grab_focus(carousel)
review_page = Adw.NavigationPage(child=Adw.ToolbarView(content=Gtk.Overlay(child=Gtk.ScrolledWindow(child=carousel))))
status = Adw.StatusPage()
carousel.get_ancestor(Gtk.Overlay).add_overlay(status)
for i in (edit_page, edit_page.get_child().get_child().get_sidebar(), edit_deck): review_page.bind_property("title", i, "title", GObject.BindingFlags.DEFAULT)
r_header = Adw.HeaderBar()

menu = Menu((("Undo Last Review", "undo-review"),),
             (("Autoplay", "autoplay"), ("Edit Deck", "edit-deck"), ("Edit Cards", "edit-cards"), ("Export Deck", "export"),),
             ("Sort Cards", ("review-sort", review_sort)),
             app.default_menu)
r_header.pack_end(Button(t=Gtk.MenuButton, icon_name="open-menu", tooltip_text="Menu", menu_model=menu))
review_page.get_child().add_top_bar(r_header)
r_bottom = Gtk.SearchBar(child=Gtk.CenterBox(hexpand=True, start_widget=Button(callback=lambda *_: answer(-1), tooltip_text="Bad", icon_name="thumbs-down-outline"), center_widget=Adw.CarouselIndicatorDots(carousel=carousel), end_widget=Button(callback=lambda *_: answer(1), tooltip_text="Good", icon_name="thumbs-up-outline")))
status.bind_property("visible", r_bottom, "search-mode-enabled", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN)
review_page.get_child().add_bottom_bar(r_bottom)

Action("export", lambda *_: Gtk.FileDialog(title="Export Deck", accept_label="Export", initial_name=f"{review_page.get_title()}.json").save(app.window, None, lambda d, r: d.save_finish(r).replace_contents(json_string(app.data["Decks"][review_page.get_title()], ensure_ascii=False).encode("utf-8"), None, False, Gio.FileCreateFlags.NONE)))
Action("import-deck", lambda *_: Gtk.FileDialog(default_filter=Gtk.FileFilter(name="JSON File", mime_types=("application/json",))).open_multiple(app.window, None, lambda d, r: add(d.open_multiple_finish(r))))

view = Adw.NavigationView()
app.window.get_content().set_child(view)
view.add(main_page)

random_sort = lambda i: GLib.random_int()
def get_review_cards(deck):
    now, new_cards, to_review = GLib.DateTime.new_now_utc(), [], []
    for n, i in enumerate(app.data["Decks"][deck]["Cards"]):
        if i["Hidden"]: continue
        if i["Ease"] == 0: new_cards.append(n)
        elif now.to_unix() > i["Date"]: to_review.append(n)
    if "Descending" in app.data["Decks"][deck]["New Cards Order"]: new_cards.reverse()
    if "Random" in app.data["Decks"][deck]["New Cards Order"]: new_cards.sort(key=random_sort)
    n = app.data["Decks"][deck]["New Cards/Day"]
    if GLib.DateTime.new_from_unix_utc(app.data["Decks"][deck]["Daily"][0]).to_local().get_ymd() == now.to_local().get_ymd():
        n -= app.data["Decks"][deck]["Daily"][1]
    else:
        app.data["Decks"][deck]["Daily"] = (GLib.DateTime.new_now_utc().to_unix(), 0)
    return new_cards[:int(max(n, 0))] + to_review
def cards_available(i):
    if app.timer_id != i: return
    no = Gio.Notification.new("Cards Available to Review")
    no.set_body(f"There are {sum(len(get_review_cards(i)) for i in app.data['Decks'])} cards to review")
    app.send_notification("cards", no)
def sort_review(*_):
    app.modifying = True
    carousel.p = None
    while carousel.get_first_child(): carousel.remove(carousel.get_first_child())
    s = app.lookup_action("review-sort").get_state().unpack()
    if "Random" in s: app.cards.sort(key=random_sort)
    else: app.cards.sort(key=lambda i: app.data["Decks"][review_page.get_title()]["Cards"][int(i.get_name())]["Ease"], reverse="Descending" in s)
    for i in app.cards: carousel.append(i)
    app.modifying = False
    review_changed(carousel, 0)
def do_undo(*_):
    if not undo_action.get_enabled(): return
    i = app.undo.pop(-1)
    deck = review_page.get_title()
    print(f"{deck} / Card {i[0]} / Ease {i[1]} <- {app.data['Decks'][deck]['Cards'][i[0]]['Ease']}, Date {GLib.DateTime.new_from_unix_utc(i[2]).format('%c')} <- {GLib.DateTime.new_from_unix_utc(app.data['Decks'][deck]['Cards'][i[0]]['Date']).format('%c')}")
    app.data["Decks"][deck]["Cards"][i[0]]["Ease"], app.data["Decks"][deck]["Cards"][i[0]]["Date"] = i[1], i[2]
    if i[1] == 0: add_daily(-1)
    add_review(-1)
    page_changed(undo=app.undo)
undo_action = Action("undo-review", do_undo)
def page_changed(*_, undo=[]):
    if app.modifying: return
    app.modifying = True
    app.cards, app.undo = [], undo
    undo_action.set_enabled(bool(app.undo))
    if view.get_visible_page() == review_page:
        for i in get_review_cards(review_page.get_title()): app.cards.append(Adw.Bin(hexpand=True, name=str(i)))
        sort_review()
        review_changed(carousel, 0)
        status.set_visible(not carousel.get_first_child())
        if status.get_visible() and not tuple(i for i in app.data["Decks"][review_page.get_title()]["Cards"] if not i["Hidden"]):
            status.set_description("")
            status.set_title("No Cards")
            status.set_icon_name("folder-documents-symbolic")
        elif status.get_visible():
            n = GLib.DateTime.new_now_local()
            next_day = GLib.DateTime.new(GLib.TimeZone.new_local(), n.get_year(), n.get_month(), n.get_day_of_month(), 0, 0, 0).add_days(1).to_unix()
            t = min(tuple(i["Date"] for i in app.data["Decks"][review_page.get_title()]["Cards"] if not i["Hidden"] and i["Ease"] > 0), default=next_day)
            n = GLib.DateTime.new_from_unix_local(min(next_day, t) if tuple(i for i in app.data["Decks"][review_page.get_title()]["Cards"] if i["Ease"] == 0) else t).difference(n) / 1e6
            app.timer_id += 1
            GLib.timeout_add(min((n + 1) * 1000, 4294967295), cards_available, app.timer_id)
            t = " ".join(f"{int(v)} {unit}" for v, unit in zip((n // 2592000, (n % 2592000) // 86400, (n % 86400) // 3600, (n % 3600) // 60), ["months", "days", "hours", "minutes"]) if v > 0) or [f"{int(n)} seconds"]
            status.set_description(f"{t} for more cards")
            status.set_title("Good Job")
            status.set_icon_name("brain-augemnted-symbolic")
    if view.get_visible_page() == edit_page:
        cards.get_model().get_model().get_model().set_model(Gtk.StringList.new(tuple(str(i) for i in range(len(app.data["Decks"][review_page.get_title()]["Cards"])))))
        app.modifying = False
        cards.get_model().select_item(0, True)
    app.modifying = False
view.connect("notify::visible-page", page_changed)
edit_deck.connect("closed", page_changed)

def add_daily(v):
    d_time, cards = app.data["Decks"][review_page.get_title()]["Daily"]
    app.data["Decks"][review_page.get_title()]["Daily"] = (d_time, cards + v)
def add_review(v):
    now, last_review = GLib.DateTime.new_now_utc(), app.data["Decks"][review_page.get_title()]["Reviews"][-1]
    if not v == -1 and now.difference(GLib.DateTime.new_from_unix_utc(last_review[0])) > 3.6e9: app.data["Decks"][review_page.get_title()]["Reviews"].append((now.to_unix(), v))
    else:
        app.data["Decks"][review_page.get_title()]["Reviews"][-1] = (last_review[0], last_review[1] + v)
def answer(v):
    if status.get_visible(): return
    deck, c = review_page.get_title(), carousel.get_nth_page(carousel.get_position())
    n, now = int(c.get_name()), GLib.DateTime.new_now_utc()
    e = app.data["Decks"][deck]["Cards"][n]["Ease"]
    ne = app.data["Decks"][deck]["Cards"][n]["Ease"] = max(1, v + e)
    if e == 0:
        add_daily(1)
        app.data["Decks"][deck]["Cards"][n]["Date"] = now.to_unix()
    d = GLib.DateTime.new_from_unix_utc(app.data["Decks"][deck]["Cards"][n]["Date"])
    new = d.add_seconds((d.to_unix_usec() * ((0.012 if e == 0 and v == -1 else ne) / 1e11)) * app.data["Decks"][deck]["Ease Multiplier"])
    app.data["Decks"][deck]["Cards"][n]["Date"] = new.to_unix()
    print(f"{deck} / Card {n} / Ease {e} -> {ne}, Date {d.format('%c')} -> {new.format('%c')}")
    add_review(1)
    app.undo.append((n, e, d.to_unix()))
    undo_action.set_enabled(True)
    carousel.remove(c)
    carousel.p = None
    review_changed(carousel, carousel.get_position())
    if not carousel.get_first_child(): page_changed()
controller = carousel.observe_controllers()[-1]
actions = tuple(i for i in controller)
for i in range(2): controller.remove_shortcut(actions[i])
for i in ("a", "h"):
    carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(i), Gtk.CallbackAction.new(lambda w, a, v: v.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, carousel, GLib.Variant("u", Gtk.DirectionType.LEFT)), actions[2].get_action())))
    carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(f"<shift>{i}"), Gtk.CallbackAction.new(lambda w, a, v: v.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, carousel, GLib.Variant("u", Gtk.DirectionType.TAB_BACKWARD)), actions[6].get_action())))
for i in ("d", "l"):
    carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(i), Gtk.CallbackAction.new(lambda w, a, v: v.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, carousel, GLib.Variant("u", Gtk.DirectionType.RIGHT)), actions[3].get_action())))
    carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(f"<shift>{i}"), Gtk.CallbackAction.new(lambda w, a, v: v.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, carousel, GLib.Variant("u", Gtk.DirectionType.TAB_FORWARD)), actions[7].get_action())))

carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<shift>Left"), Gtk.CallbackAction.new(lambda w, a, v: v.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, carousel, GLib.Variant("u", Gtk.DirectionType.TAB_BACKWARD)), actions[6].get_action())))
carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<shift>Right"), Gtk.CallbackAction.new(lambda w, a, v: v.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, carousel, GLib.Variant("u", Gtk.DirectionType.TAB_FORWARD)), actions[7].get_action())))

carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("1"), Gtk.CallbackAction.new(lambda *_: answer(1))))
carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("2"), Gtk.CallbackAction.new(lambda *_: answer(-1))))
carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<primary>z"), Gtk.CallbackAction.new(do_undo)))
def move(d=None):
    if app.modifying or status.get_visible(): return
    page = carousel.get_nth_page(carousel.get_position())
    side = page.get_child().get_nth_page(page.get_child().get_position())
    if d is None:
        d = (side.get_next_sibling() or side.get_prev_sibling())
    else:
        d = side.get_next_sibling() if d == 1 else side.get_prev_sibling()
    if d: page.get_child().scroll_to(d, True)
    else: return Gdk.EVENT_PROPAGATE
for i in ("Up", "w", "k"): carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(i), Gtk.CallbackAction.new(lambda *_: move(0))))
for i in ("Down", "s", "j"): carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(i), Gtk.CallbackAction.new(lambda *_: move(1))))
carousel.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("space"), Gtk.CallbackAction.new(lambda *_: move())))

t_drop = Gtk.DropTarget(preload=True, actions=Gdk.DragAction.COPY, formats=Gdk.ContentFormats.parse("GdkFileList"))
def add(v, r=False):
    v = v.read_value_finish(r) if r else v
    for file in v:
        if file.get_basename().endswith("csv"):
            now = GLib.DateTime.new_now_utc().to_unix()
            fields = csv(file.load_contents()[1].decode("utf-8").splitlines())
            for i in fields:
                middle_index = len(i) // 2
                front = ", ".join(i[:middle_index])
                back = ", ".join(i[middle_index:])
                app.data["Decks"][review_page.get_title()]["Cards"].append({"Card": (front, back), "Date": now, "Hidden": False, "Ease": 0})
            page_changed()
        elif file.get_basename().endswith(".json"):
            o = json(file.load_contents()[1].decode("utf-8"))
            ne = new_deck(name=file.get_basename().rsplit(".", 1)[0])
            for i in ne:
                if not i in o:
                    ne = None
                    Toast(f"{file.get_basename()} could not be added")
                    break
            if ne is None: continue
            new, n = file.get_basename().rsplit(".", 1)[0], 0
            while new in app.data["Decks"]:
                n += 1
                new = f"{file.get_basename().rsplit('.', 1)[0]} {n}"
            ne.update(o)
            app.data["Decks"][new] = ne
            add_deck(new)
Action("import-cards", lambda *_: Gtk.FileDialog(default_filter=Gtk.FileFilter(name="CSV File", mime_types=("text/csv",))).open_multiple(app.window, None, lambda d, r: add(d.open_multiple_finish(r))))
view.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<primary>v"), Gtk.CallbackAction.new(lambda w, a: (c := app.window.get_display().get_clipboard(), (c.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, add), True)[-1] if c.get_formats().contain_gtype(Gdk.FileList) else False)[-1] if flowbox.get_mapped() else False)))
t_drop.connect("drop", lambda d, v, *_: add(v))
main_page.get_child().add_controller(t_drop)

def new_deck(*_, name=None):
    new = na = name if name else "New Deck"
    n = 0
    while new in app.data["Decks"]:
        n += 1
        new = f"{na} {n}" 
    now = GLib.DateTime.new_now_utc().to_unix()
    o = {"Reviews": [(now, 0)], "Daily": (now, 0), "Cards": [], "Tags": [], "New Cards/Day": 20, "New Cards Order": "Deck Ascending", "Ease Multiplier": 1}
    if not name:
        app.data["Decks"][new] = o
        add_deck(new)
    else: return o
Action("new", lambda *_: new_card() if cards.get_mapped() else new_deck() if flowbox.get_mapped() else None, "<primary>n")

def sequential_play(media, b=False):
    if media.get_playing(): return
    if hasattr(media, "sig"):
        media.disconnect(media.sig)
        del media.sig
    if b:
        n = app.to_play.index(media) + 1
        if n >= len(app.to_play): return
        else:
            media = app.to_play[n]
    media.play()
    media.sig = media.connect("notify::playing", sequential_play)
def autoplay(*_):
    if app.modifying or not app.lookup_action("autoplay").get_state().unpack() or status.get_visible(): return
    for i in app.to_play:
        if hasattr(i, "sig"):
            i.disconnect(i.sig)
            del i.sig
        i.pause()
    app.to_play = []
    page = carousel.get_nth_page(carousel.get_position())
    side = page.get_child().get_nth_page(page.get_child().get_position())
    for i in side.get_child().get_child():
        if hasattr(i, "controls"):
            i = i.controls
        m = i.media if hasattr(i, "media") else i.get_media_stream() if hasattr(i, "get_media_stream") else None
        if not m: continue
        app.to_play.append(m)
    if not app.to_play: return
    sequential_play(app.to_play[0])
embed_audio = regex(r"\[(.*)\]\(file://(.*)\)")
card_label = lambda i: Gtk.Label(use_markup=True, label=i, justify=Gtk.Justification.CENTER, wrap=True, wrap_mode=Pango.WrapMode.CHAR)
def label_play(e, p, x, y):
    if p > 1: e.get_widget().media.seek(0)
    e.get_widget().media.pause() if e.get_widget().media.get_playing() else e.get_widget().media.play()
def parse_side(box, card):
    if "file://" in card:
        text = ""
        for line in card.split("\n"):
            match = embed_audio.match(line)
            if match:
                if text:
                    box.append(card_label(text))
                    text = ""
                g = match.groups()
                label = card_label(g[0])
                label.media = Gtk.MediaFile.new_for_file(media_folder.get_child(g[1]))
                c = Gtk.GestureClick()
                c.connect("released", label_play)
                label.add_controller(c)
                box.append(label)
            else:
                if "file://" in line:
                    if text:
                        box.append(card_label(text))
                        text = ""
                    file = media_folder.get_child(line.split("//")[1])
                    media = Media(file.get_uri(), parent_type=Gtk.Overlay, play=False, c__halign=Gtk.Align.CENTER, c__content_fit=Gtk.ContentFit.SCALE_DOWN)
                    media.set_tooltip_text(file.get_basename())
                    box.append(media)
                else:
                    text += line
        if text: box.append(card_label(text))
    else: box.append(card_label(card))

def review_changed(c, i):
    if app.modifying or not carousel.get_first_child() or carousel.p == i: return
    carousel.p = i
    current, deck = carousel.get_nth_page(i), review_page.get_title()
    for i in (current, current.get_next_sibling()):
        if not i: continue
        if i == current and i.get_child():
            carousel.grab_focus()
            autoplay()
            continue
        if i.get_child(): continue
        n = int(i.get_name())
        i.set_child(Adw.Carousel(css_name="card", orientation=Gtk.Orientation.VERTICAL))
        i.get_child().connect("page-changed", autoplay)
        for it in app.data["Decks"][deck]["Cards"][n]["Card"]:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            parse_side(box, it)
            i.get_child().append(Gtk.ScrolledWindow(vexpand=True, child=Gtk.Viewport(vscroll_policy=Gtk.ScrollablePolicy.NATURAL, child=box)))
        if i == current:
            carousel.grab_focus()
            autoplay()
carousel.connect("page-changed", review_changed)

media_folder = app.data_folder.get_child("media")
if not os.path.exists(media_folder.peek_path()): media_folder.make_directory_with_parents()
Action("media-folder", lambda *_: launch(media_folder))

def new_a(i, g, c=None):
    a = Action(i, callback=c, stateful=app.data[g][i])
    a.path = g
    app.persist.append(a)
new_a("autoplay", "Review", autoplay)
sort_get_date = lambda i: app.data["Decks"][review_page.get_title()]["Cards"][int(i.get_string())]["Date"]
sort_get_deck = lambda i: int(i.get_string())
for i in app.data["Sort"]: new_a(i, "Sort", sort_review if "review" in i else (lambda *_: flowbox.invalidate_sort(), )[0] if "decks" in i else None)
app.lookup_action("cards-sort").bind_property("state", cards.get_model().get_model().get_sorter(), "sort-order", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: getattr(Gtk.SortType, v.unpack().split(" ")[-1].upper()))
app.lookup_action("cards-sort").bind_property("state", cards.get_model().get_model().get_sorter(), "expression", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE, lambda b, v: Gtk.ClosureExpression.new(int, sort_get_date if "Date" in v.unpack() else sort_get_deck))
for i in (Button(t=Gtk.MenuButton, menu_model=Menu(("Sort Cards", ("cards-sort", cards_sort) ),), tooltip_text="Sort", bindings=((app.lookup_action("cards-sort"), "state", None, "icon-name", None, lambda b, v: f"view-sort-{v.unpack().split(' ')[-1].lower()}"),)), Button(t=Gtk.ToggleButton, icon_name="edit-find-replace", tooltip_text="Search", bindings=((None, "active", cards_search_bar, "search-mode-enabled", GObject.BindingFlags.DEFAULT | GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE),)),): cards_header.pack_end(i)
for i in app.data["Edit"]: new_a(i, "Edit", lambda *_: edit_filter.changed(Gtk.FilterChange.DIFFERENT))
for i in app.data["Decks"]: GLib.idle_add(add_deck, i)
GLib.idle_add(do_search)
app.run()
