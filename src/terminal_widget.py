"""
VTE Terminal widget wrapper for GTK4.

Provides a configured VTE terminal with scrollbar, search, and connection
lifecycle management.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Vte', '3.91')

from gi.repository import Gtk, Vte, GLib, Gdk, Pango, GObject
from typing import Optional

import os
import signal

from .connection import Connection
from .config import Config


class TerminalWidget(Gtk.Box):
    """
    A VTE terminal with scrollbar and connection management.

    Signals:
        title-changed: Terminal title changed
        child-exited: The shell/SSH process exited
        connection-established: SSH connection appears alive
    """

    __gsignals__ = {
        "title-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "child-exited": (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    def __init__(self, config: Config, connection: Optional[Connection] = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)

        self.config = config
        self.connection = connection
        self._process_pid = -1

        # Create VTE terminal
        self.vte = Vte.Terminal()
        self._configure_terminal()

        # Create scrollbar
        vadjustment = self.vte.get_vadjustment()
        self.scrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=vadjustment
        )

        # Pack terminal + scrollbar
        self.vte.set_hexpand(True)
        self.vte.set_vexpand(True)
        self.append(self.vte)
        self.append(self.scrollbar)

        # Connect signals
        self.vte.connect("child-exited", self._on_child_exited)
        self.vte.connect("window-title-changed", self._on_title_changed)

        # Focus handling
        self.vte.set_focusable(True)
        self.vte.set_can_focus(True)

        # Key event controller for shortcuts
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.vte.add_controller(key_ctrl)

        # Ctrl+Scroll to zoom font size
        scroll_ctrl = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_ctrl.connect("scroll", self._on_scroll)
        self.vte.add_controller(scroll_ctrl)

        # Font scale tracking (1.0 = default)
        self._font_scale = 1.0

        # Right-click menu
        self._setup_context_menu()

    def _configure_terminal(self):
        """Apply terminal configuration."""
        cfg = self.config

        # Font
        font_desc = Pango.FontDescription.from_string(
            (self.connection and self.connection.font) or cfg["terminal_font"]
        )
        self.vte.set_font(font_desc)

        # Scrollback
        self.vte.set_scrollback_lines(cfg["terminal_scrollback_lines"])

        # Colors – use connection overrides only when they are set AND distinct
        conn_bg = (self.connection and self.connection.bg_color) or ""
        conn_fg = (self.connection and self.connection.fg_color) or ""
        if conn_bg and conn_fg and conn_bg == conn_fg:
            # Same fg/bg would make text invisible → ignore both
            conn_bg = conn_fg = ""
        bg_str = conn_bg or cfg["terminal_bg_color"]
        fg_str = conn_fg or cfg["terminal_fg_color"]

        bg = Gdk.RGBA()
        bg.parse(bg_str)
        fg = Gdk.RGBA()
        fg.parse(fg_str)

        # Parse palette
        palette = []
        for color_str in cfg["terminal_palette"]:
            c = Gdk.RGBA()
            c.parse(color_str)
            palette.append(c)

        self.vte.set_colors(fg, bg, palette)

        # Cursor
        cursor_map = {
            "block": Vte.CursorShape.BLOCK,
            "ibeam": Vte.CursorShape.IBEAM,
            "underline": Vte.CursorShape.UNDERLINE,
        }
        shape = cursor_map.get(cfg["terminal_cursor_shape"], Vte.CursorShape.BLOCK)
        self.vte.set_cursor_shape(shape)

        # Misc
        self.vte.set_allow_bold(cfg["terminal_allow_bold"])
        self.vte.set_audible_bell(cfg["terminal_audible_bell"])

        # Allow hyperlinks
        try:
            self.vte.set_allow_hyperlink(True)
        except AttributeError:
            pass  # Older VTE

        # Word char exceptions for double-click selection
        try:
            self.vte.set_word_char_exceptions(cfg["word_separators"])
        except AttributeError:
            pass

    def spawn_command(self, argv: list[str], env: list[str] = None,
                      working_dir: str = None):
        """
        Spawn a process inside the terminal.

        Args:
            argv: Command and arguments, e.g. ["ssh", "user@host"]
            env: Environment variables as "KEY=VALUE" strings
            working_dir: Working directory (default: home)
        """
        import os
        import shutil

        if working_dir is None:
            working_dir = str(GLib.get_home_dir())

        # Build environment
        if env is None:
            env = [f"{k}={v}" for k, v in os.environ.items()]

        # Resolve argv[0] to absolute path (GLib.SpawnFlags.DEFAULT
        # requires an absolute path; this avoids needing SEARCH_PATH)
        if argv and not os.path.isabs(argv[0]):
            resolved = shutil.which(argv[0])
            if resolved:
                argv = [resolved] + argv[1:]

        try:
            self.vte.spawn_async(
                Vte.PtyFlags.DEFAULT,       # pty_flags
                working_dir,                # working_directory
                argv,                       # argv
                env,                        # envv
                GLib.SpawnFlags.DEFAULT,    # spawn_flags
                None,                       # child_setup
                None,                       # child_setup_data
                -1,                         # timeout (-1 = default)
                None,                       # cancellable
                self._on_spawn_complete,    # callback
                None,                       # callback_data
            )
        except Exception as e:
            print(f"Error spawning process: {e}")
            import traceback
            traceback.print_exc()

    def _on_spawn_complete(self, terminal, pid, error, *user_data):
        """Callback after spawn_async completes."""
        if error:
            print(f"Spawn error: {error}")
            return
        self._process_pid = pid

    def feed_child(self, text: str):
        """Send text to the terminal as if typed."""
        try:
            self.vte.feed_child(text.encode("utf-8"))
        except TypeError:
            # Some VTE versions need different args
            data = text.encode("utf-8")
            self.vte.feed_child(data, len(data))

    def copy_clipboard(self):
        """Copy selected text to clipboard."""
        self.vte.copy_clipboard_format(Vte.Format.TEXT)

    def paste_clipboard(self):
        """Paste from clipboard."""
        self.vte.paste_clipboard()

    def select_all(self):
        """Select all terminal content."""
        self.vte.select_all()

    def reset_terminal(self, clear: bool = False):
        """Reset the terminal. If clear=True, also clear scrollback."""
        self.vte.reset(True, clear)

    def get_text(self) -> str:
        """Get all visible terminal text."""
        try:
            text = self.vte.get_text_format(Vte.Format.TEXT)
            if isinstance(text, tuple):
                text = text[0]
            return text if text else ""
        except Exception:
            return ""

    def search_text(self, pattern: str, backward: bool = False):
        """Search for text in the terminal."""
        try:
            regex = Vte.Regex.new_for_search(pattern, len(pattern.encode()), 0)
            self.vte.search_set_regex(regex, 0)
            if backward:
                self.vte.search_find_previous()
            else:
                self.vte.search_find_next()
        except Exception as e:
            print(f"Search error: {e}")

    def set_font_scale(self, scale: float):
        """Set the terminal font scale."""
        self.vte.set_font_scale(scale)

    def get_title(self) -> str:
        """Get the terminal window title."""
        return self.vte.get_window_title() or ""

    def grab_focus(self):
        """Focus the VTE terminal."""
        self.vte.grab_focus()

    def terminate(self):
        """Kill the child process running inside the terminal."""
        pid = self._process_pid
        if pid > 0:
            try:
                os.kill(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass  # Already exited
            except OSError as e:
                print(f"Error killing process {pid}: {e}")
            finally:
                self._process_pid = -1

    # --- Signal handlers ---

    def _on_child_exited(self, terminal, status):
        self._process_pid = -1  # Process already exited, clear PID
        self.emit("child-exited", status)

    def _on_title_changed(self, terminal):
        title = self.get_title()
        self.emit("title-changed", title)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK

        if ctrl and shift:
            if keyval == Gdk.KEY_C:
                self.copy_clipboard()
                return True
            elif keyval == Gdk.KEY_V:
                self.paste_clipboard()
                return True

        # Ctrl+0 / Ctrl+KP_0: reset font size
        if ctrl and keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self._font_scale = 1.0
            self.vte.set_font_scale(1.0)
            return True

        # Ctrl+Plus: zoom in
        if ctrl and keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._font_scale = min(self._font_scale + 0.1, 4.0)
            self.vte.set_font_scale(self._font_scale)
            return True

        # Ctrl+Minus: zoom out
        if ctrl and keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._font_scale = max(self._font_scale - 0.1, 0.3)
            self.vte.set_font_scale(self._font_scale)
            return True

        return False

    def _on_scroll(self, controller, dx, dy):
        """Handle Ctrl+Scroll to zoom font size."""
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False  # Let normal scrollback work

        step = 0.1
        if dy < 0:
            # Scroll up → zoom in
            self._font_scale = min(self._font_scale + step, 4.0)
        elif dy > 0:
            # Scroll down → zoom out
            self._font_scale = max(self._font_scale - step, 0.3)

        self.vte.set_font_scale(self._font_scale)
        return True  # Consume the event

    def _setup_context_menu(self):
        """Set up right-click context menu."""
        click = Gtk.GestureClick(button=3)  # Right click
        click.connect("pressed", self._show_context_menu)
        self.vte.add_controller(click)
        self._context_popover: Gtk.PopoverMenu | None = None

    def _show_context_menu(self, gesture, n_press, x, y):
        """Show the terminal context menu."""
        # Clean up any previous popover
        self._cleanup_context_popover()

        menu_model = self._build_context_menu()
        popover = Gtk.PopoverMenu(menu_model=menu_model)
        popover.set_parent(self.vte)
        popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        popover.connect("closed", self._on_context_popover_closed)
        self._context_popover = popover
        popover.popup()

    def _cleanup_context_popover(self):
        """Unparent and discard the current context popover if any."""
        if self._context_popover is not None:
            try:
                self._context_popover.unparent()
            except Exception:
                pass
            self._context_popover = None

    def _on_context_popover_closed(self, popover):
        """Schedule popover cleanup after GTK finishes its bookkeeping."""
        GLib.idle_add(self._deferred_context_popover_cleanup, popover)

    def _deferred_context_popover_cleanup(self, popover):
        """Unparent the popover now that GTK state accounting is done."""
        try:
            popover.unparent()
        except Exception:
            pass
        if self._context_popover is popover:
            self._context_popover = None
        return False

    def _build_context_menu(self):
        """Build the context menu model."""
        from gi.repository import Gio
        menu = Gio.Menu()

        section1 = Gio.Menu()
        section1.append("Copy", "term.copy")
        section1.append("Paste", "term.paste")
        section1.append("Select All", "term.select-all")
        menu.append_section(None, section1)

        section2 = Gio.Menu()
        section2.append("Reset Terminal", "term.reset")
        section2.append("Clear Scrollback", "term.clear")
        menu.append_section(None, section2)

        section3 = Gio.Menu()
        section3.append("Split Horizontally", "panel.split-h")
        section3.append("Split Vertically", "panel.split-v")
        menu.append_section(None, section3)

        return menu
