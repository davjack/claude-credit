#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Davjack
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install claude-credit for the current user: `python3 install.py`, from this folder.

Copies the script and its Bash firewall to ~/.local/share/claude-credit/ and
links ~/.local/bin/claude-credit to it. Run it again to update. Nothing else on
the system changes: no sudo, no Claude Code setting.
"""

import importlib.util
import os
import shutil
import sys

FILES = (("claude-credit", 0o755), ("hooks/bash-firewall.py", 0o644), ("hooks/hostio.py", 0o644))
DISPLAY = """Affichage complet (facultatif) : les paquets rich et prompt_toolkit manquent.
  Debian, Ubuntu, WSL : sudo apt install python3-rich python3-prompt-toolkit
  Fedora :              sudo dnf install python3-rich python3-prompt-toolkit
  Arch :                sudo pacman -S python-rich python-prompt_toolkit
  macOS, autres :       python3 -m pip install --user rich prompt_toolkit
Sans eux, claude-credit marche avec un affichage simple."""


def main():
    if os.name == "nt":
        print("Windows : claude-credit ne marche que dans WSL. Relancer cette commande dans WSL.")
        return 1
    source = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser("~")
    target = os.path.join(home, ".local", "share", "claude-credit")
    bin_dir = os.path.join(home, ".local", "bin")
    link = os.path.join(bin_dir, "claude-credit")
    script = os.path.join(target, "claude-credit")

    if os.path.lexists(link) and not (os.path.islink(link) and os.readlink(link) == script):
        print(f"{link} existe déjà et n'est pas le lien de cette installation : rien n'a été changé.")
        print("Le déplacer ou le supprimer, puis relancer : python3 install.py")
        return 1
    for name, mode in FILES:
        path = os.path.join(target, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Written aside, then renamed: a session still running never reads a half-written hook.
        shutil.copyfile(os.path.join(source, name), path + ".new")
        os.chmod(path + ".new", mode)
        os.replace(path + ".new", path)
    os.makedirs(bin_dir, exist_ok=True)
    if not os.path.islink(link):
        os.symlink(script, link)

    print(f"claude-credit est installé dans {target}")
    print(f"Commande : {link}")
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"{bin_dir} n'est pas dans le PATH. Ajouter cette ligne à ~/.bashrc ou ~/.zshrc,"
              " puis ouvrir un nouveau terminal :")
        print('  export PATH="$HOME/.local/bin:$PATH"')
    if shutil.which("claude") is None:
        print("Claude Code (la commande claude) est introuvable : l'installer avant la première séance.")
    if any(importlib.util.find_spec(name) is None for name in ("rich", "prompt_toolkit")):
        print(DISPLAY)
    print("Pour désinstaller : rm -r ~/.local/share/claude-credit ~/.local/bin/claude-credit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
