#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Davjack
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PreToolUse hook: guard Bash against catastrophic and session-killing commands.

Purpose: provide a hard floor that survives `--dangerously-skip-permissions`
(bypassPermissions). In that mode permission `deny` rules still apply but the
built-in circuit breaker only covers `rm -rf /` and `rm -rf ~`, so this hook is
the only place left to stop the rest.

It blocks two families:

  1. **Irreversible destruction** — the command cannot be undone and no commit
     brings the data back.
  2. **Commands that hang forever** — interactive wizards blocking on `read`.
     Harmless when you are watching, fatal for an unattended run: the session
     stalls with no error and no timeout. Blocking them (exit 2) hands Claude a
     reason it can act on, usually by finding the non-interactive variant.

Design goals:
  - NO false positives on data. A dangerous string inside a commit message, an
    `echo`, or a `grep` pattern is not a dangerous command. This is why the
    command is tokenized quote-aware and only the *command word* of each
    pipeline segment is inspected, instead of regex-scanning the whole string.
    Two deliberate exceptions scan the raw string: the fork bomb, whose shape is
    unmistakable, and the fallback used when tokenization fails.
  - Follow the command through wrappers — `bash -c`, `xargs`, `find -exec` —
    because `bash -c "rm -rf /"` is the same command with a hat on.
  - Distinguish catastrophic from merely destructive. `rm -rf node_modules` is
    routine; `rm -rf .` wipes the working tree. Only the former stays allowed.
  - Block (exit 2) rather than prompt: exit 2 feeds the reason back to Claude
    without stopping the session, and the user can always run it by hand.

Input  (stdin JSON): { "tool_name": "Bash", "tool_input": { "command": "..." } }
Kimi uses `Shell`. Explicit patch/MCP/other tool names are ignored even if their
arguments include `command`; absent tool names retain the legacy shell payload.
Output: exit 2 + stderr reason to BLOCK; exit 0 to allow.

Fail-open on malformed tool payloads. This guard recognizes selected shell
patterns; it is not a universal shell parser or a sandbox for arbitrary code.
"""

import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostio  # noqa: E402  (sibling module, resolved via the line above)

# --- tokenization -----------------------------------------------------------

OPERATORS = {"|", "||", "&&", ";", "&", "|&"}
PUNCTUATION = "();<>|&\n"  # an unquoted newline ends a command, like `;`
# Words that can legitimately precede the real command inside a segment.
PREFIXES = {"sudo", "doas", "command", "exec", "time", "nohup", "nice",
            "ionice", "setsid", "stdbuf", "env", "timeout", "systemd-run"}
# Shell keywords that open a command position: `then rm -rf ~` runs rm.
KEYWORDS = {"(", "{", "!", "if", "then", "else", "elif", "while", "until", "do", "coproc"}
# `<<EOF`, `<<-'EOF'`, `<< "END-OF-TEXT"`, `<<\EOF`, not the here-string `<<<`.
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([^\s'\"<>|&;()]+))")
# A redirection operator as tokenize() returns it: `>`, `>>`, `>&`, `&>`, `<`, `>|`.
REDIRECTION = re.compile(r"&?[<>]+[&|]?")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}

MAX_DEPTH = 3  # bash -c inside xargs inside find is already pathological

# Shapes scanned for in the raw string when tokenization fails (see scan()).
UNPARSEABLE_DANGER = re.compile(
    r"(?:^|[\s;&|(])(?:"
    r"rm\s+(?:-\w*[rR]\w*\s+)+[^\s]"      # recursive rm, any target
    r"|mkfs(?:\.\w+)?\s"
    r"|shred\s"
    r"|dd\s[^\n]*\bof=/dev/(?!null|zero|stdout|stderr)"
    r"|rm\s+(?:--recursive|--force)\s"        # long-form flags
    r"|git\s[^\n]*\b(?:reset\s+--hard|push\s+(?:-f|--force)(?!-with-lease)|clean\s+-\w*[dxX])"
    r")"
)

# --- interactive commands ---------------------------------------------------
# Scripts and targets that block on stdin. Project-specific entries belong in
# the make-target list; the script pattern is generic.
INTERACTIVE_SCRIPT = re.compile(r"(?:^|/)(?:.*-)?wizard[\w-]*\.sh$|(?:^|/)db-setup\.sh$")
# `setup` is deliberately absent: it is the most common bootstrap target there
# is, and non-interactive in most projects. Blocking it would fire on routine
# work, which gets a guard switched off.
#
# `backup` and `restore` are a judged trade-off, not an obvious call: they are
# often plain non-interactive targets elsewhere. They stay because in the stack
# this hook was first written for they run a blocking wizard, and the cost of the two
# outcomes is not symmetric — a false positive costs one blocked call and a
# message naming the fix, a false negative costs a multi-hour unattended run
# that dies silently with no error and no timeout.
INTERACTIVE_MAKE_TARGETS = {
    "create-admin", "create-user", "backup", "restore",
}


def strip_heredocs(command):
    """(the command without its heredoc bodies, [(text before `<<`, text after, body)]).

    A body is data unless a shell reads it, which scan() checks. It runs from
    the line after its `<<WORD` (after the logical line, when a `\\` continues
    it) to the line holding only WORD (leading tabs allowed, for `<<-`); the
    bodies of one line follow each other. A `<<` with no such line (inside
    quotes, in arithmetic) is no heredoc: its lines stay commands.
    """
    lines, kept, bodies, i = command.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        i += 1
        # A body starts after the logical line: `cat <<EOF \` continues on the next one.
        while i < len(lines) and (len(line) - len(line.rstrip("\\"))) % 2:
            line += "\n" + lines[i]
            i += 1
        kept.append(line)
        for match in HEREDOC.finditer(line):
            word = next(group for group in match.groups() if group)
            end = next((j for j in range(i, len(lines)) if lines[j].lstrip("\t").rstrip(" \t") == word), None)
            if end is not None:
                bodies.append((line[:match.start()], line[match.end():], "\n".join(lines[i:end])))
                i = end + 1
    return "\n".join(kept), bodies


def strip_comments(command):
    """The command without its comments: a `#` opening a word, outside quotes, to the end of its line.

    Left in, an apostrophe in a comment (`# don't`) unbalanced the whole command
    and sent it to the raw fallback, which blocks a routine `rm -rf build` and
    misses `find ~ -delete`.
    """
    out, quote, i = [], None, 0
    while i < len(command):
        char = command[i]
        if char == "\\" and quote != "'":
            out.append(command[i:i + 2])
            i += 2
            continue
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and (i == 0 or command[i - 1] in " \t\n;&|()"):
            end = command.find("\n", i)
            i = len(command) if end < 0 else end
            continue
        out.append(char)
        i += 1
    return "".join(out)


def tokenize(command):
    """Quote-aware tokenization. Shell operators and unquoted newlines become
    their own tokens, quoted strings collapse to a single token. Returns a token
    list, or None if the command cannot be parsed."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=PUNCTUATION)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""  # do not treat '#' (e.g. in URLs) as a comment
    try:
        return list(lex)
    except ValueError:
        return None


def segments(tokens):
    """Split a token list into pipeline/command segments on shell operators.

    shlex glues adjacent punctuation (`&&` before a line break, `);`, `&&(`):
    a punctuation-only token holding `;`, `&`, `|` or a newline ends a segment
    too, unless it is a redirection (`>&`, `&>`). A lone `{` opens one: a
    function body starts there.
    """
    segs, cur = [], []
    for tok in tokens:
        if tok in OPERATORS or tok == "{" or (
                not tok.strip(PUNCTUATION) and not REDIRECTION.fullmatch(tok)
                and any(char in tok for char in ";&|\n")):
            if cur:
                segs.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


# Options of a prefix command that consume the following token, and prefixes
# that take a positional operand of their own (`timeout 300 cmd`). Without this,
# `timeout 300 rm -rf /` resolved its command word to `300` and sailed through.
PREFIX_VALUE_OPTS = {
    "-u", "-g", "-p", "-n", "-c", "-k", "-s", "-w", "-C", "-D",
    "--user", "--group", "--prompt", "--adjustment", "--class", "--classdata",
    "--kill-after", "--signal", "--chdir", "--close-from",
}
PREFIX_OPERANDS = {"timeout": 1}
# A prefix whose options disagree with the shared set: systemd-run's `--user` is
# a bare flag, so the shared set would swallow the command word after it. Heavy
# jobs are commonly run as `systemd-run --user --scope ... nice -n 10 <cmd>`,
# which left every such command unseen until it was listed here.
OWN_VALUE_OPTS = {
    "systemd-run": {
        "-H", "-M", "-u", "-p", "-E", "--host", "--machine", "--unit",
        "--property", "--description", "--slice", "--expand-environment",
        "--service-type", "--uid", "--gid", "--nice", "--working-directory",
        "--root-directory", "--setenv", "--json", "--job-mode", "--background",
        "--path-property", "--socket-property", "--timer-property", "--on-active",
        "--on-boot", "--on-startup", "--on-unit-active", "--on-unit-inactive",
        "--on-calendar",
    },
}


def command_word(seg):
    """Return (command, args) after skipping env-assignments and prefix words."""
    i = 0
    operands_to_skip = 0
    value_opts = PREFIX_VALUE_OPTS
    while i < len(seg):
        t = seg[i]
        if re.match(r'^\w+=', t) or t in KEYWORDS:
            i += 1
            continue
        if t in PREFIXES:
            operands_to_skip += PREFIX_OPERANDS.get(t, 0)
            value_opts = OWN_VALUE_OPTS.get(t, PREFIX_VALUE_OPTS)
            i += 1
            continue
        if t.startswith("-"):
            # A flag can only belong to a prefix we already skipped.
            if i == 0:
                break
            if t in value_opts and i + 1 < len(seg) and not seg[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        if operands_to_skip:
            operands_to_skip -= 1
            i += 1
            continue
        break
    if i >= len(seg):
        return None, []
    return seg[i], seg[i + 1:]


def _short_flags(args):
    return "".join(a[1:] for a in args
                   if a.startswith("-") and not a.startswith("--")
                   and len(a) > 1 and not a[1].isdigit())


def _long_flags(args):
    return [a[2:] for a in args if a.startswith("--")]


# --- per-command danger checks ----------------------------------------------

# Literal targets that cannot be resolved to a path but always mean "everything
# here": the working tree itself, or a glob covering it.
CATASTROPHIC_RM_TARGETS = {".", "./", "..", "../", "*", "$PWD", "${PWD}", "$(pwd)"}

# A path component made only of glob metacharacters and dots: `*`, `**`, `.*`.
BARE_GLOB_RE = re.compile(r"[.*?\[\]!^-]+")


INSIDE, ROOT, OUTSIDE = "inside", "root", "outside"


def classify_target(path, cwd):
    """Where `path` sits relative to the working tree: INSIDE, ROOT or OUTSIDE.

    Resolving against `cwd` matters because Claude Code addresses files by
    absolute path, so `/home/me/proj/node_modules` and `node_modules` are the
    same command and must get the same verdict. Deleting INSIDE the tree is
    routine and git can restore it; ROOT wipes the work; OUTSIDE is unrecoverable.
    """
    if path in CATASTROPHIC_RM_TARGETS:
        return ROOT if path in (".", "./", "*", "$PWD", "${PWD}", "$(pwd)") else OUTSIDE
    if path.startswith("~") or "$HOME" in path or "${HOME}" in path:
        return OUTSIDE

    stripped = path.rstrip("/") or "/"

    # A bare glob means "everything in the parent directory", so it inherits the
    # parent's verdict: `./*` and `$PWD/*` empty the tree exactly like `rm -rf .`,
    # while `src/*` stays recoverable. Enumerating spellings instead of resolving
    # them left four near-misses open.
    head, tail = os.path.split(stripped)
    if tail and BARE_GLOB_RE.fullmatch(tail):
        return classify_target(head or ".", cwd)

    try:
        root = os.path.realpath(cwd)
        absolute = stripped if os.path.isabs(stripped) else os.path.join(cwd, stripped)
        if os.path.islink(absolute):
            # Removing a symlink removes the link, not what it points at.
            # Resolving it would block `rm -rf node_modules` in a worktree
            # whose node_modules is a symlink (`worktree.symlinkDirectories`).
            target = os.path.join(os.path.realpath(os.path.dirname(absolute)),
                                  os.path.basename(absolute))
        else:
            target = os.path.realpath(absolute)
    except (OSError, ValueError):
        return OUTSIDE if path.startswith(("/", "..")) else INSIDE

    if target == root:
        return ROOT
    if root.startswith(target + os.sep):
        return OUTSIDE  # an ancestor of the tree takes the tree with it
    if target.startswith(root + os.sep):
        return INSIDE
    # Outside the tree, but scratch space is safe to wipe. Checked last, so a
    # tree living under /tmp and its ancestors keep their protection.
    if re.match(r'^/(?:tmp|var/tmp)(?:/|$)', target):
        return INSIDE
    return OUTSIDE


def _rm_dangerous(args, cwd):
    """Recursive removal of a target that cannot be restored.

    `-f` is not required: it only suppresses prompts, which do not exist in a
    non-interactive run, so it is not what makes the command destructive.
    """
    short, longs = _short_flags(args), _long_flags(args)
    if not ("r" in short or "R" in short or "recursive" in longs):
        return False
    return any(
        not a.startswith("-") and classify_target(a, cwd) in (ROOT, OUTSIDE)
        for a in _without_redirections(args)
    )


def _without_redirections(args):
    """`args` without redirections, their files and fds: `rm -rf build 2>/dev/null` spares /dev/null."""
    def redirection(i):
        return 0 <= i < len(args) and REDIRECTION.fullmatch(args[i])
    return [a for i, a in enumerate(args)
            if not (redirection(i) or redirection(i - 1) or (a.isdigit() and redirection(i + 1)))]


def _git_dangerous(args):
    # Skip global options to find the real subcommand.
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1:]
    if sub == "reset" and "--hard" in rest:
        return True
    if sub == "push":
        # Short flags cluster (`-fu`), so test the letters, not the token.
        # --force-with-lease comes back from _long_flags as "force-with-lease",
        # not "force", and stays allowed. A refspec prefixed with '+' is a force
        # push written the other way.
        if "f" in _short_flags(rest) or "force" in _long_flags(rest):
            return True
        if any(r.startswith("+") for r in rest if not r.startswith("-")):
            return True
    if sub == "clean":
        short, longs = _short_flags(rest), _long_flags(rest)
        if ("f" in short or "force" in longs) and ("d" in short or "x" in short or "X" in short):
            return True
    return False


def _shell_is_bare(base, args):
    """A shell reading piped code from stdin (no script file, no -c payload)."""
    if base not in SHELLS:
        return False
    return all(a.startswith("-") for a in args) and "-c" not in args


def _make_interactive_target(args):
    for a in args:
        if a.startswith("-") or "=" in a:
            continue
        if a in INTERACTIVE_MAKE_TARGETS:
            return a
    return None


# --- nested command extraction ----------------------------------------------

# Options of xargs that consume the following token, so the command word is not
# mistaken for one of their values.
XARGS_VALUE_OPTS = {"-I", "-i", "-n", "-P", "-d", "-E", "-e", "-s", "-L", "-l", "-a", "--arg-file"}


def _nested_commands(base, args):
    """Return the command strings this one wraps, to be scanned in turn."""
    nested = []

    if base in SHELLS:
        for i, a in enumerate(args):
            if a in ("-c", "-lc", "-cl") and i + 1 < len(args):
                nested.append(args[i + 1])
                break

    elif base == "eval":
        # `eval 'rm -rf /'` is `bash -c` without the ceremony.
        nested.append(" ".join(args))

    elif base == "xargs":
        i = 0
        while i < len(args):
            a = args[i]
            if a in XARGS_VALUE_OPTS:
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            break
        if i < len(args):
            nested.append(shlex.join(args[i:]))

    elif base == "find":
        # `{}` stands for whatever find walks, so resolve it to the search path:
        # `find / -exec rm -rf {} \;` is `rm -rf /` with extra steps.
        target = _find_search_paths(args)[0]
        i = 0
        while i < len(args):
            if args[i] in ("-exec", "-execdir", "-ok", "-okdir"):
                payload = []
                i += 1
                while i < len(args) and args[i] not in (";", "+"):
                    payload.append(args[i])
                    i += 1
                if payload:
                    nested.append(shlex.join(payload).replace("{}", target))
            i += 1

    return nested


def _find_search_paths(args):
    """The paths find walks: leading operands, before the expression (an option, `!` or `(`)."""
    paths = []
    for a in args:
        if a.startswith("-") or a in ("!", "(", ")", ","):
            break
        paths.append(a)
    return paths or ["."]


# Predicates that narrow what find matches. `find . -name '*.tmp' -delete` is a
# targeted cleanup; `find . -delete` empties the working tree.
FIND_FILTERS = {
    "-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-lname",
    "-size", "-newer", "-newermt", "-mtime", "-mmin", "-ctime", "-atime",
    "-user", "-group", "-perm", "-samefile", "-inum", "-links",
}


def _find_delete_dangerous(args, cwd):
    """`find <path> -delete` removes as surely as rm -rf.

    Outside the working tree it is blocked whatever the filters: a mistake there
    is unrecoverable. Inside, only an unfiltered sweep is blocked — a filtered
    delete is routine and git can restore it.
    """
    if "-delete" not in args:
        return False
    filtered = any(a in FIND_FILTERS for a in args)
    for path in _find_search_paths(args):
        where = classify_target(path, cwd)
        if where == OUTSIDE:
            return True
        if where == ROOT and not filtered:
            return True  # `find . -delete` empties the tree
    return False


# --- main scan --------------------------------------------------------------

def _read_by_shell(before, after):
    """Whether a shell runs the heredoc as its script: `bash <<'EOF'`, `cat <<'EOF' | sh`."""
    def bare(text):
        first = segments(tokenize(text) or [])[:1]
        cmd, args = command_word(first[0]) if first else (None, [])
        return bool(cmd) and _shell_is_bare(cmd.split("/")[-1], _without_redirections(args))
    before_segments = segments(tokenize(before) or [])
    return bool(before_segments and bare(shlex.join(before_segments[-1]))) or (
        bool(re.match(r"\s*\|", after)) and bare(after.lstrip()[1:].lstrip("&")))


def scan(command, cwd=None, depth=0):
    """Return a block reason for `command`, or None to allow."""
    if depth > MAX_DEPTH:
        return None
    if cwd is None:
        cwd = os.getcwd()

    # Bodies first: a body line ending in `\` must not swallow its delimiter.
    command, bodies = strip_heredocs(command)
    for before, after, body in bodies:
        if _read_by_shell(before, after):
            reason = scan(body, cwd, depth + 1)
            if reason:
                return reason + " (in a heredoc a shell reads)"
    # A backslash before a newline joins the lines, unless it is itself escaped (`\\`).
    command = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1 ", strip_comments(command))
    tokens = tokenize(command)
    if tokens is None:
        # Unbalanced quotes used to disable the whole hook: `rm -rf /etc "` was
        # allowed because shlex raised and we failed open. Fall back to a raw
        # scan of the destructive shapes. A false positive here only costs a
        # retry, whereas failing open costs the filesystem.
        if UNPARSEABLE_DANGER.search(command):
            return "destructive command in an unparseable shell string (unbalanced quotes)"
        return None

    saw_net_fetch = False
    for seg in segments(tokens):
        cmd, args = command_word(seg)
        if cmd is None:
            continue
        base = cmd.split("/")[-1]

        if base == "rm" and _rm_dangerous(args, cwd):
            return "recursive rm on the working tree itself or on a path outside it"
        if base == "git" and _git_dangerous(args):
            return "destructive git command (reset --hard / push --force / clean -fdx)"
        if base in ("curl", "wget"):
            saw_net_fetch = True
        if saw_net_fetch and _shell_is_bare(base, args):
            return "piping a remote download into a shell (remote code execution)"
        if base == "dd" and any(
                a.startswith("of=/dev/")
                and not re.match(r'of=/dev/(?:null|zero|stdout|stderr)\b', a)
                for a in args):
            return "dd writing to a raw device"
        if base == "mkfs" or base.startswith("mkfs."):
            return "mkfs (filesystem format)"
        if base == "shred":
            return "shred (irreversible wipe)"
        if base == "find" and _find_delete_dangerous(args, cwd):
            return "find -delete rooted outside the working tree, or unfiltered at its root"
        if base == "chmod":
            recursive = any(
                (a.startswith("-") and not a.startswith("--") and "R" in a)
                or a == "--recursive" for a in args)
            if recursive and ("777" in args or "0777" in args) and any(
                    a.startswith(("/", "~")) or "$HOME" in a for a in args):
                return "chmod -R 777 on a system path"

        # Either invoked directly (`./deploy-wizard.sh`) or handed to a shell
        # (`bash build/scripts/db-setup.sh`).
        interactive = cmd if INTERACTIVE_SCRIPT.search(cmd) else next(
            (a for a in args if base in SHELLS and INTERACTIVE_SCRIPT.search(a)), None)
        if interactive:
            return (f"`{interactive.split('/')[-1]}` blocks on user input and would hang"
                    " an unattended run (look for a non-interactive variant or pass the"
                    " answers as flags)")
        if base == "make":
            target = _make_interactive_target(args)
            if target:
                return (f"`make {target}` is an interactive target and would hang an"
                        " unattended run (use a non-interactive variant if the project"
                        " has one, or pass the inputs explicitly)")

        # Redirect to a raw disk device.
        for j, t in enumerate(seg):
            if t in (">", ">|", ">>") and j + 1 < len(seg) and \
                    re.match(r'/dev/(?:sd|nvme|hd|mmcblk|disk)\w', seg[j + 1]):
                return "writing to a raw disk device"

        for inner in _nested_commands(base, args):
            reason = scan(inner, cwd, depth + 1)
            if reason:
                return reason + f" (wrapped in `{base}`)"

    # Fork bomb (string-level; the shape is distinctive enough).
    if re.search(r':\s*\(\s*\)\s*\{\s*:\s*\|\s*:?\s*&?\s*\}\s*;\s*:', command):
        return "fork bomb"
    return None


def main():
    data = hostio.payload()
    if data is None:
        sys.exit(0)  # fail-open on unreadable input

    command = hostio.bash_command(data)
    if not command.strip():
        sys.exit(0)

    reason = scan(command, hostio.cwd(data))
    if reason:
        hostio.deny(
            "BLOCKED by bash-firewall: " + reason + ".\n"
            "Blocked even in yolo mode. If you really intend it, run it yourself "
            "in a terminal.\n"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
