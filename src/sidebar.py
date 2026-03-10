"""
Sidebar with hierarchical connection tree.

Displays connections organized in groups using Gtk.TreeView.
Supports right-click context menu, double-click to connect,
and drag-and-drop reordering.
"""

import gi
gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, GLib, Gdk, GObject, Pango
from typing import Optional

from .connection import Connection, ConnectionManager


# TreeStore columns
COL_DISPLAY_NAME = 0  # str: Display text
COL_ICON_NAME = 1     # str: Icon name
COL_CONNECTION_ID = 2  # str: Connection ID (empty for groups)
COL_GROUP_PATH = 3     # str: Group path (for group rows)
COL_IS_GROUP = 4       # bool: True if this is a group row
COL_TOOLTIP = 5        # str: Tooltip text
COL_VISIBLE = 6        # bool: Visible (for search filtering)


class Sidebar(Gtk.Box):
    """
    Left panel showing connections grouped hierarchically.

    Signals:
        connect-requested: User double-clicked a connection
        edit-requested: User wants to edit a connection
        add-requested: User wants to add a new connection
    """

    __gsignals__ = {
        "connect-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "edit-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "add-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "delete-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "add-group-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, connection_manager: ConnectionManager, credential_store=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.connection_manager = connection_manager
        self.credential_store = credential_store

        self.set_size_request(200, -1)
        self.add_css_class("sidebar")

        # --- Search bar ---
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search connections...")
        self.search_entry.set_margin_start(6)
        self.search_entry.set_margin_end(6)
        self.search_entry.set_margin_top(6)
        self.search_entry.set_margin_bottom(6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # --- Toolbar ---
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_bottom(6)
        toolbar.set_halign(Gtk.Align.CENTER)

        btn_add = Gtk.Button(icon_name="list-add-symbolic")
        btn_add.set_tooltip_text("Add Connection")
        btn_add.connect("clicked", lambda _: self.emit("add-requested"))
        btn_add.add_css_class("flat")
        toolbar.append(btn_add)

        btn_add_group = Gtk.Button(icon_name="folder-new-symbolic")
        btn_add_group.set_tooltip_text("Add Group")
        btn_add_group.connect("clicked", lambda _: self.emit("add-group-requested"))
        btn_add_group.add_css_class("flat")
        toolbar.append(btn_add_group)

        btn_expand = Gtk.Button(icon_name="view-more-symbolic")
        btn_expand.set_tooltip_text("Expand All")
        btn_expand.connect("clicked", lambda _: self.tree_view.expand_all())
        btn_expand.add_css_class("flat")
        toolbar.append(btn_expand)

        btn_collapse = Gtk.Button(icon_name="view-less-symbolic")
        btn_collapse.set_tooltip_text("Collapse All")
        btn_collapse.connect("clicked", lambda _: self.tree_view.collapse_all())
        btn_collapse.add_css_class("flat")
        toolbar.append(btn_collapse)

        self.append(toolbar)

        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Tree View ---
        # TreeStore: display_name, icon_name, connection_id, group_path, is_group, tooltip, visible
        self.store = Gtk.TreeStore(str, str, str, str, bool, str, bool)

        self._search_text = ""

        # Use the store directly (no filter model) so DnD reordering works
        self.tree_view = Gtk.TreeView(model=self.store)
        self.tree_view.set_headers_visible(False)
        self.tree_view.set_enable_search(False)
        self.tree_view.set_tooltip_column(COL_TOOLTIP)
        self.tree_view.set_activate_on_single_click(False)
        self.tree_view.set_reorderable(True)

        # Suppress harmless GtkGizmo snapshot warning from set_reorderable
        GLib.log_set_handler(
            "Gtk",
            GLib.LogLevelFlags.LEVEL_WARNING,
            self._filter_gtk_warnings,
        )

        # Column with icon + text
        column = Gtk.TreeViewColumn()
        column.set_expand(True)

        # Icon renderer
        icon_renderer = Gtk.CellRendererPixbuf()
        column.pack_start(icon_renderer, False)
        column.add_attribute(icon_renderer, "icon-name", COL_ICON_NAME)
        column.add_attribute(icon_renderer, "visible", COL_VISIBLE)

        # Text renderer
        text_renderer = Gtk.CellRendererText()
        text_renderer.set_padding(4, 2)
        text_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        column.pack_start(text_renderer, True)
        column.add_attribute(text_renderer, "text", COL_DISPLAY_NAME)
        column.add_attribute(text_renderer, "visible", COL_VISIBLE)

        self.tree_view.append_column(column)

        # Double-click to connect
        self.tree_view.connect("row-activated", self._on_row_activated)

        # Right-click menu
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.tree_view.add_controller(click)

        # Scrolled window for the tree
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_child(self.tree_view)
        self.append(self._scrolled)

        # Track whether we're programmatically rebuilding the tree
        self._refreshing = False

        # Snapshot of tree structure before DnD, for validation
        self._pre_dnd_snapshot: dict | None = None

        # Persist reorder after drag-and-drop completes
        self.store.connect("row-inserted", self._on_row_inserted)
        self.store.connect("row-deleted", self._on_row_deleted)
        self._pending_sync = False

        # Initial population
        self.refresh()

    def refresh(self):
        """Rebuild the tree from the connection manager."""
        self._refreshing = True
        self.store.clear()

        # Build group structure and connection placement
        groups = self.connection_manager.get_groups()
        connections = self.connection_manager.get_connections()

        # Track created group rows by path
        group_iters = {}

        # Create group rows
        for group_path in groups:
            parts = group_path.split("/")
            for i, part in enumerate(parts):
                current_path = "/".join(parts[:i + 1])
                if current_path not in group_iters:
                    parent_path = "/".join(parts[:i]) if i > 0 else None
                    parent_iter = group_iters.get(parent_path)
                    iter_ = self.store.append(parent_iter, [
                        part,                          # display name
                        "folder-symbolic",             # icon
                        "",                            # no connection ID
                        current_path,                  # group path
                        True,                          # is group
                        f"Group: {current_path}",      # tooltip
                        True,                          # visible
                    ])
                    group_iters[current_path] = iter_

        # Add connections (in creation order)
        for conn in connections:
            parent_iter = group_iters.get(conn.group)

            icon = "network-server-symbolic"
            if self.credential_store and self.credential_store.has_credentials(conn.id):
                icon = "dialog-password-symbolic"

            tooltip = f"{conn.display_name()}"
            if conn.description:
                tooltip += f"\n{conn.description}"

            self.store.append(parent_iter, [
                conn.name or conn.display_name(),  # display name
                icon,                              # icon
                conn.id,                           # connection ID
                conn.group,                        # group path
                False,                             # not a group
                tooltip,                           # tooltip
                True,                              # visible
            ])

        # Expand all by default
        self.tree_view.expand_all()
        self._refreshing = False
        # Capture the tree structure as the baseline for DnD validation
        self._pre_dnd_snapshot = self._take_snapshot()

    def get_selected_connection_id(self) -> Optional[str]:
        """Get the connection ID of the selected row, or None."""
        selection = self.tree_view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            conn_id = model.get_value(iter_, COL_CONNECTION_ID)
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if not is_group and conn_id:
                return conn_id
        return None

    def get_selected_group_path(self) -> Optional[str]:
        """Get the group path of the selected row, or None."""
        selection = self.tree_view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                return model.get_value(iter_, COL_GROUP_PATH)
        return None

    # --- GLib log filter ---

    @staticmethod
    def _filter_gtk_warnings(log_domain, log_level, message):
        """Suppress the harmless GtkGizmo snapshot-without-allocation warning."""
        if message and "GtkGizmo" in message and "without a current allocation" in message:
            return  # swallow this specific warning
        # Let all other warnings through to stderr
        GLib.log_default_handler(log_domain, log_level, message)

    # --- Drag-and-drop reorder persistence ---

    def _on_row_inserted(self, model, path, iter_):
        """A row was inserted (happens during DnD reorder)."""
        if self._refreshing:
            return
        if not self._pending_sync:
            self._pending_sync = True
            GLib.idle_add(self._sync_order_from_store)

    def _on_row_deleted(self, model, path):
        """A row was deleted (happens during DnD reorder)."""
        if self._refreshing:
            return
        if not self._pending_sync:
            self._pending_sync = True
            GLib.idle_add(self._sync_order_from_store)

    def _take_snapshot(self) -> dict:
        """Capture the parent-group of every connection and the parent of every group."""
        snapshot = {"connections": {}, "groups": {}}
        self._snapshot_walk(self.store, None, "", snapshot)
        return snapshot

    def _snapshot_walk(self, model, parent_iter, parent_group, snapshot):
        iter_ = model.iter_children(parent_iter)
        while iter_:
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                group_path = model.get_value(iter_, COL_GROUP_PATH)
                snapshot["groups"][group_path] = parent_group
                self._snapshot_walk(model, iter_, group_path, snapshot)
            else:
                conn_id = model.get_value(iter_, COL_CONNECTION_ID)
                if conn_id:
                    snapshot["connections"][conn_id] = parent_group
                # Also recurse into non-group nodes to find items
                # that were incorrectly dropped as children
                if model.iter_has_child(iter_):
                    self._snapshot_walk(model, iter_, parent_group, snapshot)
            iter_ = model.iter_next(iter_)

    def _has_invalid_nesting(self, model, parent_iter) -> bool:
        """Return True if any non-group row has children (invalid state)."""
        iter_ = model.iter_children(parent_iter)
        while iter_:
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if not is_group and model.iter_has_child(iter_):
                return True
            if model.iter_has_child(iter_):
                if self._has_invalid_nesting(model, iter_):
                    return True
            iter_ = model.iter_next(iter_)
        return False

    def _validate_move(self) -> bool:
        """Check that every item is still under the same parent as before DnD."""
        if self._pre_dnd_snapshot is None:
            return True

        # Reject if any non-group row ended up with children
        if self._has_invalid_nesting(self.store, None):
            return False

        current = {"connections": {}, "groups": {}}
        self._snapshot_walk(self.store, None, "", current)

        old = self._pre_dnd_snapshot
        # All original connections must still be present
        if set(current["connections"].keys()) != set(old["connections"].keys()):
            return False
        # Every connection must still be in the same parent group
        for conn_id, parent in current["connections"].items():
            if conn_id in old["connections"] and old["connections"][conn_id] != parent:
                return False
        # Every group must still be under the same parent
        for gpath, parent in current["groups"].items():
            if gpath in old["groups"] and old["groups"][gpath] != parent:
                return False
        return True

    def _sync_order_from_store(self):
        """Read the current tree order and persist it to ConnectionManager."""
        self._pending_sync = False

        if not self._validate_move():
            # Invalid move (cross-group or nesting under non-group) — revert
            self.refresh()
            return False

        ordered_ids = []
        group_order = []
        self._collect_order(self.store, None, ordered_ids, group_order)

        self.connection_manager.reorder_connections(ordered_ids)
        if group_order:
            self.connection_manager.reorder_groups(group_order)

        # Update baseline snapshot for next DnD
        self._pre_dnd_snapshot = self._take_snapshot()
        return False

    def _collect_order(self, model, parent_iter, ordered_ids, group_order):
        """Walk the tree in display order collecting connection IDs and group paths."""
        iter_ = model.iter_children(parent_iter)
        while iter_:
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                group_path = model.get_value(iter_, COL_GROUP_PATH)
                if group_path:
                    group_order.append(group_path)
                self._collect_order(model, iter_, ordered_ids, group_order)
            else:
                conn_id = model.get_value(iter_, COL_CONNECTION_ID)
                if conn_id:
                    ordered_ids.append(conn_id)
            iter_ = model.iter_next(iter_)

    # --- Signal handlers ---

    def _on_row_activated(self, tree_view, path, column):
        """Handle double-click on a row."""
        iter_ = self.store.get_iter(path)
        is_group = self.store.get_value(iter_, COL_IS_GROUP)

        if is_group:
            # Toggle expand/collapse
            if tree_view.row_expanded(path):
                tree_view.collapse_row(path)
            else:
                tree_view.expand_row(path, False)
        else:
            conn_id = self.store.get_value(iter_, COL_CONNECTION_ID)
            if conn_id:
                self.emit("connect-requested", conn_id)

    def _on_right_click(self, gesture, n_press, x, y):
        """Show context menu on right-click using a plain Gtk.Popover."""
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))

        # Build list of (label, callback) items
        items: list[tuple[str, callable]] = []

        if path_info:
            path, column, cell_x, cell_y = path_info
            self.tree_view.get_selection().select_path(path)

            iter_ = self.store.get_iter(path)
            is_group = self.store.get_value(iter_, COL_IS_GROUP)
            conn_id = self.store.get_value(iter_, COL_CONNECTION_ID)

            if is_group:
                items = [
                    ("Add Connection Here", lambda _: self.emit("add-requested")),
                    ("Add Subgroup", lambda _: self.emit("add-group-requested")),
                    ("Delete Group", lambda _: self._delete_selected_group()),
                ]
            else:
                items = [
                    ("Connect", lambda _, cid=conn_id: self.emit("connect-requested", cid)),
                    (None, None),  # separator
                    ("Edit", lambda _, cid=conn_id: self.emit("edit-requested", cid)),
                    ("Duplicate", lambda _, cid=conn_id: self._duplicate_connection(cid)),
                    ("Delete", lambda _, cid=conn_id: self.emit("delete-requested", cid)),
                ]
        else:
            items = [
                ("Add Connection", lambda _: self.emit("add-requested")),
                ("Add Group", lambda _: self.emit("add-group-requested")),
            ]

        # Build popover with buttons
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        popover = Gtk.Popover()
        popover.set_autohide(True)

        for label, callback in items:
            if label is None:
                box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                continue
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.FILL)
            # Close popover first, then fire the action
            def make_handler(cb, pop):
                def handler(b):
                    pop.popdown()
                    cb(b)
                return handler
            btn.connect("clicked", make_handler(callback, popover))
            box.append(btn)

        popover.set_child(box)
        popover.set_parent(self._scrolled)

        # Translate coordinates from tree_view to scrolled window
        result = self.tree_view.translate_coordinates(self._scrolled, x, y)
        if result is not None:
            sx, sy = result
        else:
            sx, sy = x, y
        rect = Gdk.Rectangle()
        rect.x = int(sx)
        rect.y = int(sy)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _delete_selected_group(self):
        """Delete the selected group."""
        group_path = self.get_selected_group_path()
        if group_path:
            self.connection_manager.delete_group(group_path, delete_connections=False)
            self.refresh()

    def _duplicate_connection(self, conn_id: str):
        """Duplicate a connection."""
        conn = self.connection_manager.get_connection(conn_id)
        if conn:
            clone = conn.clone()
            self.connection_manager.add_connection(clone)
            self.refresh()

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        self._search_text = entry.get_text().lower()
        self._apply_search_filter()

    def _apply_search_filter(self):
        """Show/hide rows based on the current search text.

        Uses the COL_VISIBLE column to toggle cell renderer visibility.
        Groups are visible if they or any descendant matches.
        When searching, all matching rows are expanded so they are visible.
        """
        if not self._search_text:
            # No search — make everything visible
            self._set_all_visible(self.store, None, True)
            self.tree_view.expand_all()
            return

        # Walk the tree bottom-up: a row is visible if it matches
        # or if any child is visible.
        self._update_visibility(self.store, None)
        self.tree_view.expand_all()

    def _set_all_visible(self, model, parent_iter, visible):
        """Set visibility on all rows."""
        iter_ = model.iter_children(parent_iter)
        while iter_:
            model.set_value(iter_, COL_VISIBLE, visible)
            self._set_all_visible(model, iter_, visible)
            iter_ = model.iter_next(iter_)

    def _update_visibility(self, model, parent_iter) -> bool:
        """Update COL_VISIBLE for search. Returns True if any child is visible."""
        any_visible = False
        iter_ = model.iter_children(parent_iter)
        while iter_:
            name = model.get_value(iter_, COL_DISPLAY_NAME) or ""
            is_group = model.get_value(iter_, COL_IS_GROUP)

            matches = self._search_text in name.lower()

            if is_group:
                # A group is visible if it matches or any descendant matches
                child_visible = self._update_visibility(model, iter_)
                visible = matches or child_visible
            else:
                visible = matches

            model.set_value(iter_, COL_VISIBLE, visible)
            if visible:
                any_visible = True
            iter_ = model.iter_next(iter_)
        return any_visible
