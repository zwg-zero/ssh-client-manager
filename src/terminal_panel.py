"""
Terminal panel with tab management and horizontal/vertical splitting.

Adapts gnome-connection-manager's split/tab approach to GTK4:
- Multiple Gtk.Notebook widgets connected via group name for tab DnD
- Gtk.Paned containers for horizontal/vertical splits
- Recursive nesting: Paned can contain Notebook or another Paned
- When a notebook empties, it's removed and the pane collapses
"""

import gi
gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, GLib, GObject, Gdk, Gio, Graphene
from typing import Optional

from .terminal_widget import TerminalWidget
from .connection import Connection
from .config import Config

# Split direction constants
HSPLIT = Gtk.Orientation.HORIZONTAL
VSPLIT = Gtk.Orientation.VERTICAL

# Group name for cross-notebook tab DnD
TAB_GROUP_NAME = "ssh-client-manager-tabs"


class TabLabel(Gtk.Box):
    """
    Custom notebook tab label with title and close button.

    Features:
    - Editable title
    - Close button
    - Right-click context menu
    - Visual state for disconnected terminals (strikethrough)
    """

    __gsignals__ = {
        "close-clicked": (GObject.SignalFlags.RUN_LAST, None, ()),
        "split-horizontal": (GObject.SignalFlags.RUN_LAST, None, ()),
        "split-vertical": (GObject.SignalFlags.RUN_LAST, None, ()),
        "tab-clicked": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, title: str = "Terminal", show_close: bool = True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_halign(Gtk.Align.CENTER)

        # Title label
        self.label = Gtk.Label(label=title)
        self.append(self.label)

        # Close button
        if show_close:
            close_btn = Gtk.Button()
            close_btn.set_has_frame(False)
            close_btn.set_icon_name("window-close-symbolic")
            close_btn.set_valign(Gtk.Align.CENTER)
            close_btn.add_css_class("flat")
            close_btn.add_css_class("circular")
            close_btn.connect("clicked", lambda _: self.emit("close-clicked"))
            self.append(close_btn)

        # Right-click menu
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.add_controller(click)

        # Middle-click to close
        mid_click = Gtk.GestureClick(button=2)
        mid_click.connect("pressed", lambda *_: self.emit("close-clicked"))
        self.add_controller(mid_click)

        # Left-click to refocus terminal (covers clicking the already-active tab)
        left_click = Gtk.GestureClick(button=1)
        left_click.connect("released", lambda *_: self.emit("tab-clicked"))
        self.add_controller(left_click)

        # Track the active popover so we can clean it up
        self._popover: Gtk.PopoverMenu | None = None

    def set_title(self, title: str):
        """Update the tab title."""
        self.label.set_text(title)

    def get_title(self) -> str:
        """Get the current tab title."""
        return self.label.get_text()

    def mark_disconnected(self):
        """Visual indicator that the terminal connection ended."""
        self.label.add_css_class("dim-label")
        attrs = self.label.get_attributes()
        if attrs is None:
            from gi.repository import Pango
            attrs = Pango.AttrList()
        # Would add strikethrough but Pango.AttrList in GTK4 is complex.
        # Use CSS instead.
        self.label.add_css_class("disconnected-tab")

    def mark_active(self):
        """Remove disconnected visual state."""
        self.label.remove_css_class("dim-label")
        self.label.remove_css_class("disconnected-tab")

    def set_selected_for_cluster(self, selected: bool):
        """Highlight if selected for cluster mode."""
        if selected:
            self.add_css_class("cluster-selected")
        else:
            self.remove_css_class("cluster-selected")

    def _on_right_click(self, gesture, n_press, x, y):
        """Show tab context menu."""
        # Clean up any previous popover
        self._cleanup_popover()

        # Find the Notebook ancestor — parenting the popover to the tiny
        # TabLabel causes GTK to compute negative sizes for internal menu
        # GtkImage widgets.  The Notebook has plenty of allocation.
        notebook = self._find_notebook()
        if notebook is None:
            return

        # Convert click coordinates from TabLabel-local → Notebook-local
        src_point = Graphene.Point()
        src_point.x = float(x)
        src_point.y = float(y)
        ok, dest_point = self.compute_point(notebook, src_point)
        if ok:
            nx, ny = dest_point.x, dest_point.y
        else:
            nx, ny = float(x), float(y)

        menu_model = Gio.Menu()

        section1 = Gio.Menu()
        section1.append("Split Horizontally", "panel.split-h")
        section1.append("Split Vertically", "panel.split-v")
        section1.append("Unsplit All", "panel.unsplit")
        menu_model.append_section(None, section1)

        section2 = Gio.Menu()
        section2.append("Reconnect", "panel.reconnect")
        section2.append("Clone Tab", "panel.clone-tab")
        menu_model.append_section(None, section2)

        section3 = Gio.Menu()
        section3.append("Close Tab", "panel.close-tab")
        menu_model.append_section(None, section3)

        popover = Gtk.PopoverMenu(menu_model=menu_model)
        popover.set_has_arrow(False)
        popover.set_parent(notebook)
        popover.set_pointing_to(Gdk.Rectangle(int(nx), int(ny), 1, 1))
        popover.connect("closed", self._on_popover_closed)
        self._popover = popover
        popover.popup()

    def _find_notebook(self):
        """Walk up the widget tree to find the containing Notebook."""
        widget = self.get_parent()
        while widget is not None:
            if isinstance(widget, Gtk.Notebook):
                return widget
            widget = widget.get_parent()
        return None

    def _cleanup_popover(self):
        """Unparent and discard the current popover if any."""
        if self._popover is not None:
            try:
                self._popover.unparent()
            except Exception:
                pass
            self._popover = None

    def _on_popover_closed(self, popover):
        """Schedule popover cleanup after GTK finishes its bookkeeping."""
        GLib.idle_add(self._deferred_popover_cleanup, popover)

    def _deferred_popover_cleanup(self, popover):
        """Unparent the popover now that GTK state accounting is done."""
        try:
            popover.unparent()
        except Exception:
            pass
        if self._popover is popover:
            self._popover = None
        return False


class TerminalPanel(Gtk.Box):
    """
    Manages the terminal area with tabbed notebooks and split panes.

    Architecture (adapted from gnome-connection-manager for GTK4):
    - Starts with a single Gtk.Notebook as the main terminal area
    - Splitting creates a Gtk.Paned that holds the original notebook
      in one pane and a new notebook in the other
    - Notebooks share a group name for tab drag-and-drop between them
    - When a notebook empties (0 tabs), it's auto-removed and
      the Paned collapses
    - Splitting can be nested: Paned → Paned → Notebook, etc.

    Signals:
        tab-added: A new terminal tab was created
        tab-removed: A terminal tab was closed
        active-terminal-changed: The focused terminal changed
    """

    __gsignals__ = {
        "tab-added": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "tab-removed": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "active-terminal-changed": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "terminal-title-changed": (GObject.SignalFlags.RUN_LAST, None, (object, str)),
        "clone-requested": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, config: Config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.config = config

        # The focused notebook (receives new tabs, split source)
        self.focused_notebook: Optional[Gtk.Notebook] = None

        # The currently focused terminal
        self.focused_terminal: Optional[TerminalWidget] = None

        # Map: terminal_widget -> (connection, tab_label, notebook)
        self._terminals: dict[TerminalWidget, tuple] = {}

        # All notebooks in this panel
        self._notebooks: list[Gtk.Notebook] = []

        # Guard flag: suppress signal handlers during bulk reparenting
        self._reorganizing: bool = False

        # Create the initial notebook
        self.main_notebook = self._create_notebook()
        self.focused_notebook = self.main_notebook
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.append(self.main_notebook)

        # Set up panel-level actions
        self._setup_actions()

    def _setup_paned_cursor(self, paned: Gtk.Paned):
        """Defer setting the open-hand cursor on the paned separator until realized."""
        GLib.idle_add(self._apply_paned_cursor, paned)

    def _apply_paned_cursor(self, paned: Gtk.Paned) -> bool:
        # "fleur" (X11 name) = four-directional arrow; fallback chain
        cursor = Gdk.Cursor.new_from_name(
            "fleur", Gdk.Cursor.new_from_name(
                "move", Gdk.Cursor.new_from_name("all-scroll", None)
            )
        )
        child = paned.get_first_child()
        while child is not None:
            if child.get_css_name() == "separator":
                child.set_cursor(cursor)
                break
            child = child.get_next_sibling()
        return False

    def _create_notebook(self) -> Gtk.Notebook:
        """Create a new Gtk.Notebook configured for tab management."""
        nb = Gtk.Notebook()
        nb.set_scrollable(True)
        nb.set_show_border(False)
        nb.set_group_name(TAB_GROUP_NAME)  # Enable cross-notebook DnD
        nb.set_vexpand(True)
        nb.set_hexpand(True)

        # Allow tabs to be rearranged and detached
        nb.connect("page-added", self._on_page_added)
        nb.connect("page-removed", self._on_page_removed)
        nb.connect("switch-page", self._on_switch_page)
        nb.connect("create-window", self._on_create_window)

        self._notebooks.append(nb)
        return nb

    def _setup_actions(self):
        """Register action handlers for this panel."""
        action_group = Gio.SimpleActionGroup()

        actions = {
            "split-h": lambda *_: self.split(HSPLIT),
            "split-v": lambda *_: self.split(VSPLIT),
            "unsplit": lambda *_: self.unsplit(),
            "close-tab": lambda *_: self.close_current_tab(),
            "reconnect": lambda *_: self.reconnect_current(),
            "clone-tab": lambda *_: self.clone_current_tab(),
        }

        for name, callback in actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            action_group.add_action(action)

        self.insert_action_group("panel", action_group)

    # =====================================================================
    # Tab Management
    # =====================================================================

    def add_tab(self, terminal: TerminalWidget,
                connection: Optional[Connection] = None,
                title: str = "Terminal",
                notebook: Optional[Gtk.Notebook] = None) -> Gtk.Notebook:
        """
        Add a terminal as a new tab in the specified (or focused) notebook.

        Args:
            terminal: The terminal widget to add
            connection: The connection (if SSH) or None (local shell)
            title: Tab title
            notebook: Target notebook (default: focused_notebook)

        Returns:
            The notebook the tab was added to
        """
        nb = notebook or self.focused_notebook
        if nb is None:
            nb = self.main_notebook

        # Create tab label
        show_close = self.config.get("show_tab_close_button", True)
        tab_label = TabLabel(title=title, show_close=show_close)

        # Connect tab label signals — look up notebook at call time, not capture time
        tab_label.connect("close-clicked",
                          lambda _, t=terminal: self._close_tab_by_lookup(t))
        tab_label.connect("tab-clicked",
                          lambda _, t=terminal: GLib.idle_add(t.grab_focus))

        # Track the terminal
        self._terminals[terminal] = (connection, tab_label, nb)

        # Connect terminal signals
        terminal.connect("title-changed", self._on_terminal_title_changed)
        terminal.connect("child-exited", self._on_terminal_child_exited)

        # Focus tracking — look up notebook from _terminals at call time
        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect("enter", lambda _, t=terminal: self._on_terminal_focused(t))
        terminal.vte.add_controller(focus_ctrl)

        # Add to notebook
        page_num = nb.append_page(terminal, tab_label)
        nb.set_tab_reorderable(terminal, True)
        nb.set_tab_detachable(terminal, True)
        nb.set_current_page(page_num)

        # Focus the terminal
        GLib.idle_add(terminal.grab_focus)

        self.emit("tab-added", terminal)
        return nb

    @staticmethod
    def _clear_root_focus(widget: Gtk.Widget):
        """Clear the window-level focus so no widget owns focus.

        This must be called *before* detaching children from a GtkPaned.
        Without it, GTK's internal focus propagation calls
        ``set_focus_child(nil)`` on the paned and emits a warning.
        """
        root = widget.get_root()
        if root is not None:
            root.set_focus(None)

    def _restore_focus(self):
        """Re-focus the currently-active terminal (or any available one)."""
        # Prefer the terminal we already consider focused
        if (self.focused_terminal is not None
                and self.focused_terminal.get_parent() is not None):
            GLib.idle_add(self.focused_terminal.grab_focus)
            return
        # Fallback: first visible terminal we can find
        for nb in self._notebooks:
            if nb.get_n_pages() > 0:
                child = nb.get_nth_page(nb.get_current_page())
                if isinstance(child, TerminalWidget):
                    self.focused_terminal = child
                    self.focused_notebook = nb
                    GLib.idle_add(child.grab_focus)
                    return

    def _safe_remove_page(self, notebook: Gtk.Notebook, page_num: int):
        """Remove a page safely, avoiding GtkPaned focus warnings.

        Clears the window-level focus when the notebook lives inside a
        GtkPaned, then hides the child and removes the page.  Focus is
        restored afterwards via ``_restore_focus``.
        """
        child = notebook.get_nth_page(page_num)
        if child is not None:
            # If the notebook is inside a paned, clear root focus first
            if isinstance(notebook.get_parent(), Gtk.Paned):
                self._clear_root_focus(notebook)
            child.set_visible(False)
        notebook.remove_page(page_num)
        # Restore focus in an idle callback so the removal is fully complete
        GLib.idle_add(self._restore_focus)

    def _close_tab(self, terminal: TerminalWidget, notebook: Gtk.Notebook):
        """Close a specific tab."""
        page_num = notebook.page_num(terminal)
        if page_num >= 0:
            self._safe_remove_page(notebook, page_num)

    def _close_tab_by_lookup(self, terminal: TerminalWidget):
        """Close a tab, looking up the current notebook from tracking dict."""
        info = self._terminals.get(terminal)
        if info:
            _, _, nb = info
            page_num = nb.page_num(terminal)
            if page_num >= 0:
                self._safe_remove_page(nb, page_num)

    def close_current_tab(self):
        """Close the current tab in the focused notebook."""
        if self.focused_notebook is None:
            return

        nb = self.focused_notebook
        page_num = nb.get_current_page()
        if page_num >= 0:
            self._safe_remove_page(nb, page_num)

    def get_tab_count(self) -> int:
        """Get total number of tabs across all notebooks."""
        return sum(nb.get_n_pages() for nb in self._notebooks)

    def get_all_terminals(self) -> list[TerminalWidget]:
        """Get all terminal widgets."""
        return list(self._terminals.keys())

    def get_terminal_info(self) -> list[tuple[str, 'TerminalWidget']]:
        """Return ``[(title, terminal), ...]`` for every open tab."""
        result = []
        for terminal, (conn, tab_label, nb) in self._terminals.items():
            title = (
                tab_label.get_title()
                if tab_label else
                (conn.name if conn else "Terminal")
            )
            result.append((title, terminal))
        return result

    def set_cluster_highlight(self, terminal: TerminalWidget, active: bool):
        """Toggle visual cluster-selected highlight on a tab label."""
        info = self._terminals.get(terminal)
        if info:
            _, tab_label, _ = info
            if tab_label:
                tab_label.set_selected_for_cluster(active)

    def clear_cluster_highlights(self):
        """Remove cluster highlight from every tab."""
        for terminal in self._terminals:
            self.set_cluster_highlight(terminal, False)

    def get_terminal_connection(self, terminal: TerminalWidget) -> Optional[Connection]:
        """Get the connection associated with a terminal."""
        info = self._terminals.get(terminal)
        return info[0] if info else None

    def next_tab(self):
        """Switch to next tab in focused notebook."""
        if self.focused_notebook:
            nb = self.focused_notebook
            current = nb.get_current_page()
            total = nb.get_n_pages()
            if total > 1:
                nb.set_current_page((current + 1) % total)

    def prev_tab(self):
        """Switch to previous tab in focused notebook."""
        if self.focused_notebook:
            nb = self.focused_notebook
            current = nb.get_current_page()
            total = nb.get_n_pages()
            if total > 1:
                nb.set_current_page((current - 1) % total)

    def switch_to_tab(self, index: int):
        """Switch to tab by index (0-based) in focused notebook."""
        if self.focused_notebook:
            nb = self.focused_notebook
            if 0 <= index < nb.get_n_pages():
                nb.set_current_page(index)

    # =====================================================================
    # Split Management (adapted from gnome-connection-manager)
    # =====================================================================

    def split(self, orientation: Gtk.Orientation):
        """
        Split the focused notebook, creating a new pane.

        Algorithm:
        1. The focused notebook must have >= 2 tabs (need at least one to move)
        2. Create a new Gtk.Paned with the given orientation
        3. Remove the focused notebook from its parent
        4. Place the focused notebook as the start child of the paned
        5. Create a new notebook as the end child
        6. Move the current tab from the old notebook to the new one
        7. Insert the paned where the old notebook was
        """
        nb = self.focused_notebook
        if nb is None:
            return

        # Need at least 2 tabs to split (one stays, one moves)
        if nb.get_n_pages() < 2:
            return

        self._reorganizing = True

        # Get the current page to move
        current_page = nb.get_current_page()
        if current_page < 0:
            return

        child = nb.get_nth_page(current_page)
        if child is None:
            return

        tab_label = nb.get_tab_label(child)

        # Get notebook's parent
        parent = nb.get_parent()

        # Create a new paned
        paned = Gtk.Paned(orientation=orientation)
        paned.set_vexpand(True)
        paned.set_hexpand(True)
        paned.set_wide_handle(True)
        self._setup_paned_cursor(paned)

        # Create new notebook
        new_nb = self._create_notebook()

        # Remove the old notebook from its parent
        if parent == self:
            # It's the top-level child of this TerminalPanel
            self.remove(nb)
            self.append(paned)
        elif isinstance(parent, Gtk.Paned):
            # Clear focus before reparenting inside a paned
            self._clear_root_focus(nb)
            if parent.get_start_child() == nb:
                parent.set_start_child(None)
                parent.set_start_child(paned)
            else:
                parent.set_end_child(None)
                parent.set_end_child(paned)
        else:
            # Unknown parent type, bail
            self._notebooks.remove(new_nb)
            return

        # Place the old notebook in pane 1 (start)
        paned.set_start_child(nb)

        # Remove the current tab from old notebook
        # Keep a Python reference to the child to prevent it from being destroyed
        child_ref = child  # prevent GC during reparenting
        tab_info = self._terminals.get(child)
        self._safe_remove_page(nb, current_page)

        # Place the new notebook in pane 2 (end)
        paned.set_end_child(new_nb)

        # Add the moved tab to the new notebook
        if tab_info:
            conn, old_tab_label, _ = tab_info
            new_tab_label = TabLabel(
                title=old_tab_label.get_title(),
                show_close=self.config.get("show_tab_close_button", True)
            )
            new_tab_label.connect("close-clicked",
                                  lambda _, t=child_ref: self._close_tab_by_lookup(t))
            new_tab_label.connect("tab-clicked",
                                  lambda _, t=child_ref: GLib.idle_add(t.grab_focus))
            self._terminals[child_ref] = (conn, new_tab_label, new_nb)

            new_nb.append_page(child_ref, new_tab_label)
            child_ref.set_visible(True)  # restore after _safe_remove_page
            new_nb.set_tab_reorderable(child_ref, True)
            new_nb.set_tab_detachable(child_ref, True)

        # Set paned position at midpoint
        def set_position():
            if orientation == HSPLIT:
                alloc = paned.get_allocation()
                paned.set_position(alloc.width // 2 if alloc.width > 0 else 400)
            else:
                alloc = paned.get_allocation()
                paned.set_position(alloc.height // 2 if alloc.height > 0 else 300)
            return False

        GLib.idle_add(set_position)

        self._reorganizing = False

        # Focus the new notebook/terminal
        self.focused_notebook = new_nb
        if isinstance(child_ref, TerminalWidget):
            GLib.idle_add(child_ref.grab_focus)

    def unsplit(self):
        """
        Remove all splits, moving all tabs back to the main notebook.

        Finds all notebooks, moves their tabs to the first notebook,
        then rebuilds the layout with just that one notebook.
        """
        if len(self._notebooks) <= 1:
            return

        self._reorganizing = True

        # Collect all tabs from all notebooks
        all_tabs = []
        for nb in self._notebooks:
            for i in range(nb.get_n_pages()):
                child = nb.get_nth_page(i)
                tab_label = nb.get_tab_label(child)
                all_tabs.append((child, tab_label))

        # Remove all pages from all notebooks
        for nb in self._notebooks:
            while nb.get_n_pages() > 0:
                self._safe_remove_page(nb, 0)

        # Remove all children from this panel
        child = self.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child

        # Reset to single notebook
        self._notebooks.clear()
        self.main_notebook = self._create_notebook()
        self.focused_notebook = self.main_notebook
        self.append(self.main_notebook)

        # Add all tabs back
        for child_widget, old_label in all_tabs:
            if isinstance(child_widget, TerminalWidget):
                tab_info = self._terminals.get(child_widget)
                title = old_label.get_title() if hasattr(old_label, 'get_title') else "Terminal"
                if tab_info:
                    conn, _, _ = tab_info

                new_label = TabLabel(
                    title=title,
                    show_close=self.config.get("show_tab_close_button", True)
                )
                new_label.connect("close-clicked",
                                  lambda _, t=child_widget: self._close_tab_by_lookup(t))
                new_label.connect("tab-clicked",
                                  lambda _, t=child_widget: GLib.idle_add(t.grab_focus))
                self._terminals[child_widget] = (
                    tab_info[0] if tab_info else None,
                    new_label,
                    self.main_notebook
                )
                self.main_notebook.append_page(child_widget, new_label)
                child_widget.set_visible(True)  # restore after _safe_remove_page
                self.main_notebook.set_tab_reorderable(child_widget, True)
                self.main_notebook.set_tab_detachable(child_widget, True)

        self._reorganizing = False

    def _check_notebook_empty(self, notebook: Gtk.Notebook):
        """
        Handle a notebook becoming empty after tab removal.

        If the notebook is empty and it's not the last one:
        1. Find its parent Paned
        2. Get the sibling widget
        3. Replace the Paned with the sibling in the widget hierarchy
        """
        if notebook.get_n_pages() > 0:
            return

        if len(self._notebooks) <= 1:
            return  # Don't remove the last notebook

        parent = notebook.get_parent()
        if not isinstance(parent, Gtk.Paned):
            return

        self._reorganizing = True

        # Hide the empty notebook immediately so GTK doesn't try to
        # measure its (now-empty) tab bar during the reparenting below.
        # This avoids "GtkGizmo (tabs) reported min height -3" warnings.
        notebook.set_visible(False)

        # Identify the sibling (the other child of the paned)
        if parent.get_start_child() == notebook:
            sibling = parent.get_end_child()
        else:
            sibling = parent.get_start_child()

        # Clear window-level focus *before* touching the paned children.
        # Otherwise GTK propagates a nil focus through the paned and warns.
        self._clear_root_focus(parent)

        # Detach both children from the paned.
        parent.set_start_child(None)
        parent.set_end_child(None)

        # Replace the paned with the sibling in the paned's parent
        grandparent = parent.get_parent()
        if grandparent == self:
            self.remove(parent)
            self.append(sibling)
        elif isinstance(grandparent, Gtk.Paned):
            if grandparent.get_start_child() == parent:
                grandparent.set_start_child(sibling)
            else:
                grandparent.set_end_child(sibling)

        # Remove the empty notebook from tracking
        if notebook in self._notebooks:
            self._notebooks.remove(notebook)

        # Update focused_notebook
        if self.focused_notebook == notebook:
            self.focused_notebook = self._notebooks[0] if self._notebooks else None

        # Update main_notebook reference if needed
        if self.main_notebook == notebook:
            self.main_notebook = self._notebooks[0] if self._notebooks else None

        self._reorganizing = False

        # Restore keyboard focus to a surviving terminal
        self._restore_focus()

    # =====================================================================
    # Reconnect / Clone
    # =====================================================================

    def reconnect_current(self):
        """
        Reconnect the current terminal's SSH session.
        Re-emits tab-added so the window can re-spawn the SSH process.
        """
        if self.focused_terminal and self.focused_terminal in self._terminals:
            conn, tab_label, nb = self._terminals[self.focused_terminal]
            if conn:
                self.emit("tab-added", self.focused_terminal)

    def clone_current_tab(self):
        """Request cloning the current tab.

        Emits 'clone-requested' with the connection so the window can
        create a fully-functional new tab (including spawning the process).
        For local (no connection) terminals, opens a new local tab directly.
        """
        if self.focused_terminal and self.focused_terminal in self._terminals:
            conn, tab_label, nb = self._terminals[self.focused_terminal]
            if conn:
                self.emit("clone-requested", conn)
            else:
                # Local shell: clone by opening a new local terminal
                new_terminal = TerminalWidget(self.config)
                self.add_tab(new_terminal, None, "Local")
                from .ssh_handler import SSHHandler
                new_terminal.spawn_command(SSHHandler.get_local_shell_command())

    # =====================================================================
    # Cluster Mode
    # =====================================================================

    def send_to_all(self, text: str):
        """Send text to all open terminals (cluster mode)."""
        for terminal in self._terminals:
            terminal.feed_child(text)

    def send_to_selected(self, text: str, terminals: list[TerminalWidget]):
        """Send text to selected terminals."""
        for terminal in terminals:
            terminal.feed_child(text)

    # =====================================================================
    # Signal Handlers
    # =====================================================================

    def _on_page_added(self, notebook, child, page_num):
        """Handle a page being added (including via DnD)."""
        # Ensure the tab bar is visible now that there's content
        notebook.set_show_tabs(True)

        if self._reorganizing:
            return
        # Update terminal tracking if this is a DnD move
        if isinstance(child, TerminalWidget) and child in self._terminals:
            conn, tab_label, old_nb = self._terminals[child]
            if old_nb != notebook:
                self._terminals[child] = (conn, tab_label, notebook)

    def _on_page_removed(self, notebook, child, page_num):
        """Handle a page being removed."""
        # Hide the tab bar immediately when the notebook becomes empty.
        # This prevents GTK from measuring the empty tabs gizmo and
        # reporting "min height -3".
        if notebook.get_n_pages() == 0:
            notebook.set_show_tabs(False)

        if self._reorganizing:
            return
        if isinstance(child, TerminalWidget):
            # Check if it's being moved (DnD) vs actually closed
            # If it has no parent, it's being closed
            GLib.idle_add(self._deferred_page_removed, child, notebook)

    def _deferred_page_removed(self, child, notebook):
        """Deferred cleanup after page removal."""
        if child.get_parent() is None:
            # Terminal was actually closed (not moved via DnD)
            # Terminate the child process first
            if isinstance(child, TerminalWidget):
                child.terminate()
            # Emit tab-removed BEFORE removing from tracking so
            # handlers can still look up connection info.
            self.emit("tab-removed", child)
            if child in self._terminals:
                del self._terminals[child]

        # Check if notebook is now empty
        self._check_notebook_empty(notebook)
        return False

    def _on_switch_page(self, notebook, child, page_num):
        """Handle switching to a different tab."""
        if isinstance(child, TerminalWidget):
            self.focused_terminal = child
            self.focused_notebook = notebook
            self.emit("active-terminal-changed", child)
            GLib.idle_add(child.grab_focus)

    def _on_create_window(self, notebook, page, x, y):
        """
        Handle tab being dragged out of a notebook.

        In GTK4, returning None means the tab stays in the notebook.
        For now, we don't support detaching to new windows.
        """
        return None

    def _on_terminal_focused(self, terminal: TerminalWidget):
        """Track which terminal/notebook has focus (looks up notebook dynamically)."""
        self.focused_terminal = terminal
        info = self._terminals.get(terminal)
        if info:
            self.focused_notebook = info[2]
        self.emit("active-terminal-changed", terminal)

    def _on_terminal_title_changed(self, terminal, title):
        """Update tab label when terminal title changes."""
        if terminal in self._terminals:
            conn, tab_label, nb = self._terminals[terminal]
            if conn:
                # SSH tab: keep the connection name, don't overwrite with
                # VTE's window title (e.g. "user@host:~")
                pass
            elif title:
                # Local shell: use the VTE window title
                tab_label.set_title(title)
            self.emit("terminal-title-changed", terminal, title)

    def _on_terminal_child_exited(self, terminal, status):
        """Handle terminal child process exiting."""
        if terminal in self._terminals:
            conn, tab_label, nb = self._terminals[terminal]
            tab_label.mark_disconnected()
