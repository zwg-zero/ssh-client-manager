"""
Main application window.

Combines the sidebar (connection tree), terminal panel (split tabs),
toolbar, and menu into the main GTK4/libadwaita window.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Vte', '3.91')

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, GObject, Vte

from .config import Config
from .connection import Connection, ConnectionManager
from .credential_store import CredentialStore
from .ssh_handler import SSHHandler
from .terminal_widget import TerminalWidget
from .terminal_panel import TerminalPanel
from .sidebar import Sidebar
from .cluster_window import ClusterWindow
from .connection_dialog import ConnectionDialog
from .preferences_dialog import PreferencesDialog


class MainWindow(Adw.ApplicationWindow):
    """
    The main application window.

    Layout:
    ┌──────────────────────────────────────────────┐
    │ HeaderBar  [≡] [+Local] [SplitH] [SplitV]   │
    │            [Unsplit] [Cluster] [Prefs] [☰]   │
    ├──────────┬───────────────────────────────────┤
    │ Sidebar  │ TerminalPanel                     │
    │ ┌──────┐ │ ┌─────────────┬─────────────────┐ │
    │ │Search│ │ │ Tab1  Tab2  │ Tab3  Tab4       │ │
    │ ├──────┤ │ ├─────────────┼─────────────────┤ │
    │ │Group1│ │ │             │                 │ │
    │ │ Host1│ │ │  Terminal   │  Terminal        │ │
    │ │ Host2│ │ │             │                 │ │
    │ │Group2│ │ │             │                 │ │
    │ │ Host3│ │ └─────────────┴─────────────────┘ │
    └──────────┴───────────────────────────────────┘
    """

    def __init__(self, application: Adw.Application, config: Config):
        super().__init__(
            application=application,
            title="SSH Client Manager",
            default_width=config["window_width"],
            default_height=config["window_height"],
        )

        self.config = config
        self.connection_manager = ConnectionManager()
        self.credential_store = CredentialStore()
        self.ssh_handler = SSHHandler(self.credential_store)

        # Track pending askpass cleanup timers by terminal id
        self._askpass_cleanup_timers: dict[int, int] = {}  # id(terminal) -> GLib timer id
        self._askpass_script_paths: dict[int, tuple[str, str]] = {}  # id(terminal) -> (script_path, conn_id)

        # Cluster mode state
        self._cluster_mode = False
        self._cluster_window: ClusterWindow | None = None

        self._setup_actions()
        self._build_ui()
        self._connect_signals()
        self._setup_keyboard_shortcuts()

        # Add a welcome local terminal on start if no connections open
        GLib.idle_add(self._open_initial_terminal)

    def _setup_actions(self):
        """Register window-level actions."""
        actions = {
            "new-connection": self._on_new_connection,
            "new-local": self._on_new_local_terminal,
            "edit-ssh-config": self._on_edit_ssh_config,
            "connect-selected": self._on_connect_selected,
            "preferences": self._on_preferences,
            "split-h": self._on_split_horizontal,
            "split-v": self._on_split_vertical,
            "unsplit": self._on_unsplit,
            "cluster-toggle": self._on_cluster_toggle,
            "close-tab": self._on_close_tab,
            "next-tab": self._on_next_tab,
            "prev-tab": self._on_prev_tab,
            "toggle-sidebar": self._on_toggle_sidebar,
            "search-terminal": self._on_search_terminal,
            "import-connections": self._on_import_connections,
            "export-connections": self._on_export_connections,
            "about": self._on_about,
            "quit": lambda *_: self.close(),
        }

        action_group = Gio.SimpleActionGroup()
        for name, callback in actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            action_group.add_action(action)

        self.insert_action_group("win", action_group)

        # Sidebar actions
        sidebar_group = Gio.SimpleActionGroup()
        sidebar_actions = {
            "connect": self._on_sidebar_connect,
            "edit": self._on_sidebar_edit,
            "delete": self._on_sidebar_delete,
            "duplicate": self._on_sidebar_duplicate,
            "add-connection": lambda *_: self._on_new_connection(None, None),
            "add-group": lambda *_: self._on_add_group(),
            "add-subgroup": lambda *_: self._on_add_group(),
            "delete-group": self._on_delete_group,
        }
        for name, callback in sidebar_actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            sidebar_group.add_action(action)

        self.insert_action_group("sidebar", sidebar_group)

        # Terminal context menu actions
        term_group = Gio.SimpleActionGroup()
        term_actions = {
            "copy": lambda *_: self._active_terminal_action("copy_clipboard"),
            "paste": lambda *_: self._active_terminal_action("paste_clipboard"),
            "select-all": lambda *_: self._active_terminal_action("select_all"),
            "reset": lambda *_: self._active_terminal_action("reset_terminal"),
            "clear": lambda *_: self._active_terminal_action("reset_terminal", True),
        }
        for name, callback in term_actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            term_group.add_action(action)

        self.insert_action_group("term", term_group)

    def _build_ui(self):
        """Build the complete window UI."""
        # Main vertical layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # --- Header Bar ---
        self.header_bar = Adw.HeaderBar()
        self._build_header_bar()
        main_box.append(self.header_bar)

        # --- Main content: Sidebar + Terminal Panel ---
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_vexpand(True)
        self.paned.set_wide_handle(True)
        self.paned.set_position(self.config["sidebar_width"])

        # Sidebar
        saved_expanded = self.config.get("sidebar_expanded_groups", [])
        self.sidebar = Sidebar(self.connection_manager, self.credential_store,
                               initial_expanded_groups=saved_expanded)

        # Terminal panel
        self.terminal_panel = TerminalPanel(self.config)

        self.paned.set_start_child(self.sidebar)
        self.paned.set_end_child(self.terminal_panel)

        # Keep the sidebar from being resizable to collapse
        self.paned.set_shrink_start_child(False)
        self.paned.set_shrink_end_child(False)

        main_box.append(self.paned)

        # --- Status bar ---
        self.status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_bar.set_margin_start(8)
        self.status_bar.set_margin_end(8)
        self.status_bar.set_margin_top(2)
        self.status_bar.set_margin_bottom(2)
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_hexpand(True)
        self.status_bar.append(self.status_label)

        self.tab_count_label = Gtk.Label(label="0 tabs")
        self.tab_count_label.add_css_class("dim-label")
        self.status_bar.append(self.tab_count_label)

        main_box.append(self.status_bar)

        self.set_content(main_box)

    def _build_header_bar(self):
        """Build the header bar with buttons and menus."""
        header = self.header_bar

        # Left side: Sidebar toggle + New Local
        btn_sidebar = Gtk.Button(icon_name="sidebar-show-symbolic")
        btn_sidebar.set_tooltip_text("Toggle Sidebar (F9)")
        btn_sidebar.set_action_name("win.toggle-sidebar")
        header.pack_start(btn_sidebar)

        btn_local = Gtk.Button(icon_name="utilities-terminal-symbolic")
        btn_local.set_tooltip_text("New Local Terminal")
        btn_local.set_action_name("win.new-local")
        header.pack_start(btn_local)

        # Split buttons
        btn_split_h = Gtk.Button(icon_name="object-flip-horizontal-symbolic")
        btn_split_h.set_tooltip_text("Split Horizontally")
        btn_split_h.set_action_name("win.split-h")
        header.pack_start(btn_split_h)

        btn_split_v = Gtk.Button(icon_name="object-flip-vertical-symbolic")
        btn_split_v.set_tooltip_text("Split Vertically")
        btn_split_v.set_action_name("win.split-v")
        header.pack_start(btn_split_v)

        btn_unsplit = Gtk.Button(icon_name="view-restore-symbolic")
        btn_unsplit.set_tooltip_text("Unsplit All")
        btn_unsplit.set_action_name("win.unsplit")
        header.pack_start(btn_unsplit)

        # Right side: Cluster, Search, Menu
        btn_cluster = Gtk.ToggleButton()
        btn_cluster.set_icon_name("network-workgroup-symbolic")
        btn_cluster.set_tooltip_text("Cluster Mode - Send to All Terminals")
        btn_cluster.connect("toggled", self._on_cluster_button_toggled)
        self._cluster_button = btn_cluster
        header.pack_end(btn_cluster)

        # App menu
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Menu")
        menu_button.set_menu_model(self._build_app_menu())
        header.pack_end(menu_button)

    def _build_app_menu(self) -> Gio.Menu:
        """Build the application menu."""
        menu = Gio.Menu()

        section1 = Gio.Menu()
        section1.append("New Connection", "win.new-connection")
        section1.append("New Local Terminal", "win.new-local")
        section1.append("Edit SSH Config File", "win.edit-ssh-config")
        menu.append_section(None, section1)

        section2 = Gio.Menu()
        section2.append("Import Connections", "win.import-connections")
        section2.append("Export Connections", "win.export-connections")
        menu.append_section(None, section2)

        section3 = Gio.Menu()
        section3.append("Preferences", "win.preferences")
        section3.append("About", "win.about")
        menu.append_section(None, section3)

        section4 = Gio.Menu()
        section4.append("Quit", "win.quit")
        menu.append_section(None, section4)

        return menu

    def _connect_signals(self):
        """Connect signals from child widgets."""
        # Sidebar signals
        self.sidebar.connect("connect-requested", self._on_sidebar_connect_by_id)
        self.sidebar.connect("edit-requested",
                             lambda _, cid: self._edit_connection(cid))
        self.sidebar.connect("add-requested",
                             lambda _: self._on_new_connection(None, None))
        self.sidebar.connect("add-group-requested",
                             lambda _: self._on_add_group())
        self.sidebar.connect("delete-requested",
                             self._on_sidebar_delete_by_id)
        self.sidebar.connect("duplicate-group-requested",
                             lambda _, gp: self._on_duplicate_group(gp))

        # Terminal panel signals
        self.terminal_panel.connect("tab-added", self._on_tab_added)
        self.terminal_panel.connect("clone-requested", self._on_clone_requested)
        self.terminal_panel.connect("tab-removed", self._on_tab_removed)
        self.terminal_panel.connect("active-terminal-changed",
                                    self._on_active_terminal_changed)
        self.terminal_panel.connect("terminal-title-changed",
                                    self._on_terminal_title_changed)

        # Handle window close
        self.connect("close-request", self._on_close_request)

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts."""
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    # =====================================================================
    # Connection Operations
    # =====================================================================

    def open_connection(self, connection_id: str):
        """Open a new terminal tab for the given connection."""
        conn = self.connection_manager.get_connection(connection_id)
        if not conn:
            self._set_status(f"Connection not found: {connection_id}")
            return

        # Create terminal widget
        terminal = TerminalWidget(self.config, conn)

        # Build SSH command (parsed from the command field) and environment
        ssh_cmd = self.ssh_handler.build_ssh_command(conn)
        env = self.ssh_handler.build_environment(conn)

        # Extract the askpass script path from the environment for cleanup later
        askpass_script = None
        for var in env:
            if var.startswith("SSH_ASKPASS="):
                askpass_script = var.split("=", 1)[1]
                break

        # Add tab
        title = conn.name or conn.display_name()
        self.terminal_panel.add_tab(terminal, conn, title)

        # Spawn SSH process
        terminal.spawn_command(ssh_cmd, env)

        # Schedule post-login commands
        commands = self.ssh_handler.get_post_login_commands(conn)
        if commands:
            self._schedule_post_login_commands(terminal, commands)

        # Clean up askpass script after delay, keyed by terminal instance
        tid = id(terminal)
        if askpass_script:
            self._askpass_script_paths[tid] = (askpass_script, conn.id)
        self._cancel_askpass_timer(tid)
        timer_id = GLib.timeout_add(15000, self._askpass_timer_fired, tid)
        self._askpass_cleanup_timers[tid] = timer_id

        self._set_status(f"Connected: {conn.name}")

    def open_local_terminal(self):
        """Open a new local shell terminal tab."""
        terminal = TerminalWidget(self.config)
        shell_cmd = SSHHandler.get_local_shell_command()
        self.terminal_panel.add_tab(terminal, None, "Local")
        terminal.spawn_command(shell_cmd)
        self._set_status("Local terminal opened")

    def _schedule_post_login_commands(self, terminal: TerminalWidget,
                                      commands: list[str]):
        """Send post-login commands after detecting a shell prompt.

        Polls the terminal text every 500 ms looking for a recognisable
        shell prompt (``$ ``, ``# ``, ``% ``, ``> ``) on the line where
        the cursor sits.  Polling starts after 1 s to skip the initial
        SSH handshake / banner noise.

        A safety ceiling of 30 s ensures we don't poll forever if the
        prompt pattern is unusual.
        """
        import re

        # Matches typical Bash/Zsh/Fish/Ksh prompts:
        #   [user@host ~]$   user@host:~$   host%   root#   >
        prompt_re = re.compile(r'[\$#%>]\s*$')

        state = {'fired': False, 'polls': 0}
        max_polls = 58  # 1 s initial + up to 58 × 500 ms ≈ 30 s

        def _send_commands():
            """Send the commands with optional inter-command delays."""
            if state['fired']:
                return
            state['fired'] = True

            delay_ms = 200  # tiny gap after prompt detected
            for cmd in commands:
                if cmd.startswith("##D="):
                    try:
                        delay_ms += int(cmd[4:])
                    except ValueError:
                        pass
                    continue
                GLib.timeout_add(
                    delay_ms,
                    lambda t=terminal, c=cmd: (t.feed_child(c + "\n"), False)[1]
                )
                delay_ms += 200

        def _poll_for_prompt():
            """Check whether the terminal is showing a shell prompt."""
            if state['fired']:
                return False  # stop polling

            state['polls'] += 1

            # Safety ceiling – send anyway
            if state['polls'] > max_polls:
                _send_commands()
                return False

            try:
                vte = terminal.vte
                col, row = vte.get_cursor_position()
                # Read the cursor line and the one above (prompt may span)
                text = vte.get_text_range_format(
                    Vte.Format.TEXT, max(0, row - 1), 0, row, 300
                )
                if isinstance(text, tuple):
                    text = text[0]
                if text and prompt_re.search(text.rstrip('\n')):
                    _send_commands()
                    return False
            except Exception:
                pass

            # Fallback: scan the last non-empty line of all visible text
            try:
                text = terminal.vte.get_text_format(Vte.Format.TEXT)
                if isinstance(text, tuple):
                    text = text[0]
                if text:
                    lines = text.rstrip().split('\n')
                    if lines and prompt_re.search(lines[-1]):
                        _send_commands()
                        return False
            except Exception:
                pass

            return True  # keep polling

        # Start polling after 1 s, then every 500 ms
        GLib.timeout_add(1000, lambda: (GLib.timeout_add(500, _poll_for_prompt), False)[1])

    # =====================================================================
    # Action Handlers
    # =====================================================================

    def _on_new_connection(self, action, param):
        """Open the new connection dialog."""
        dialog = ConnectionDialog(
            self, self.connection_manager,
            self.credential_store
        )
        dialog.connect("connection-saved", self._on_connection_saved)
        dialog.present()

    def _on_new_local_terminal(self, action, param):
        """Open a new local terminal."""
        self.open_local_terminal()

    def _on_edit_ssh_config(self, action, param):
        """Open a local terminal and run the SSH config edit command."""
        cmd = self.config["ssh_config_edit_command"].strip()
        if not cmd:
            cmd = "vim ~/.ssh/config"
        terminal = TerminalWidget(self.config)
        shell_cmd = SSHHandler.get_local_shell_command()
        self.terminal_panel.add_tab(terminal, None, "SSH Config")
        terminal.spawn_command(shell_cmd)
        # Send the edit command shortly after the shell starts
        GLib.timeout_add(200, lambda: (terminal.feed_child(cmd + "\n"), False)[1])
        self._set_status("Editing SSH config file")

    def _on_connect_selected(self, action, param):
        """Connect to the selected connection in sidebar."""
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self.open_connection(conn_id)

    def _on_preferences(self, action, param):
        """Open preferences dialog."""
        dialog = PreferencesDialog(self, self.config)
        dialog.present()

    def _on_split_horizontal(self, action, param):
        """Split the terminal area horizontally."""
        self.terminal_panel.split(Gtk.Orientation.HORIZONTAL)

    def _on_split_vertical(self, action, param):
        """Split the terminal area vertically."""
        self.terminal_panel.split(Gtk.Orientation.VERTICAL)

    def _on_unsplit(self, action, param):
        """Remove all splits."""
        self.terminal_panel.unsplit()

    def _on_cluster_toggle(self, action, param):
        """Toggle cluster mode (keyboard shortcut)."""
        new_state = not self._cluster_mode
        self._cluster_button.set_active(new_state)

    def _on_cluster_button_toggled(self, button):
        """Open or close the cluster window."""
        self._cluster_mode = button.get_active()
        if self._cluster_mode:
            self._open_cluster_window()
        else:
            self._close_cluster_window()

    def _open_cluster_window(self):
        """Open the cluster popup window."""
        if self._cluster_window is not None:
            self._cluster_window.present()
            return
        self._cluster_window = ClusterWindow(self, self.terminal_panel)
        self._cluster_window.connect(
            "close-request", self._on_cluster_window_closed
        )
        self._cluster_window.present()

    def _close_cluster_window(self):
        """Close the cluster popup window and clear highlights."""
        if self._cluster_window is not None:
            self._cluster_window.close()
            self._cluster_window = None
        self.terminal_panel.clear_cluster_highlights()

    def _on_cluster_window_closed(self, *_):
        """Called when the user closes the cluster window directly."""
        self._cluster_window = None
        self._cluster_mode = False
        self._cluster_button.set_active(False)
        return False  # allow default close

    def _on_close_tab(self, action, param):
        """Close the current tab."""
        self.terminal_panel.close_current_tab()

    def _on_next_tab(self, action, param):
        """Switch to next tab."""
        self.terminal_panel.next_tab()

    def _on_prev_tab(self, action, param):
        """Switch to previous tab."""
        self.terminal_panel.prev_tab()

    def _on_toggle_sidebar(self, action, param):
        """Toggle sidebar visibility."""
        visible = self.sidebar.get_visible()
        self.sidebar.set_visible(not visible)

    def _on_search_terminal(self, action, param):
        """Open search in current terminal."""
        terminal = self.terminal_panel.focused_terminal
        if terminal:
            # Create inline search bar
            self._show_search_bar(terminal)

    def _on_import_connections(self, action, param):
        """Import connections from file."""
        try:
            dialog = Gtk.FileDialog()
            dialog.set_title("Import Connections")
            dialog.open(self, None, self._on_import_file_chosen)
        except Exception:
            chooser = Gtk.FileChooserNative(
                title="Import Connections",
                transient_for=self,
                action=Gtk.FileChooserAction.OPEN,
            )
            chooser.connect("response", self._on_import_chooser_response)
            chooser.show()

    def _on_export_connections(self, action, param):
        """Export connections to file."""
        try:
            dialog = Gtk.FileDialog()
            dialog.set_title("Export Connections")
            dialog.save(self, None, self._on_export_file_chosen)
        except Exception:
            chooser = Gtk.FileChooserNative(
                title="Export Connections",
                transient_for=self,
                action=Gtk.FileChooserAction.SAVE,
            )
            chooser.connect("response", self._on_export_chooser_response)
            chooser.show()

    def _on_import_file_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                with open(file.get_path(), "r") as f:
                    self.connection_manager.import_connections(f.read())
                self.sidebar.refresh()
                self._set_status("Connections imported")
        except Exception as e:
            self._set_status(f"Import failed: {e}")

    def _on_export_file_chosen(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if file:
                data = self.connection_manager.export_connections()
                with open(file.get_path(), "w") as f:
                    f.write(data)
                self._set_status("Connections exported")
        except Exception as e:
            self._set_status(f"Export failed: {e}")

    def _on_import_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            if file:
                try:
                    with open(file.get_path(), "r") as f:
                        self.connection_manager.import_connections(f.read())
                    self.sidebar.refresh()
                    self._set_status("Connections imported")
                except Exception as e:
                    self._set_status(f"Import failed: {e}")

    def _on_export_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            if file:
                data = self.connection_manager.export_connections()
                try:
                    with open(file.get_path(), "w") as f:
                        f.write(data)
                    self._set_status("Connections exported")
                except Exception as e:
                    self._set_status(f"Export failed: {e}")

    def _on_about(self, action, param):
        """Show about dialog."""
        try:
            about = Adw.AboutWindow(
                transient_for=self,
                application_name="SSH Client Manager",
                application_icon="utilities-terminal",
                version="1.0.0",
                developer_name="SSH Client Manager Contributors",
                license_type=Gtk.License.GPL_3_0,
                comments="A modern SSH connection manager with GTK4.\n\n"
                         "Features:\n"
                         "- Split terminals (horizontal/vertical)\n"
                         "- Encrypted credential storage\n"
                         "- Group management\n"
                         "- Cluster mode\n"
                         "- No expect dependency",
                website="https://github.com/ssh-client-manager",
            )
            about.present()
        except TypeError:
            # Older Adw fallback
            dialog = Gtk.AboutDialog(
                transient_for=self,
                modal=True,
                program_name="SSH Client Manager",
                version="1.0.0",
                license_type=Gtk.License.GPL_3_0,
            )
            dialog.present()

    # =====================================================================
    # Sidebar Action Handlers
    # =====================================================================

    def _on_sidebar_connect(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self.open_connection(conn_id)

    def _on_sidebar_connect_by_id(self, sidebar, conn_id):
        self.open_connection(conn_id)

    def _on_sidebar_edit(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self._edit_connection(conn_id)

    def _on_sidebar_delete(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self._delete_connection(conn_id)

    def _on_sidebar_delete_by_id(self, sidebar, conn_id):
        """Handle delete-requested signal from sidebar right-click menu."""
        if conn_id:
            self._delete_connection(conn_id)

    def _delete_connection(self, conn_id):
        """Delete a connection by ID."""
        self.connection_manager.delete_connection(conn_id)
        self.credential_store.delete_credentials(conn_id)
        self.sidebar.refresh()
        self._set_status("Connection deleted")

    def _on_sidebar_duplicate(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            conn = self.connection_manager.get_connection(conn_id)
            if conn:
                new_conn = conn.clone()
                self.connection_manager.add_connection(new_conn)
                # Copy credentials
                password = self.credential_store.get_password(conn_id)
                if password:
                    self.credential_store.store_password(new_conn.id, password)
                pp1 = self.credential_store.get_passphrase1(conn_id)
                if pp1:
                    self.credential_store.store_passphrase1(new_conn.id, pp1)
                pp2 = self.credential_store.get_passphrase2(conn_id)
                if pp2:
                    self.credential_store.store_passphrase2(new_conn.id, pp2)
                self.sidebar.refresh()
                self._set_status(f"Connection duplicated: {new_conn.name}")

    def _on_add_group(self):
        """Show a simple dialog to add a new group."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="New Group",
            body="Enter group name (use / for subgroups, e.g. Production/Web):",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        # Add entry
        entry = Gtk.Entry()
        entry.set_placeholder_text("Group Name")
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.set_extra_child(entry)

        dialog.connect("response", lambda d, r: self._on_add_group_response(d, r, entry))
        dialog.present()

    def _on_add_group_response(self, dialog, response, entry):
        if response == "add":
            group_name = entry.get_text().strip()
            if group_name:
                self.connection_manager.add_group(group_name)
                self.sidebar.refresh(expand_group=group_name)
                self._set_status(f"Group added: {group_name}")

    def _on_delete_group(self, action, param):
        group_path = self.sidebar.get_selected_group_path()
        if group_path:
            self.connection_manager.delete_group(group_path, delete_connections=False)
            self.sidebar.refresh()
            self._set_status(f"Group deleted: {group_path}")

    def _on_duplicate_group(self, source_group: str):
        """Show a dialog to duplicate a group with all its connections."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Duplicate Group",
            body=f'Enter a name for the new group (copy of "{source_group}"):',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("duplicate", "Duplicate")
        dialog.set_response_appearance("duplicate", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Target Group Name")
        entry.set_text(f"{source_group} (copy)")
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.set_extra_child(entry)

        dialog.connect("response", lambda d, r: self._on_duplicate_group_response(
            d, r, entry, source_group))
        dialog.present()

    def _on_duplicate_group_response(self, dialog, response, entry, source_group: str):
        """Handle the duplicate group dialog response."""
        if response == "duplicate":
            target_group = entry.get_text().strip()
            if not target_group:
                return

            # Check if target group already exists
            existing_groups = self.connection_manager.get_groups()
            if target_group in existing_groups:
                err = Adw.MessageDialog(
                    transient_for=self,
                    heading="Error",
                    body=f'Group "{target_group}" already exists. Please choose a different name.',
                )
                err.add_response("ok", "OK")
                err.present()
                return

            # Create the target group
            self.connection_manager.add_group(target_group)

            # Copy all connections from source group to target group
            connections = self.connection_manager.get_connections_in_group(source_group)
            for conn in connections:
                clone = conn.clone()
                clone.name = conn.name  # keep original name (clone() appends " (copy)")
                clone.group = target_group
                self.connection_manager.add_connection(clone)

                # Copy credentials to the cloned connection
                if self.credential_store:
                    pw = self.credential_store.get_password(conn.id)
                    if pw:
                        self.credential_store.store_password(clone.id, pw)
                    pp1 = self.credential_store.get_passphrase1(conn.id)
                    if pp1:
                        self.credential_store.store_passphrase1(clone.id, pp1)
                    pp2 = self.credential_store.get_passphrase2(conn.id)
                    if pp2:
                        self.credential_store.store_passphrase2(clone.id, pp2)

            self.sidebar.refresh(expand_group=target_group)
            self._set_status(f'Group duplicated: "{source_group}" → "{target_group}" ({len(connections)} connections)')

    def _edit_connection(self, connection_id: str):
        """Open the edit dialog for a connection."""
        conn = self.connection_manager.get_connection(connection_id)
        if not conn:
            return

        dialog = ConnectionDialog(
            self, self.connection_manager,
            self.credential_store, conn
        )
        dialog.connect("connection-saved", self._on_connection_saved)
        dialog.present()

    def _on_connection_saved(self, dialog, connection):
        """Handle a connection being saved."""
        self.sidebar.refresh()
        self._set_status(f"Connection saved: {connection.name}")

    # =====================================================================
    # Terminal Panel Signal Handlers
    # =====================================================================

    def _on_tab_added(self, panel, terminal):
        self._update_tab_count()
        if self._cluster_window is not None:
            self._cluster_window.refresh()

    def _on_clone_requested(self, panel, connection):
        """Handle clone-requested signal by opening a full new connection tab."""
        self.open_connection(connection.id)

    def _cancel_askpass_timer(self, terminal_id: int):
        """Cancel any pending askpass cleanup timer for a terminal."""
        timer_id = self._askpass_cleanup_timers.pop(terminal_id, None)
        if timer_id is not None:
            GLib.source_remove(timer_id)

    def _askpass_timer_fired(self, terminal_id: int):
        """Callback for the delayed askpass cleanup timer."""
        self._askpass_cleanup_timers.pop(terminal_id, None)
        info = self._askpass_script_paths.pop(terminal_id, None)
        if info:
            script_path, conn_id = info
            self.ssh_handler.cleanup_askpass_script(script_path)
            self.ssh_handler.cleanup_askpass_counter(conn_id)
        return False  # don't repeat

    def _on_tab_removed(self, panel, terminal):
        self._update_tab_count()
        if self._cluster_window is not None:
            self._cluster_window.refresh()
        # Clean up askpass if it was an SSH session
        tid = id(terminal)
        self._cancel_askpass_timer(tid)
        info = self._askpass_script_paths.pop(tid, None)
        if info:
            script_path, conn_id = info
            self.ssh_handler.cleanup_askpass_script(script_path)
            self.ssh_handler.cleanup_askpass_counter(conn_id)

    def _on_active_terminal_changed(self, panel, terminal):
        conn = panel.get_terminal_connection(terminal)
        if conn:
            self._set_status(f"{conn.name or conn.display_name()}")
        else:
            self._set_status("Local terminal")

    def _on_terminal_title_changed(self, panel, terminal, title):
        pass  # Tab label updates are handled in terminal_panel

    # =====================================================================
    # Keyboard Shortcuts
    # =====================================================================

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle global keyboard shortcuts."""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        alt = state & Gdk.ModifierType.ALT_MASK

        # F9: Toggle sidebar
        if keyval == Gdk.KEY_F9:
            self._on_toggle_sidebar(None, None)
            return True

        # Ctrl+Shift shortcuts
        if ctrl and shift:
            if keyval == Gdk.KEY_T:
                self.open_local_terminal()
                return True
            elif keyval == Gdk.KEY_N:
                self._on_new_connection(None, None)
                return True
            elif keyval == Gdk.KEY_D:
                self.terminal_panel.clone_current_tab()
                return True
            elif keyval == Gdk.KEY_H:
                self.terminal_panel.split(Gtk.Orientation.HORIZONTAL)
                return True
            elif keyval == Gdk.KEY_V:
                # Note: Ctrl+Shift+V is paste in the terminal.
                # Use different shortcut for vertical split.
                pass

        # Ctrl+W: Close tab
        if ctrl and keyval == Gdk.KEY_w:
            self.terminal_panel.close_current_tab()
            return True

        # Ctrl+Tab / Ctrl+Shift+Tab: Next/Prev tab
        if ctrl and keyval == Gdk.KEY_Tab:
            if shift:
                self.terminal_panel.prev_tab()
            else:
                self.terminal_panel.next_tab()
            return True

        # Ctrl+F: Search
        if ctrl and keyval == Gdk.KEY_f:
            self._on_search_terminal(None, None)
            return True

        # Alt+1-9: Switch to tab by number
        if alt:
            num = keyval - Gdk.KEY_1
            if 0 <= num <= 8:
                self.terminal_panel.switch_to_tab(num)
                return True

        return False

    # =====================================================================
    # Search
    # =====================================================================

    def _show_search_bar(self, terminal: TerminalWidget):
        """Show an inline search bar for the terminal."""
        # Check if we already have a search bar
        search_bar = getattr(self, '_search_revealer', None)
        if search_bar and search_bar.get_reveal_child():
            search_bar.set_reveal_child(False)
            return

        # Create search bar
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        search_entry = Gtk.SearchEntry()
        search_entry.set_hexpand(True)
        search_entry.set_placeholder_text("Search terminal...")
        hbox.append(search_entry)

        btn_prev = Gtk.Button(icon_name="go-up-symbolic")
        btn_prev.add_css_class("flat")
        hbox.append(btn_prev)

        btn_next = Gtk.Button(icon_name="go-down-symbolic")
        btn_next.add_css_class("flat")
        hbox.append(btn_next)

        btn_close = Gtk.Button(icon_name="window-close-symbolic")
        btn_close.add_css_class("flat")
        btn_close.connect("clicked", lambda _: revealer.set_reveal_child(False))
        hbox.append(btn_close)

        # Connect search
        search_entry.connect("search-changed",
                             lambda e: terminal.search_text(e.get_text()))
        btn_next.connect("clicked",
                         lambda _: terminal.search_text(search_entry.get_text()))
        btn_prev.connect("clicked",
                         lambda _: terminal.search_text(search_entry.get_text(), backward=True))

        revealer.set_child(hbox)

        # Insert at top of terminal panel
        if hasattr(self, '_search_revealer') and self._search_revealer.get_parent():
            self._search_revealer.get_parent().remove(self._search_revealer)

        self.terminal_panel.prepend(revealer)
        self._search_revealer = revealer
        revealer.set_reveal_child(True)
        search_entry.grab_focus()

    # =====================================================================
    # Utilities
    # =====================================================================

    def _open_initial_terminal(self):
        """Open an initial local terminal."""
        self.open_local_terminal()
        return False

    def _set_status(self, text: str):
        """Update the status bar text."""
        self.status_label.set_text(text)

    def _update_tab_count(self):
        """Update the tab count display."""
        count = self.terminal_panel.get_tab_count()
        self.tab_count_label.set_text(f"{count} tab{'s' if count != 1 else ''}")

    def _active_terminal_action(self, method_name: str, *args):
        """Call a method on the active terminal."""
        terminal = self.terminal_panel.focused_terminal
        if terminal:
            method = getattr(terminal, method_name, None)
            if method:
                method(*args)

    def _on_close_request(self, window):
        """Handle window close request."""
        # Save window state
        self.config.set("sidebar_width", self.paned.get_position())
        self.config.set("sidebar_expanded_groups",
                        list(self.sidebar.get_expanded_groups()))
        alloc = self.get_allocation()
        if alloc.width > 0:
            self.config.set("window_width", alloc.width)
        if alloc.height > 0:
            self.config.set("window_height", alloc.height)
        self.config.save()

        # Terminate all child processes in open terminals
        for terminal in self.terminal_panel.get_all_terminals():
            terminal.terminate()

        # Clean up SSH handler
        self.ssh_handler.cleanup_all()

        return False  # Allow close
