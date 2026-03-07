"""
SSH process handling with SSH_ASKPASS-based credential injection.

No expect commands are used. Instead, credentials are passed via a
temporary SSH_ASKPASS script that outputs the appropriate credential
when SSH requests it.
"""

import os
import stat
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from .connection import Connection
from .credential_store import CredentialStore


class SSHHandler:
    """
    Builds SSH commands and manages credential injection via SSH_ASKPASS.

    The askpass mechanism works by:
    1. Creating a temporary shell script that echoes the stored password/passphrase
    2. Setting SSH_ASKPASS to point to this script
    3. Setting SSH_ASKPASS_REQUIRE=force so SSH uses it even with a TTY
    4. Cleaning up the script after a delay
    """

    def __init__(self, credential_store: CredentialStore):
        self._cred_store = credential_store
        self._askpass_scripts: dict[str, str] = {}  # connection_id -> script path
        self._askpass_dir = Path(tempfile.mkdtemp(prefix="ssh-cm-askpass-"))
        os.chmod(str(self._askpass_dir), stat.S_IRWXU)  # 0700

    def build_ssh_command(self, connection: Connection) -> list[str]:
        """
        Build the SSH command line from the connection's command field.

        The command field contains the full SSH command as typed in a shell,
        potentially spanning multiple lines.  We join lines and split with
        shlex so that quoted arguments are preserved.

        Returns: list of command arguments, e.g. ["ssh", "-p", "22", "user@host"]
        """
        import shlex

        if not connection.command:
            return ["ssh"]

        # Join multi-line commands and split properly
        cmd_str = " ".join(connection.command.strip().splitlines())
        try:
            return shlex.split(cmd_str)
        except ValueError:
            # Fallback: naive split
            return cmd_str.split()

    def build_environment(self, connection: Connection) -> list[str]:
        """
        Build environment variables for the SSH process.

        Returns: list of "KEY=VALUE" strings suitable for VTE spawn.
        """
        env = os.environ.copy()

        # Set TERM
        if connection.term_type:
            env["TERM"] = connection.term_type
        elif "TERM" not in env:
            env["TERM"] = "xterm-256color"

        # Set up SSH_ASKPASS for credential injection
        askpass_script = self._create_askpass_script(connection)
        if askpass_script:
            env["SSH_ASKPASS"] = askpass_script
            env["SSH_ASKPASS_REQUIRE"] = "force"
            # DISPLAY must be set for SSH_ASKPASS to work
            if "DISPLAY" not in env:
                env["DISPLAY"] = ":0"

            # If a password is stored, disable SSH agent so SSH_ASKPASS is used
            password = self._cred_store.get_password(connection.id)
            if password:
                env.pop("SSH_AUTH_SOCK", None)

        return [f"{k}={v}" for k, v in env.items()]

    def _create_askpass_script(self, connection: Connection) -> Optional[str]:
        """
        Create a temporary SSH_ASKPASS script for the connection.

        SSH invokes this script once per prompt, passing the prompt text
        as $1.  The script inspects the prompt to decide what to return:

        * Prompt contains "passphrase" → key passphrase is needed.
          - 1st passphrase prompt  → Passphrase 1
          - 2nd passphrase prompt  → Passphrase 2  (if set)
          - If no passphrase stored → exit 1  (SSH falls back to interactive)
        * Prompt contains "password"  → login password is needed.
          - Return Password if stored, otherwise exit 1.
        * Any other prompt           → exit 1  (unknown prompt, don't guess).

        Because SSH spawns a **new process** for each prompt, we persist a
        counter in a temp file keyed to the connection ID so that successive
        passphrase prompts see an incrementing count.
        """
        password = self._cred_store.get_password(connection.id)
        passphrase1 = self._cred_store.get_passphrase1(connection.id)
        passphrase2 = self._cred_store.get_passphrase2(connection.id)

        if not password and not passphrase1 and not passphrase2:
            return None

        # Counter file uses connection-id prefix so it survives across
        # the separate askpass process invocations for one SSH session.
        counter_file = f"/tmp/.ssh-cm-askpass-counter-{connection.id[:8]}"

        # Build the askpass script — always branch on prompt type
        script_lines = [
            "#!/bin/bash",
            'PROMPT="$1"',
            f'COUNTER_FILE="{counter_file}"',
            '',
            '# --- passphrase prompt ---',
            'if echo "$PROMPT" | grep -qi "passphrase"; then',
        ]

        if passphrase1 and passphrase2:
            script_lines.extend([
                '    COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)',
                '    COUNT=$((COUNT + 1))',
                '    echo "$COUNT" > "$COUNTER_FILE"',
                '    if [ "$COUNT" -le 1 ]; then',
                f'        echo "{self._escape_for_shell(passphrase1)}"',
                '    else',
                f'        echo "{self._escape_for_shell(passphrase2)}"',
                '    fi',
            ])
        elif passphrase1:
            script_lines.append(
                f'    echo "{self._escape_for_shell(passphrase1)}"')
        elif passphrase2:
            script_lines.append(
                f'    echo "{self._escape_for_shell(passphrase2)}"')
        else:
            # No passphrase stored → fail so SSH falls back to interactive
            script_lines.append('    exit 1')

        # --- password prompt ---
        script_lines.append('elif echo "$PROMPT" | grep -qi "password"; then')
        if password:
            script_lines.append(
                f'    echo "{self._escape_for_shell(password)}"')
        else:
            script_lines.append('    exit 1')

        # --- anything else → don't guess ---
        script_lines.extend([
            'else',
            '    exit 1',
            'fi',
        ])

        script_content = "\n".join(script_lines) + "\n"

        # Write to temp file with restricted permissions
        script_path = os.path.join(
            str(self._askpass_dir),
            f"askpass-{connection.id[:8]}"
        )

        fd = os.open(script_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                      stat.S_IRWXU)  # 0700
        with os.fdopen(fd, "w") as f:
            f.write(script_content)

        self._askpass_scripts[connection.id] = script_path
        return script_path

    def cleanup_askpass(self, connection_id: str):
        """Remove the askpass script and counter file for a connection."""
        if connection_id in self._askpass_scripts:
            script_path = self._askpass_scripts.pop(connection_id)
            try:
                os.unlink(script_path)
            except OSError:
                pass
        # Remove passphrase counter file
        counter = f"/tmp/.ssh-cm-askpass-counter-{connection_id[:8]}"
        try:
            os.unlink(counter)
        except OSError:
            pass

    def cleanup_all(self):
        """Remove all askpass scripts and the temp directory."""
        for script_path in self._askpass_scripts.values():
            try:
                os.unlink(script_path)
            except OSError:
                pass
        self._askpass_scripts.clear()
        try:
            shutil.rmtree(str(self._askpass_dir), ignore_errors=True)
        except OSError:
            pass

    def get_post_login_commands(self, connection: Connection) -> list[str]:
        """
        Parse post-login commands from the connection config.

        Supports delay syntax: ##D=1000 (delay 1000ms before next command)
        Returns list of (command_or_delay) strings.
        """
        if not connection.commands:
            return []

        commands = []
        for line in connection.commands.strip().split("\n"):
            line = line.strip()
            if line:
                commands.append(line)
        return commands

    @staticmethod
    def _escape_for_shell(s: str) -> str:
        """Escape a string for safe inclusion in a shell double-quoted string."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")

    @staticmethod
    def get_local_shell_command() -> list[str]:
        """Get the command for opening a local shell."""
        shell = os.environ.get("SHELL", "/bin/bash")
        return [shell]
