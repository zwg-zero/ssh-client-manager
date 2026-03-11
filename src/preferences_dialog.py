"""
Preferences dialog.

Provides UI for configuring terminal appearance, behavior, and shortcuts.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk
from .config import Config


class PreferencesDialog(Adw.Window):
    """Application preferences window."""

    def __init__(self, parent: Gtk.Window, config: Config):
        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=500,
            default_height=600,
            title="Preferences",
        )

        self.config = config

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # === Terminal Section ===
        content.append(self._section_label("Terminal"))

        # Font
        self.entry_font = Gtk.Entry(text=config["terminal_font"])
        content.append(self._pref_row("Font:", self.entry_font))

        # Scrollback lines
        self.spin_scrollback = Gtk.SpinButton()
        self.spin_scrollback.set_range(100, 1000000)
        self.spin_scrollback.set_value(config["terminal_scrollback_lines"])
        self.spin_scrollback.set_increments(100, 1000)
        content.append(self._pref_row("Scrollback Lines:", self.spin_scrollback))

        # Background color
        self.color_bg = Gtk.ColorButton()
        bg = Gdk.RGBA()
        bg.parse(config["terminal_bg_color"])
        self.color_bg.set_rgba(bg)
        content.append(self._pref_row("Background Color:", self.color_bg))

        # Foreground color
        self.color_fg = Gtk.ColorButton()
        fg = Gdk.RGBA()
        fg.parse(config["terminal_fg_color"])
        self.color_fg.set_rgba(fg)
        content.append(self._pref_row("Foreground Color:", self.color_fg))

        # Cursor shape
        self.combo_cursor = Gtk.ComboBoxText()
        self.combo_cursor.append("block", "Block")
        self.combo_cursor.append("ibeam", "IBeam")
        self.combo_cursor.append("underline", "Underline")
        self.combo_cursor.set_active_id(config["terminal_cursor_shape"])
        content.append(self._pref_row("Cursor Shape:", self.combo_cursor))

        # Bold
        self.switch_bold = Gtk.Switch()
        self.switch_bold.set_active(config["terminal_allow_bold"])
        self.switch_bold.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Allow Bold:", self.switch_bold))

        # Bell
        self.switch_bell = Gtk.Switch()
        self.switch_bell.set_active(config["terminal_audible_bell"])
        self.switch_bell.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Audible Bell:", self.switch_bell))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === SSH Section ===
        content.append(self._section_label("SSH"))

        self.spin_default_port = Gtk.SpinButton()
        self.spin_default_port.set_range(1, 65535)
        self.spin_default_port.set_value(config["ssh_default_port"])
        self.spin_default_port.set_increments(1, 10)
        content.append(self._pref_row("Default Port:", self.spin_default_port))

        self.spin_keepalive = Gtk.SpinButton()
        self.spin_keepalive.set_range(0, 3600)
        self.spin_keepalive.set_value(config["ssh_keepalive_interval"])
        self.spin_keepalive.set_increments(10, 60)
        content.append(self._pref_row("Keepalive Interval:", self.spin_keepalive))

        self.spin_timeout = Gtk.SpinButton()
        self.spin_timeout.set_range(5, 300)
        self.spin_timeout.set_value(config["ssh_connection_timeout"])
        self.spin_timeout.set_increments(5, 30)
        content.append(self._pref_row("Connection Timeout:", self.spin_timeout))

        # SSH config edit command
        self.entry_ssh_config_cmd = Gtk.Entry(text=config["ssh_config_edit_command"])
        self.entry_ssh_config_cmd.set_placeholder_text("vim ~/.ssh/config")
        content.append(self._pref_row("SSH Config Edit Cmd:", self.entry_ssh_config_cmd))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Behavior Section ===
        content.append(self._section_label("Behavior"))

        self.switch_confirm_close_tab = Gtk.Switch()
        self.switch_confirm_close_tab.set_active(config["confirm_close_tab"])
        self.switch_confirm_close_tab.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Confirm Close Tab:", self.switch_confirm_close_tab))

        self.switch_confirm_close_window = Gtk.Switch()
        self.switch_confirm_close_window.set_active(config["confirm_close_window"])
        self.switch_confirm_close_window.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Confirm Close Window:", self.switch_confirm_close_window))

        self.switch_tab_close_btn = Gtk.Switch()
        self.switch_tab_close_btn.set_active(config["show_tab_close_button"])
        self.switch_tab_close_btn.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Tab Close Button:", self.switch_tab_close_btn))

        # === Apply/Save Buttons ===
        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)

        btn_apply = Gtk.Button(label="Apply")
        btn_apply.add_css_class("suggested-action")
        btn_apply.connect("clicked", self._on_apply)
        btn_box.append(btn_apply)

        content.append(btn_box)

        scrolled.set_child(content)
        main_box.append(scrolled)
        self.set_content(main_box)

    def _section_label(self, text: str) -> Gtk.Label:
        """Create a section heading label."""
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        label.add_css_class("heading")
        label.set_margin_top(8)
        return label

    def _pref_row(self, label_text: str, widget: Gtk.Widget) -> Gtk.Box:
        """Create a preference row with label and widget."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(8)
        row.set_margin_end(8)

        label = Gtk.Label(label=label_text)
        label.set_xalign(1)
        label.set_size_request(150, -1)
        label.add_css_class("dim-label")
        row.append(label)

        widget.set_hexpand(True)
        row.append(widget)
        return row

    def _on_apply(self, button):
        """Apply and save all preferences."""
        cfg = self.config

        cfg.set("terminal_font", self.entry_font.get_text())
        cfg.set("terminal_scrollback_lines", int(self.spin_scrollback.get_value()))
        cfg.set("terminal_bg_color", self.color_bg.get_rgba().to_string())
        cfg.set("terminal_fg_color", self.color_fg.get_rgba().to_string())
        cfg.set("terminal_cursor_shape", self.combo_cursor.get_active_id() or "block")
        cfg.set("terminal_allow_bold", self.switch_bold.get_active())
        cfg.set("terminal_audible_bell", self.switch_bell.get_active())
        cfg.set("ssh_default_port", int(self.spin_default_port.get_value()))
        cfg.set("ssh_keepalive_interval", int(self.spin_keepalive.get_value()))
        cfg.set("ssh_connection_timeout", int(self.spin_timeout.get_value()))
        cfg.set("ssh_config_edit_command", self.entry_ssh_config_cmd.get_text())
        cfg.set("confirm_close_tab", self.switch_confirm_close_tab.get_active())
        cfg.set("confirm_close_window", self.switch_confirm_close_window.get_active())
        cfg.set("show_tab_close_button", self.switch_tab_close_btn.get_active())

        cfg.save()
        self.close()
