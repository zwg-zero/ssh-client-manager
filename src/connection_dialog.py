"""
Connection add/edit dialog.

Matches the gnome-connection-manager layout:
  Properties tab: Group, Name, Description, Command (multiline), Passphrase1,
                  Passphrase2, Password, TERM
  Commands tab:   Post-login commands
  Appearance tab: Font, colors
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk, GObject
from typing import Optional

from .connection import Connection, ConnectionManager
from .credential_store import CredentialStore


class ConnectionDialog(Adw.Window):
    """
    Dialog for adding or editing a connection.

    Tabs:
    - Properties: group, name, description, command, credentials, TERM
    - Commands:   post-login commands
    - Appearance: font, bg/fg color overrides
    """

    __gsignals__ = {
        "connection-saved": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, parent: Gtk.Window,
                 connection_manager: ConnectionManager,
                 credential_store: CredentialStore,
                 connection: Optional[Connection] = None):
        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=580,
            default_height=680,
        )

        self.connection_manager = connection_manager
        self.credential_store = credential_store
        self.connection = connection  # None for new, existing for edit

        is_edit = connection is not None
        self.set_title("Edit Connection" if is_edit else "New Connection")

        # ----- Main layout -----
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)
        main_box.append(header)

        # Notebook
        notebook = Gtk.Notebook()
        notebook.set_margin_start(12)
        notebook.set_margin_end(12)
        notebook.set_margin_top(6)
        notebook.set_margin_bottom(12)
        notebook.set_vexpand(True)

        notebook.append_page(self._build_properties_page(), Gtk.Label(label="Properties"))
        notebook.append_page(self._build_commands_page(), Gtk.Label(label="Commands"))
        notebook.append_page(self._build_appearance_page(), Gtk.Label(label="Appearance"))

        main_box.append(notebook)
        self.set_content(main_box)

        # Populate if editing
        if connection:
            self._populate_fields(connection)

    # -----------------------------------------------------------------
    # Page builders
    # -----------------------------------------------------------------

    def _build_properties_page(self) -> Gtk.Widget:
        """Build the main connection settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_start(8)
        box.set_margin_end(8)

        # Group combo
        self.combo_group = Gtk.ComboBoxText()
        self.combo_group.append_text("")  # Root (no group)
        for group in self.connection_manager.get_groups():
            self.combo_group.append_text(group)
        self.combo_group.set_hexpand(True)
        self.combo_group.connect("changed", self._on_group_combo_changed)
        box.append(self._labeled("Group:", self.combo_group))

        # New group entry
        self.entry_group = Gtk.Entry()
        self.entry_group.set_placeholder_text("Group / Subgroup")
        box.append(self._labeled("New Group:", self.entry_group))

        # Name
        self.entry_name = Gtk.Entry()
        self.entry_name.set_placeholder_text("My Server")
        box.append(self._labeled("Name:", self.entry_name))

        # Description
        self.entry_description = Gtk.Entry()
        self.entry_description.set_placeholder_text("Optional description")
        box.append(self._labeled("Description:", self.entry_description))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Command (multiline)
        label_cmd = Gtk.Label(label="SSH Command (the full command line, e.g.\n"
                              "  ssh -o \"ServerAliveInterval=240\" user@host )")
        label_cmd.set_xalign(0)
        label_cmd.add_css_class("dim-label")
        box.append(label_cmd)

        self.textview_command = Gtk.TextView()
        self.textview_command.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview_command.set_monospace(True)
        self.textview_command.set_left_margin(8)
        self.textview_command.set_right_margin(8)
        self.textview_command.set_top_margin(4)
        self.textview_command.set_bottom_margin(4)

        scrolled_cmd = Gtk.ScrolledWindow()
        scrolled_cmd.set_min_content_height(72)
        scrolled_cmd.set_max_content_height(200)
        scrolled_cmd.set_propagate_natural_height(True)
        scrolled_cmd.set_child(self.textview_command)
        scrolled_cmd.add_css_class("card")
        box.append(scrolled_cmd)

        # Dynamically resize the command area as content changes
        self.textview_command.get_buffer().connect(
            "changed", lambda buf: self._resize_command_area(scrolled_cmd)
        )
        self._scrolled_cmd = scrolled_cmd

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Passphrase1
        self.entry_passphrase1 = Gtk.PasswordEntry()
        self.entry_passphrase1.set_show_peek_icon(True)
        box.append(self._labeled("Passphrase 1:", self.entry_passphrase1))

        # Passphrase2
        self.entry_passphrase2 = Gtk.PasswordEntry()
        self.entry_passphrase2.set_show_peek_icon(True)
        box.append(self._labeled("Passphrase 2:", self.entry_passphrase2))

        # Password
        self.entry_password = Gtk.PasswordEntry()
        self.entry_password.set_show_peek_icon(True)
        box.append(self._labeled("Password:", self.entry_password))

        hint = Gtk.Label(
            label="Credentials are encrypted and injected via SSH_ASKPASS.\n"
                  "Leave blank to be prompted interactively."
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        box.append(hint)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(box)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return scrolled

    def _build_commands_page(self) -> Gtk.Widget:
        """Build the post-login commands page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_start(8)
        box.set_margin_end(8)

        label = Gtk.Label(
            label="Commands to execute after SSH login (one per line).\n"
                  "Use ##D=1000 to insert a delay in milliseconds."
        )
        label.set_xalign(0)
        label.set_wrap(True)
        label.add_css_class("dim-label")
        box.append(label)

        self.textview_commands = Gtk.TextView()
        self.textview_commands.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview_commands.set_monospace(True)
        self.textview_commands.set_left_margin(8)
        self.textview_commands.set_right_margin(8)
        self.textview_commands.set_top_margin(4)
        self.textview_commands.set_bottom_margin(4)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self.textview_commands)
        scrolled.add_css_class("card")
        box.append(scrolled)

        return box

    def _build_appearance_page(self) -> Gtk.Widget:
        """Build the appearance settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_start(8)
        box.set_margin_end(8)

        hint = Gtk.Label(
            label="Override default terminal appearance for this connection.\n"
                  "Leave defaults to use global settings."
        )
        hint.set_wrap(True)
        hint.set_xalign(0)
        hint.add_css_class("dim-label")
        box.append(hint)

        # Font
        self.entry_font = Gtk.Entry()
        self.entry_font.set_placeholder_text("Monospace 11")
        box.append(self._labeled("Font:", self.entry_font))

        # Background color – use a check button to opt-in
        self.chk_bg_color = Gtk.CheckButton(label="Override background")
        self.color_bg = Gtk.ColorButton()
        bg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bg_row.append(self.chk_bg_color)
        bg_row.append(self.color_bg)
        self.color_bg.set_sensitive(False)
        self.chk_bg_color.connect("toggled", lambda c: self.color_bg.set_sensitive(c.get_active()))
        box.append(bg_row)

        # Foreground color
        self.chk_fg_color = Gtk.CheckButton(label="Override foreground")
        self.color_fg = Gtk.ColorButton()
        fg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fg_row.append(self.chk_fg_color)
        fg_row.append(self.color_fg)
        self.color_fg.set_sensitive(False)
        self.chk_fg_color.connect("toggled", lambda c: self.color_fg.set_sensitive(c.get_active()))
        box.append(fg_row)

        # TERM type
        self.entry_term = Gtk.Entry()
        self.entry_term.set_placeholder_text("xterm-256color")
        box.append(self._labeled("TERM:", self.entry_term))

        return box

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _labeled(label_text: str, widget: Gtk.Widget) -> Gtk.Box:
        """Create a horizontal box with a label and widget."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_bottom(4)
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(1)
        lbl.set_size_request(120, -1)
        lbl.add_css_class("dim-label")
        row.append(lbl)
        widget.set_hexpand(True)
        row.append(widget)
        return row

    def _on_group_combo_changed(self, combo):
        """When a group is selected from combo, clear the new group entry."""
        if combo.get_active() > 0:
            self.entry_group.set_text("")

    def _resize_command_area(self, scrolled_window):
        """Dynamically adjust the command text area height to fit content."""
        buf = self.textview_command.get_buffer()
        line_count = buf.get_line_count()
        # Approximate line height: use font metrics or a reasonable default
        # A monospace font at default size is roughly 20px per line
        line_height = 20
        padding = 12  # top + bottom margins/padding
        natural = line_count * line_height + padding
        min_h = 72
        max_h = 200
        target = max(min_h, min(natural, max_h))
        scrolled_window.set_min_content_height(target)

    # -----------------------------------------------------------------
    # Populate (edit mode)
    # -----------------------------------------------------------------

    def _populate_fields(self, conn: Connection):
        """Fill form fields from an existing connection."""
        self.entry_name.set_text(conn.name or "")
        self.entry_description.set_text(conn.description or "")

        # Group
        if conn.group:
            found = False
            for i in range(100):
                try:
                    self.combo_group.set_active(i)
                    if self.combo_group.get_active_text() == conn.group:
                        found = True
                        break
                except Exception:
                    break
            if not found:
                self.combo_group.set_active(0)
                self.entry_group.set_text(conn.group)

        # Command
        buf = self.textview_command.get_buffer()
        buf.set_text(conn.command or "")

        # TERM
        self.entry_term.set_text(conn.term_type or "")

        # Commands (post-login)
        buf_cmds = self.textview_commands.get_buffer()
        buf_cmds.set_text(conn.commands or "")

        # Font
        self.entry_font.set_text(conn.font or "")

        # Colors
        if conn.bg_color:
            rgba = Gdk.RGBA()
            if rgba.parse(conn.bg_color):
                self.color_bg.set_rgba(rgba)
                self.chk_bg_color.set_active(True)
        if conn.fg_color:
            rgba = Gdk.RGBA()
            if rgba.parse(conn.fg_color):
                self.color_fg.set_rgba(rgba)
                self.chk_fg_color.set_active(True)

        # Credentials
        p1 = self.credential_store.get_passphrase1(conn.id)
        if p1:
            self.entry_passphrase1.set_text(p1)
        p2 = self.credential_store.get_passphrase2(conn.id)
        if p2:
            self.entry_passphrase2.set_text(p2)
        pw = self.credential_store.get_password(conn.id)
        if pw:
            self.entry_password.set_text(pw)

    # -----------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------

    def _on_save(self, button):
        """Validate and save the connection."""
        # Get command text
        buf = self.textview_command.get_buffer()
        start, end = buf.get_bounds()
        command = buf.get_text(start, end, True).strip()

        name = self.entry_name.get_text().strip()

        if not command and not name:
            self._show_error("Either a Name or Command is required.")
            return

        # If no name, derive from command
        if not name:
            name = command.split()[-1] if command else "Untitled"

        # Determine group
        group = self.entry_group.get_text().strip()
        if not group:
            active_text = self.combo_group.get_active_text()
            group = active_text.strip() if active_text else ""

        # Build / update connection
        conn = self.connection if self.connection else Connection()
        conn.name = name
        conn.group = group
        conn.description = self.entry_description.get_text().strip()
        conn.command = command
        conn.font = self.entry_font.get_text().strip()
        conn.term_type = self.entry_term.get_text().strip()

        # Colors — only save when explicitly checked
        conn.bg_color = ""
        conn.fg_color = ""
        if self.chk_bg_color.get_active():
            conn.bg_color = self.color_bg.get_rgba().to_string()
        if self.chk_fg_color.get_active():
            conn.fg_color = self.color_fg.get_rgba().to_string()

        # Post-login commands
        buf_cmds = self.textview_commands.get_buffer()
        s, e = buf_cmds.get_bounds()
        conn.commands = buf_cmds.get_text(s, e, True)

        # Persist connection
        self.connection_manager.update_connection(conn)

        # --- Credentials ---
        def _cred_save(store_fn, get_fn, value, conn_id):
            """Store credential if non-empty, clear if emptied."""
            if value:
                store_fn(conn_id, value)
            elif get_fn(conn_id):
                store_fn(conn_id, "")

        _cred_save(self.credential_store.store_passphrase1,
                    self.credential_store.get_passphrase1,
                    self.entry_passphrase1.get_text(), conn.id)
        _cred_save(self.credential_store.store_passphrase2,
                    self.credential_store.get_passphrase2,
                    self.entry_passphrase2.get_text(), conn.id)
        _cred_save(self.credential_store.store_password,
                    self.credential_store.get_password,
                    self.entry_password.get_text(), conn.id)

        # Ensure group is tracked
        if conn.group:
            self.connection_manager.add_group(conn.group)

        self.emit("connection-saved", conn)
        self.close()

    def _show_error(self, message: str):
        """Display a validation error."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Validation Error",
                body=message,
            )
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception:
            print(f"Error: {message}")
