#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Davjack
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PreToolUse hook: guard Bash against catastrophic and session-killing commands.

Purpose: provide a hard floor that survives `--dangerously-skip-permissions`
(bypassPermissions). In that mode permission `deny` rules still apply but the
built-in circuit breaker only covers `rm -rf /` and `rm -rf ~`, so this hook is
the only place left to stop the rest.

It blocks three families:

  1. **Irreversible destruction** — the command cannot be undone and no commit
     brings the data back.
  2. **Commands that hang forever** — interactive wizards blocking on `read`.
     Harmless when you are watching, fatal for an unattended run: the session
     stalls with no error and no timeout. Blocking them (exit 2) hands Claude a
     reason it can act on, usually by finding the non-interactive variant.
  3. **Commands that take the host's hardware from the user**: a privileged
     container (`docker`/`podman` `run`/`create --privileged`, a Compose start
     of a `privileged: true` service) gets the host's devices, consoles
     included. On 17.11.2026 one booting systemd closed the desktop's graphical
     session. The reason points to the destination VM or a disposable VM. For
     this family only, the script a command runs (`bash s.sh`, `source s.sh`,
     `./s.sh`) is read whole, and of a Makefile the targets `make` runs, each
     like a call of its own: one that starts a privileged container on a VM
     through ssh is refused too. Compose files are read as Compose reads them
     (`-f`, the defaults, what they include, `COMPOSE_FILE`).

The third family runs in a second pass, only when the first two allow the
call, under a time budget, first without reading a file: a fault or an
overrun there allows the call rather than end the hook, which would lose a
refusal of the first pass.

Design goals:
  - NO false positives on data. A dangerous string inside a commit message, an
    `echo`, or a `grep` pattern is not a dangerous command. This is why the
    command is tokenized quote-aware and only the *command word* of each
    pipeline segment is inspected, instead of regex-scanning the whole string.
    Two deliberate exceptions scan the raw string: the fork bomb, whose shape is
    unmistakable, and the fallback used when tokenization fails. A heredoc body
    or a here-string is data too, unless a shell runs it: a bare shell
    (`bash <<'EOF'`), `source /dev/stdin`, or `ssh <host>` with no remote
    command or a bare remote shell.
  - Follow the command through wrappers — `bash -c`, `xargs`, `flock`, `su
    -c`, `watch`, `find -exec` — and into `$(…)` and backticks (an unquoted
    heredoc's too), because `bash -c "rm -rf /"` is the same command with a
    hat on.
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

import bisect
import collections
import contextlib
import functools
import os
import re
import shlex
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostio  # noqa: E402  (sibling module, resolved via the line above)

# --- tokenization -----------------------------------------------------------

OPERATORS = {"|", "||", "&&", ";", "&", "|&"}
PUNCTUATION = "();<>|&\n"  # an unquoted newline ends a command, like `;`
# Words that can legitimately precede the real command inside a segment.
PREFIXES = {"sudo", "doas", "command", "exec", "time", "nohup", "nice",
            "ionice", "setsid", "stdbuf", "env", "timeout", "systemd-run", "unbuffer"}
# Shell keywords that open a command position: `then rm -rf ~` runs rm.
KEYWORDS = {"(", "{", "!", "if", "then", "else", "elif", "while", "until", "do", "coproc"}
# `<<EOF`, `<<-'EOF'`, `<< "END-OF-TEXT"`, `<<\EOF`, not the here-string `<<<`.
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([^\s'\"<>|&;()]+))")
# A redirection operator as tokenize() returns it: `>`, `>>`, `>&`, `&>`, `<`, `>|`.
REDIRECTION = re.compile(r"&?[<>]+[&|]?")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}

MAX_DEPTH = 3  # bash -c inside xargs inside find is already pathological
MAX_SSH_HOPS = 4  # `ssh jump ssh vm bash -s` is two

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

# --- the host's hardware ----------------------------------------------------
# A privileged container gets the host's devices. Docker and Podman parse the
# flag with Go's strconv.ParseBool: these are its false forms, anything else is
# true or an error. There is no short form.
CONTAINER_CLIS = {"docker", "podman"}
PRIVILEGED_FALSE = {"false", "0", "f"}
# Docker and Podman global options that take a value, and the subcommands that
# start nothing, whose own arguments may hold a `run` (`exec c tool run …`).
CONTAINER_VALUE_OPTS = {"-H", "--host", "-c", "--context", "--connection", "--config",
                        "-l", "--log-level", "--url"}
CONTAINER_OTHERS = {"attach", "build", "builder", "buildx", "commit", "compose", "config",
                    "context", "cp", "diff", "events", "exec", "export", "history", "image",
                    "images", "import", "info", "inspect", "kill", "kube", "load", "login",
                    "logout", "logs", "machine", "manifest", "network", "pause", "plugin",
                    "pod", "port", "ps", "pull", "push", "rename", "restart", "rm", "rmi",
                    "save", "search", "secret", "service", "stack", "start", "stats", "stop",
                    "system", "tag", "top", "unpause", "update", "version", "volume", "wait"}
COMPOSE_CLIS = {"docker-compose", "podman-compose"}
# Compose subcommands that create or start containers (docker compose --help,
# Compose v5.7.1, 17.11.2026), and those that start none, whose own arguments
# may hold a start word (`exec app npm run build`).
COMPOSE_STARTS = {"up", "run", "create", "start", "restart", "scale", "watch"}
COMPOSE_OTHERS = {"attach", "build", "commit", "config", "cp", "down", "events", "exec",
                  "export", "images", "kill", "logs", "ls", "pause", "port", "ps", "publish",
                  "pull", "push", "rm", "stats", "stop", "top", "unpause", "version",
                  "volumes", "wait", "help"}
# Compose's global options that take a value, so that value is no subcommand.
COMPOSE_VALUE_OPTS = {"-f", "--file", "-p", "--project-name", "--project-directory",
                      "--env-file", "--profile", "--ansi", "--progress", "--parallel",
                      # podman-compose's own
                      "--podman-path", "--podman-args", "--podman-pull-args",
                      "--podman-push-args", "--podman-build-args", "--podman-inspect-args",
                      "--podman-run-args", "--podman-start-args", "--podman-stop-args",
                      "--podman-rm-args", "--podman-volume-args", "--in-pod", "--pod-args"}
# Union of the default file names of docker compose and podman-compose:
# reading one too many only errs toward refusing.
COMPOSE_DEFAULTS = [f"{stem}{mid}.{ext}"
                    for stem in ("compose", "docker-compose", "podman-compose", "container-compose")
                    for mid in ("", ".override") for ext in ("yaml", "yml")]
FILE_MAX_BYTES = 1 << 20  # a compose file or a script is read this far
# The second pass's budget (scan()): the host kills the hook at 10 s.
HARDWARE_SECONDS = 4
COMPOSE_MAX_FILES = 64  # files one Compose start reads, the files they name included
YAML_NAME = re.compile(r"\.(?:ya?ml|json)$")
# What separates the words of YAML or JSON text: a word ending in YAML_NAME is
# a file a compose file names (`include:`, `extends: file:`), which Compose
# reads too. A split, not a pattern over the text: one was quadratic on a long
# base64 run, and the alarm does not stop such a pattern (review of /ship).
YAML_SEPARATORS = re.compile(r"[\s'\"\[\]{}(),:#=]+")
# Where a write may make a compose or pod file: a YAML or JSON name, or Compose itself (`-f -`).
COMPOSE_WHERE = re.compile(r"\.(?:ya?ml|json)\b|\bcompose\b")
# `COMPOSE_FILE=a.yml:b.yml` in a project's `.env`.
COMPOSE_FILE_ENV = re.compile(
    r"^[ \t]*(?:export[ \t]+)?COMPOSE_FILE[ \t]*=[ \t]*['\"]?([^'\"\s#]+)", re.MULTILINE)
MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
# make's options that take a value, and those that run nothing.
MAKE_VALUE_OPTS = {"-f", "--file", "--makefile", "-C", "--directory", "-I", "--include-dir",
                   "-o", "--old-file", "--assume-old", "-W", "--what-if", "--new-file",
                   "--assume-new"}
MAKE_DRY_RUNS = {"-n", "--dry-run", "--just-print", "--recon", "-q", "--question"}
# A recipe line's `@`, `-`, `+` prefixes (its tab removed): the rest is shell.
MAKE_RECIPE = re.compile(r"^[@+\- \t]*")
MAKE_VARIABLE = re.compile(
    r"^(?:(?:export|override)[ \t]+)?([A-Za-z_][\w.-]*)[ \t]*([:+?!]{0,3})=[ \t]*(.*)$")
MAKE_RULE = re.compile(r"^([^\t#:=][^:=#]*?)[ \t]*::?(?!=)(.*)$")
MAKE_REFERENCE = re.compile(r"\$[({]([A-Za-z_][\w.-]*)[)}]")
# A `docker` or `podman` word in text no tokenizer reads, even quoted (`os.system("docker run …")`).
RAW_CLI = re.compile(r"(?:^|[\s;&|(/\"'\[,=])(?:docker|podman)\b")
# Compose named by its CLI (`docker compose`, `podman-compose`, `"docker", "compose"`), not prose.
RAW_COMPOSE = re.compile(r"\b(?:docker|podman)(?:-|[\s\"',]+)compose\b")
# The same in a script of another language, where a command is a string
# (`os.system("docker run …")`, `["docker", "run", …]`) and prose is not.
QUOTED_CLI = re.compile(r"[\"']\s*(?:docker|podman)\b")
QUOTED_COMPOSE = re.compile(r"[\"']\s*(?:docker|podman)(?:-|[\s\"',]+)compose\b")
SCRIPT_SUFFIX = (" (in the script `{}` it runs, read whole: one that starts a privileged"
                 " container on a VM through ssh is refused too; run that ssh command directly)")
# The key as Compose spells it, outside a YAML comment, with a true value in
# any case; a quoted key too: `"privileged": true` in a JSON file (`-f x.json`).
# An environment variable `PRIVILEGED: 'true'` is no setting.
PRIVILEGED_YAML = re.compile(
    r"^[^#\n]*?(?<![\w-])privileged['\"]?\s*:\s*['\"]?(?i:true|yes|on)\b", re.MULTILINE)
# A privileged flag anywhere in a word: `--podman-run-args=--privileged`.
PRIVILEGED_FLAG = re.compile(r"--privileged(?:=[^\s'\";&|()]*)?")
PRIVILEGED_WHY = ("a privileged container gets the host's devices, consoles included;"
                  " on 17.11.2026 one booting systemd closed the desktop's graphical session")
PRIVILEGED_ADVICE = ("try it on the destination VM or in a disposable VM, e.g. `ssh <vm>"
                     " '<command>'`, or leave the command to the user, who can run it themselves")
PRIVILEGED_REASON = f"privileged container (--privileged): {PRIVILEGED_WHY}; {PRIVILEGED_ADVICE}"
COMPOSE_REASON = (f"compose start of a privileged service (privileged: true, or --privileged"
                  f" handed to podman): {PRIVILEGED_WHY}; {PRIVILEGED_ADVICE}")
# Output redirections that write a file: text they write may be a compose file
# the hook cannot read yet.
FILE_WRITES = {">", ">>", ">|", "&>", "&>>"}

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
    """(the command without its heredoc bodies, [(text before `<<`, text after,
    body, whether it expands)]).

    A body is data unless a shell reads it, which scan() checks. It runs from
    the line after its `<<WORD` (after the logical line, when a `\\` continues
    it) to the line holding only WORD (leading tabs allowed, for `<<-`); the
    bodies of one line follow each other. A `<<` with no such line (inside
    quotes, in arithmetic) is no heredoc: its lines stay commands. A body
    whose delimiter is unquoted expands its `$(…)` and backticks, unless its
    `<<` sits inside quotes (a commit message quoting `cat <<EOF`): then it is
    text, removed as before but never expanded.
    """
    lines, kept, bodies, i = command.split("\n"), [], [], 0
    # Where each delimiter line sits: looking ahead line by line for every `<<`
    # was quadratic on a long script of `$(( x << 3 ))` lines (no end line).
    ends = {}
    for j, text in enumerate(lines):
        ends.setdefault(text.lstrip("\t").rstrip(" \t"), []).append(j)
    quote = None  # the quote open where the line starts
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
            found = ends.get(word, [])
            at = bisect.bisect_left(found, i)
            end = found[at] if at < len(found) else None
            if end is not None:
                expands = (bool(match.group(3)) and "\\" not in match.group(0)
                           and _quote_after(line[:match.start()], quote) is None)
                bodies.append((line[:match.start()], line[match.end():], "\n".join(lines[i:end]), expands))
                i = end + 1
        quote = _quote_after(line + "\n", quote)
    return "\n".join(kept), bodies


def _quote_after(text, quote=None):
    """The quote open after `text` (None, "'", '"' or "$'"), from inside
    `quote`; a comment opening a word, outside quotes, runs to its line's end."""
    i = 0
    while i < len(text):
        char = text[i]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            i += 1
        elif quote:
            quote = None if char == quote[-1] else quote
        elif text.startswith("$'", i):
            quote, i = "$'", i + 1
        elif char in "'\"":
            quote = char
        elif char == "#" and (i == 0 or text[i - 1] in " \t\n;&|()"):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        i += 1
    return quote


def _flatten(command):
    """(command without its heredoc bodies and comments, its lines joined
    where a backslash continues them; the bodies, as strip_heredocs() gives).

    Bodies first: a body line ending in a backslash must not swallow its delimiter.
    """
    command, bodies = strip_heredocs(command)
    # A backslash before a newline joins the lines, unless it is itself escaped (`\\`).
    return re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1 ", strip_comments(command)), bodies


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


# The options of each prefix command that take a value (their manuals): the
# short letters a cluster may end in (`sudo -iu root`) and the long names
# (getopt accepts them abbreviated). One shared set swallowed the command word
# of `sudo -s rm -rf /`, `time -p …` and `setsid -c …`, whose letters are
# flags there, and missed the value of `sudo -iu root` (review of /ship,
# 17.11.2026). Prefixes that take a positional operand of their own
# (`timeout 300 cmd`): without it, `timeout 300 rm -rf /` resolved its command
# word to `300` and sailed through.
PREFIX_OPTIONS = {
    "sudo": ("ugpCDrtTUR", ("--user", "--group", "--prompt", "--chdir", "--close-from",
                            "--host", "--role", "--type", "--other-user", "--command-timeout",
                            "--chroot")),
    "doas": ("uC", ()),
    "exec": ("a", ()),
    "time": ("fo", ("--format", "--output")),
    "nice": ("n", ("--adjustment",)),
    "ionice": ("cnpPu", ("--class", "--classdata", "--pid", "--pgid", "--uid")),
    "stdbuf": ("ioe", ("--input", "--output", "--error")),
    "env": ("uCSa", ("--unset", "--chdir", "--split-string", "--argv0")),
    "timeout": ("sk", ("--signal", "--kill-after")),
    # systemd-run's `--user` is a bare flag: heavy jobs run as `systemd-run
    # --user --scope ... nice -n 10 <cmd>`, unseen until it was listed apart.
    "systemd-run": ("HMupE", (
        "--host", "--machine", "--unit", "--property", "--description", "--slice",
        "--expand-environment", "--service-type", "--uid", "--gid", "--nice",
        "--working-directory", "--root-directory", "--setenv", "--json", "--job-mode",
        "--background", "--path-property", "--socket-property", "--timer-property",
        "--on-active", "--on-boot", "--on-startup", "--on-unit-active",
        "--on-unit-inactive", "--on-calendar")),
}
PREFIX_OPERANDS = {"timeout": 1}
# d5c9106's reading, kept beside the one above for the first pass: one set of
# value options for every prefix but systemd-run, matched whole, and no
# prefix named by its path. Both readings are checked, so no form d5c9106
# refused passes, even one whose dangerous word is in truth an option's value
# (`sudo -t find … -delete`: inert, but refused before).
LEGACY_VALUE_OPTS = {"-u", "-g", "-p", "-n", "-c", "-k", "-s", "-w", "-C", "-D", "--user",
                     "--group", "--prompt", "--adjustment", "--class", "--classdata",
                     "--kill-after", "--signal", "--chdir", "--close-from"}
LEGACY_PREFIXES = PREFIXES - {"unbuffer"}


def command_word(seg, legacy=False):
    """Return (command, args) after skipping env-assignments and prefix words;
    with `legacy`, as d5c9106 read them (LEGACY_VALUE_OPTS)."""
    i = 0
    operands_to_skip = 0
    letters, longs, whole = "", (), LEGACY_VALUE_OPTS
    while i < len(seg):
        t = seg[i]
        if re.match(r'^\w+=', t) or t in KEYWORDS:
            i += 1
            continue
        # A prefix named by its path is the same prefix (`/usr/bin/sudo rm -rf /`),
        # unless a prefix's operand comes first: `timeout /usr/bin/env …` is a duration.
        prefix = os.path.basename(t) if t.startswith("/") and not operands_to_skip and not legacy else t
        if prefix in (LEGACY_PREFIXES if legacy else PREFIXES):
            operands_to_skip += PREFIX_OPERANDS.get(prefix, 0)
            letters, longs = PREFIX_OPTIONS.get(prefix, ("", ()))
            whole = ({f"-{c}" for c in letters} | set(longs) if prefix == "systemd-run"
                     else LEGACY_VALUE_OPTS)
            i += 1
            continue
        if t.startswith("-"):
            # A flag can only belong to a prefix we already skipped.
            if i == 0:
                break
            takes = t in whole if legacy else _takes_next(t, letters, longs)
            if takes and i + 1 < len(seg) and not seg[i + 1].startswith("-"):
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


def _takes_next(option, letters, longs=()):
    """Whether `option` consumes the following token as its value.

    A short cluster passes its value in the rest of the token (`-w5`), or in
    the next token when the value letter ends it (`-nw 5`); a long option in
    the next token, unless it carries `=`.
    """
    if option.startswith("--"):
        return "=" not in option and len(option) > 2 and any(
            name.startswith(option) for name in longs)
    letter = next((i for i, char in enumerate(option[1:]) if char in letters), None)
    return letter == len(option) - 2


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
    """A shell reading its script from stdin: no script file, no -c payload.

    Also `sh -s -- arg` (after `-s`, words are the script's arguments),
    `bash -o pipefail` (an option value is no script file) and a script named
    after standard input (`sh /dev/stdin`).
    """
    if base not in SHELLS or "-c" in args:
        return False
    if all(a.startswith("-") for a in args):
        return True
    script = _shell_script(args)
    return script is None or script in STDIN_PATHS


def _shell_script(args):
    """The script file a shell's arguments name: None when it reads its
    standard input (no file, or `-s`, after which words are arguments), ""
    with a `-c` payload."""
    from_stdin, i = False, 0
    while i < len(args):
        a = args[i]
        if a in ("--", "-"):
            return None if from_stdin or i + 1 >= len(args) else args[i + 1]
        if not a.startswith(("-", "+")):
            return None if from_stdin else a
        if not a.startswith("--"):
            if "c" in a[1:]:
                return ""  # a -c payload follows
            from_stdin = from_stdin or "s" in a[1:]
        i += 2 if _takes_next(a, "oO", ("--rcfile", "--init-file")) else 1
    return None


def _scripts_run(cmd, base, args):
    """[(file, how, make targets)] of what `cmd args` runs as a script: `bash
    s.sh`, `bash < s.sh`, `source s.sh` ("script"); `./s.sh`, run by its path
    ("exec"); the Makefile `make` reads ("make"), not for a dry run."""
    plain = _without_redirections(args)
    script = None
    if base in SHELLS:
        script = _shell_script(plain)
        if script is None:  # standard input, perhaps a file: `bash < s.sh`
            script = next((args[j + 1] for j, a in enumerate(args[:-1]) if a == "<"), None)
    elif base in ("source", "."):
        script = next((a for a in plain if not a.startswith("-")), None)
    elif base == "make":
        if MAKE_DRY_RUNS & set(plain):
            return []
        names, targets = _make_operands(plain)
        return [(name, "make", targets) for name in names]
    elif "/" in cmd:
        return [(cmd, "exec", ())]
    return [(script, "script", ())] if script and script not in STDIN_PATHS else []


def _make_operands(args):
    """(the makefiles `make args` reads: its `-f` files, else the default names,
    under its `-C` directory; the targets it names)."""
    directory, files, targets, i = "", [], [], 0
    while i < len(args):
        a, value = args[i], args[i + 1] if i + 1 < len(args) else ""
        if a in MAKE_VALUE_OPTS:
            if a in ("-C", "--directory"):
                directory = os.path.join(directory, value)
            elif a in ("-f", "--file", "--makefile"):
                files.append(value)
            i += 2
            continue
        if a.startswith(("--file=", "--makefile=")):
            files.append(a.split("=", 1)[1])
        elif a.startswith("--directory="):
            directory = os.path.join(directory, a.split("=", 1)[1])
        elif a.startswith("-f") and len(a) > 2:
            files.append(a[2:])
        elif a.startswith("-C") and len(a) > 2:
            directory = os.path.join(directory, a[2:])
        elif not a.startswith("-") and "=" not in a:
            targets.append(a)
        i += 1
    return [os.path.join(directory, name) for name in files or MAKEFILES if name], tuple(targets)


def _make_recipes(text, targets):
    """The shell a make run executes: the recipes of `targets` (the first
    target when none is named) and of their prerequisites, their make
    variables expanded (`$(RUN) $(PRIV)`). Comments, the other targets and
    echoed text stay out: reading the whole Makefile refused `make lint` for a
    privileged `vm-test` target, or for a comment naming the flag (review of
    /ship, 17.11.2026)."""
    variables, rules, order, current = {}, {}, [], []
    for line in text.replace("\\\n", " ").split("\n"):
        if line.startswith("\t"):
            for target in current:
                rules[target][1].append(MAKE_RECIPE.sub("", line))
            continue
        line = line.split("#", 1)[0]
        if not line.strip():
            continue  # a blank or comment line does not end a recipe
        current = []
        rule = None if MAKE_VARIABLE.match(line) else MAKE_RULE.match(line)
        assignment = MAKE_VARIABLE.match(rule.group(2).strip() if rule else line)
        if assignment:  # a target-specific value (`up: PRIV=…`) counts for all: it errs toward refusing
            name, kind, value = assignment.groups()
            if "+" in kind:
                value = f"{variables.get(name, '')} {value}"
            if "?" not in kind or name not in variables:
                variables[name] = value.strip()
            continue
        if rule:
            prerequisites, _, inline = rule.group(2).partition(";")
            current = rule.group(1).split()
            for target in current:
                rules.setdefault(target, ([], []))[0].extend(prerequisites.split())
                rules[target][1].extend([inline.strip()] if inline.strip() else [])
                order += [] if target in order else [target]
    wanted = list(targets) or [t for t in order if not t.startswith(".")][:1]
    seen, recipes = set(), []
    while wanted:
        target = wanted.pop(0)
        if target in seen or target not in rules:
            continue
        seen.add(target)
        wanted += rules[target][0]
        recipes += rules[target][1]
    text = "\n".join(recipes)
    for _round in range(4):  # a variable may name another
        text = MAKE_REFERENCE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
    return text.replace("$$", "$")


# Names of standard input that `source /dev/stdin` reads as a script.
STDIN_PATHS = {"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
# ssh's options that take a value (OpenSSH's usage line).
SSH_VALUE_LETTERS = "BbcDEeFIiJLlmOoPpQRSWw"


def _ssh_remote(args):
    """The remote command of `ssh [opts] <host> [opts] [cmd…]`, or None without a host.

    ssh reads options after the host too, up to the command.
    """
    i, host = 0, None
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            if host is None and i < len(args):
                host, i = args[i], i + 1
            break
        if a.startswith("-") and len(a) > 1:
            i += 2 if _takes_next(a, SSH_VALUE_LETTERS) else 1
        elif host is None:
            host, i = a, i + 1
        else:
            break
    return None if host is None else " ".join(args[i:])


def _reads_script(cmd, args, hops=MAX_SSH_HOPS):
    """Who runs the standard input of `cmd args` as shell commands: "local",
    "remote" (another host, whose files the hook cannot read), or None.

    A bare shell (`bash`, `sudo sh -s`), `source /dev/stdin`, the command
    `flock` wraps, and `ssh <host>` whose remote side is a login shell (no
    command) or holds a bare shell (`ssh vm bash -s`, `ssh vm 'cd /srv && bash
    -s'`). At most `hops` wrappers are peeled, then the input counts as read:
    recursing once per host let a long `ssh a ssh b …` chain crash the hook,
    which fails open.
    """
    base, args = cmd.split("/")[-1], _without_redirections(args)
    if base in ("source", "."):
        return "local" if next((a for a in args if not a.startswith("-")), None) in STDIN_PATHS else None
    if base not in ("ssh", "flock"):
        return "local" if _shell_is_bare(base, args) else None
    if hops <= 0:
        return "local"
    if base == "flock":
        wrapped = _flock_command(args)
        return _text_reads_script(wrapped, hops - 1) if wrapped else None
    remote = _ssh_remote(args)
    tokens = None if remote is None else tokenize(remote)
    if tokens is None:
        return None  # no host, or a remote command no shell can parse
    commands = [first for first in map(command_word, segments(tokens)) if first[0]]
    if not commands or any(_reads_script(*first, hops - 1) for first in commands):
        return "remote"  # a login shell, or a remote shell, reads it
    return None


def _text_reads_script(text, hops=MAX_SSH_HOPS):
    """Who runs the standard input of the first command of `text` as a script
    (_reads_script), or None; the first command read both ways, as
    _readings() does (d5c9106's reading decided it before)."""
    first = segments(tokenize(text) or [])[:1]
    readings = [command_word(first[0]), command_word(first[0], legacy=True)] if first else []
    return next(filter(None, (_reads_script(cmd, args, hops) for cmd, args in readings if cmd)), None)


def _privileged_flag(arg):
    """`--privileged`, or `--privileged=<value>` with a value that is no false form."""
    return arg == "--privileged" or (
        arg.startswith("--privileged=") and arg.split("=", 1)[1].lower() not in PRIVILEGED_FALSE)


def _subcommand(args, starts, others, value_opts):
    """Index of the first word of `starts`, unless a word of `others` comes first.

    Options and the values of `value_opts` are skipped, and so is an unknown
    word (a group such as `container`, an unlisted option's value): the walk
    errs toward finding the start word.
    """
    i = 0
    while i < len(args):
        if args[i] in starts:
            return i
        if args[i] in others:
            return None
        i += 2 if args[i] in value_opts else 1
    return None


def _container_privileged(args):
    """`run` or `create` (also under `container`) with a privileged flag after it.

    A flag after the image goes to the container's command, not to Docker:
    refused all the same, the safe direction.
    """
    sub = _subcommand(args, {"run", "create"}, CONTAINER_OTHERS, CONTAINER_VALUE_OPTS)
    return sub is not None and any(_privileged_flag(a) for a in args[sub + 1:])


def _kube_privileged(args, ctx):
    """`podman kube play <file>` (also `play kube`) of a pod whose file, or the
    text this call writes, says `privileged: true` (a `securityContext`)."""
    if not {"kube", "play"} <= set(args) or "--down" in args:
        return False
    return _written_says_privileged(ctx) or _files_say_privileged(
        _under(folder, a) for folder in ctx.folders for a in args if YAML_NAME.search(a))


def _compose_privileged(base, args, ctx):
    """A Compose start of a privileged service: by a `--privileged` handed to
    podman (`--podman-run-args`), by the text this call writes to files (a
    compose file written in the same call does not exist yet), or by a file
    it reads from a folder of the call (_compose_paths).
    """
    if base in CONTAINER_CLIS and "compose" in args:
        args = args[args.index("compose") + 1:]
    elif base not in COMPOSE_CLIS:
        return False
    start = _subcommand(args, COMPOSE_STARTS, COMPOSE_OTHERS, COMPOSE_VALUE_OPTS)
    if start is None:
        return False
    # `up --help` starts nothing; a `--help` after the service goes to the container.
    rest = args[start + 1:]
    end = next((j for j, a in enumerate(rest) if not a.startswith("-")), len(rest))
    if {"--help", "-h"} & set(rest[:end]):
        return False
    options = args[:start]
    if any(_privileged_flag(flag) for a in options for flag in PRIVILEGED_FLAG.findall(a)):
        return True
    if _written_says_privileged(ctx):
        return True
    files, project = [], None
    for a, value in zip(options, options[1:] + [""]):
        if a in ("-f", "--file"):
            files.append(value)
        elif a.startswith("--file="):
            files.append(a.split("=", 1)[1])
        elif a.startswith("-f") and not a.startswith("--"):
            files.append(a[2:].lstrip("="))  # `-fx`, `-f=x`
        elif a == "--project-directory":
            project = value
        elif a.startswith("--project-directory="):
            project = a.split("=", 1)[1]
    return _files_say_privileged(path for here in ctx.folders
                                 for path in _compose_paths(files, project, here, ctx.named))


def _compose_named(segs):
    """The files the segments hand Compose beyond `-f` and the defaults, as
    (kind, word): `COMPOSE_FILE=…` ("env", any word of the call); what a
    segment feeds standard input, read for `-f -` ("stdin": `< x.yml`, `cat
    x.yml`); the source of a copy onto a default name ("copy": `cp x.yml
    compose.override.yaml`). Not every YAML word: `kubectl apply -f k8s.yaml`
    hands Compose nothing (review of /ship)."""
    named = []
    for seg in segs:
        named += [("env", t.split("=", 1)[1]) for t in seg if t.startswith("COMPOSE_FILE=")]
        named += [("stdin", seg[j + 1]) for j, t in enumerate(seg[:-1]) if t == "<"]
        cmd, args = command_word(seg)
        base = (cmd or "").split("/")[-1]
        plain = [a for a in _without_redirections(args) if not a.startswith("-")]
        if base == "cat":
            named += [("stdin", a) for a in plain]
        elif base in ("cp", "mv", "ln", "install") and plain[1:] and (
                os.path.basename(plain[-1]) in COMPOSE_DEFAULTS):
            named += [("copy", a) for a in plain[:-1]]
    return named


def _compose_paths(files, project, here, named):
    """The files a Compose call reads from `here`: its `-f` files, else the
    defaults Compose finds (_compose_defaults). Also, erring toward refusing,
    `COMPOSE_FILE` (in the call or the project's `.env`) and the files the
    call hands Compose another way (`named`, _compose_named).
    """
    folder = os.path.abspath(_under(here, project) if project else here)
    paths = ([_under(here, name) for name in files if name not in ("", "-")] if files
             else list(_compose_defaults(folder)))
    names = [name for kind, word in named if kind != "stdin" or "-" in files
             for name in (word.split(":") if kind == "env" else [word])]
    env = [name for match in COMPOSE_FILE_ENV.finditer(_read_file(os.path.join(folder, ".env")))
           for name in match.group(1).split(":")]
    return paths + [_under(here, name) for name in names if name] + [
        _under(folder, name) for name in env if name]


@functools.lru_cache(maxsize=None)
def _compose_defaults(folder):
    """The default compose files of the first directory holding one, from
    `folder` up (measured with Compose v5.7.1: a child directory without its own
    file uses its parent's, the override merged in)."""
    while True:
        found = tuple(os.path.join(folder, name) for name in COMPOSE_DEFAULTS
                      if os.path.isfile(os.path.join(folder, name)))
        parent = os.path.dirname(folder)
        if found or parent == folder:
            return found
        folder = parent


def _files_say_privileged(paths):
    """Whether one of `paths`, or a YAML or JSON file one of them names
    (`include:`, `extends: file:`), says `privileged: true`; at most
    COMPOSE_MAX_FILES files."""
    queue, seen = list(paths), set()
    while queue and len(seen) < COMPOSE_MAX_FILES:
        path = os.path.normpath(queue.pop(0))
        if path in seen:
            continue
        seen.add(path)
        privileged, names = _compose_file(path)
        if privileged:
            return True
        queue += names
    return False


@functools.lru_cache(maxsize=None)
def _compose_file(path):
    """(whether the file says `privileged: true`, the YAML and JSON files it names)."""
    text = _read_file(path)
    names = [word for word in YAML_SEPARATORS.split(text) if YAML_NAME.search(word)]
    return bool(PRIVILEGED_YAML.search(text)), tuple(_under(os.path.dirname(path), n) for n in names)


@functools.lru_cache(maxsize=None)
def _says_privileged(text):
    """Whether written text says `privileged: true`, its `\\n` read as line
    breaks (printf, `echo -e`)."""
    return bool(PRIVILEGED_YAML.search(text.replace("\\n", "\n")))


def _writes(tokens, bodies):
    """What a call writes to files, as (where, text) pairs: heredoc bodies
    written to a file or fed to Compose (where: the text around their `<<`;
    a commit message or a `python3 - <<EOF` script is none), and the words of
    a pipeline whose output goes to a file (`printf '…' > compose.yml`, `echo
    '…' | tee -a compose.yaml`)."""
    def writes(seg):
        cmd, _args = command_word(seg)
        return (cmd or "").split("/")[-1] == "tee" or any(
            t in FILE_WRITES and not seg[i + 1:i + 2] == ["/dev/null"]
            for i, t in enumerate(seg))
    def feeds_compose(seg):
        cmd, args = command_word(seg)
        base = (cmd or "").split("/")[-1]
        return base in COMPOSE_CLIS or (base in CONTAINER_CLIS and "compose" in args)

    def written(line):  # a heredoc's own line writes it to a file, or feeds Compose (`-f -`)
        segs = segments(tokenize(line) or [])
        return any(writes(seg) or feeds_compose(seg) for seg in segs)
    pipe = "\0|"  # a word no command holds: one pipeline per segment
    pipelines = [[t if t != pipe else "|" for t in p]
                 for p in segments([pipe if t in ("|", "|&") else t for t in tokens])]
    return [(before + after, body) for before, after, body, _expands in bodies
            if written(before + after)] + [
        (" ".join(p),) * 2 for p in pipelines if any(writes(seg) for seg in segments(p))]


def _written_text(writes, name):
    """The text of `writes` to the file `name` (a word of `where` with its
    base name: `deploy.sh` is not `test-deploy.sh`), its `\\n` read as line
    breaks (printf, `echo -e`)."""
    base = os.path.basename(name)
    return "\n".join(text for where, text in writes if base in map(
        os.path.basename, re.split(r"[\s<>|&;()'\"]+", where))).replace("\\n", "\n")


def _written_says_privileged(ctx):
    """Whether this call writes `privileged: true` where a compose or pod file
    may be (COMPOSE_WHERE): prose in a `notes.md` or a PR body is none."""
    return any(_says_privileged(text) for where, text in ctx.writes if COMPOSE_WHERE.search(where))


@functools.lru_cache(maxsize=None)
def _read_file(path, executable=False):
    """The first MiB of a regular file, "" when there is none. With
    `executable` (a command run by its path), only a script: a `#!` line, or a
    text file the user may execute, which the shell runs itself; a binary holds
    a NUL byte early.

    Never a FIFO or a device (`-f /dev/stdin`): reading one would hang the
    hook. Cached for one pass of scan(): a long script names the same files often.
    """
    try:
        if not os.path.isfile(path):
            return ""
        with open(path, "rb") as fh:
            head = fh.read(1024)
            if executable and not head.startswith(b"#!") and (
                    b"\0" in head or not os.access(path, os.X_OK)):
                return ""
            return (head + fh.read(FILE_MAX_BYTES - len(head))).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def _under(folder, name):
    """`name` from `folder`, `~` expanded; a NUL byte, which no path holds,
    leaves it unexpanded rather than raise: an exception exits 1, which hosts
    treat as non-blocking, and would lose every refusal of the call."""
    try:
        return os.path.join(folder, os.path.expanduser(name))
    except ValueError:
        return os.path.join(folder, name)


def _visited_folders(segs, folders):
    """`folders` and the target of every `cd` or `pushd` in `segs`, each from
    the last; none on a remote host (`folders` empty), whose files the hook
    cannot read."""
    folders = list(folders)
    for seg in segs if folders else ():
        cmd, args = command_word(seg)
        if cmd and cmd.split("/")[-1] in ("cd", "pushd"):
            folders.append(_cd(args, folders[-1]))
    return tuple(folders)


def _cd(args, here):
    """Where `cd args` (or `pushd args`) leads from `here`, or `here` when the
    text cannot tell (`cd -`, `cd $X`)."""
    operand = next((a for a in _without_redirections(args) if a == "-" or not a.startswith("-")), "~")
    if operand == "-" or "$" in operand:
        return here
    return _under(here, operand)


def _unparseable_privileged(command, original, ctx):
    """The host's hardware in a string the tokenizer cannot parse, line by
    line: a line that parses alone is scanned as a command, the others are
    matched raw (_raw_privileged). In a script read whole, a privileged flag
    anywhere counts, as _wrapped_privileged() does for a parsed one."""
    lines = command.split("\n")
    parsed = [tokenize(line) for line in lines]
    segs = [seg for tokens in parsed if tokens for seg in segments(tokens)]
    # Every line's folders, and the whole text as written: the line that
    # writes a compose file may be the one that cannot be parsed.
    inner = _inner(ctx)._replace(folders=_visited_folders(segs, ctx.folders),
                                 writes=ctx.writes + ((original, original),))
    for line, tokens in zip(lines, parsed):
        reason = tokens is not None and _scan(line, inner)
        if reason:
            return reason
    anywhere = ctx.whole and any(_privileged_flag(flag) for flag in PRIVILEGED_FLAG.findall(command))
    return _raw_privileged([line for line, tokens in zip(lines, parsed) if tokens is None]
                           + (lines if anywhere else []), original, inner, anywhere)


def _raw_privileged(lines, original, ctx, anywhere=False, quoted=False):
    """The host's hardware in lines no tokenizer reads (an unbalanced quote, a
    script in another language).

    The first `docker`/`podman` of a line, the first `run`/`create` after it,
    then a privileged flag (anywhere in the text, with `anywhere`): when any
    such triple exists, this one does, and finding it stays linear in the
    line's length. A Compose start, its subcommand found as Compose finds it,
    privileged by a flag on its line, by `privileged: true` in the text, or by
    a compose file of the call's folders. With `quoted` (another language),
    only a CLI opening a string counts.
    """
    files_say = None
    for line in lines:
        cli = (QUOTED_CLI if quoted else RAW_CLI).search(line)
        rest = line[cli.end():] if cli else ""
        sub = re.search(r"\b(?:run|create)\b", rest)
        if sub and (anywhere or any(_privileged_flag(flag)
                                    for flag in PRIVILEGED_FLAG.findall(rest[sub.end():]))):
            return PRIVILEGED_REASON
        compose = (QUOTED_COMPOSE if quoted else RAW_COMPOSE).search(line)
        words = re.findall(r"[^\s'\",\[\]()]+", line[compose.end():]) if compose else []
        if _subcommand(words, COMPOSE_STARTS, COMPOSE_OTHERS, COMPOSE_VALUE_OPTS) is None:
            continue
        if files_say is None:
            files_say = bool(PRIVILEGED_YAML.search(original)) or _files_say_privileged(
                path for here in ctx.folders for path in _compose_paths([], None, here, ctx.named))
        if files_say or any(_privileged_flag(flag) for flag in PRIVILEGED_FLAG.findall(line)):
            return COMPOSE_REASON
    return None


def _wrapped_privileged(segs, tokens):
    """A script that runs `docker`/`podman` `run` or `create` somewhere and
    holds a privileged flag anywhere: a function often wraps `docker run`
    (`run() { docker run "$@"; }`, then `run box --privileged`)."""
    return any(_privileged_flag(t) for t in tokens) and any(
        cmd and cmd.split("/")[-1] in CONTAINER_CLIS and _subcommand(
            args, {"run", "create"}, CONTAINER_OTHERS, CONTAINER_VALUE_OPTS) is not None
        for cmd, args in map(command_word, segs))


def _script_privileged(cmd, base, args, ctx):
    """The host's hardware in what `cmd args` runs as a script (a script, a
    Makefile), read whole from each folder of the call (a Makefile from the
    last one, where make runs), or in the text this call writes to that file:
    it may not exist yet."""
    for name, how, targets in _scripts_run(cmd, base, args):
        folders = ctx.folders[-1:] if how == "make" else ctx.folders
        paths = dict.fromkeys(os.path.normpath(_under(folder, name)) for folder in folders)
        texts = [(path, _read_file(path, how == "exec")) for path in paths]
        for path, text in texts + [("", _written_text(ctx.writes, name))]:
            reason = text and _text_privileged(text, how, targets, path, ctx)
            if reason:
                return reason + SCRIPT_SUFFIX.format(name)
    return None


def _text_privileged(text, how, targets, path, ctx):
    """The host's hardware in a script's text, read like a call of its own,
    from the call's folders and the script's own; a script in another language
    (`#!/usr/bin/env python3`) is matched raw, line by line. Of a Makefile,
    the recipes `targets` run (_make_recipes)."""
    first = text.split("\n", 1)[0]
    if how == "exec" and first.startswith("#!") and not any(
            os.path.basename(word) in SHELLS for word in first[2:].split()):
        # Line by line, and its own text says nothing of Compose: such a file
        # names the flag or the setting as often as it uses them.
        return _raw_privileged(text.split("\n"), "", ctx, quoted=True)
    if how == "make":
        text = _make_recipes(text, targets)
    folders = ctx.folders + ((os.path.dirname(path),) if path else ())
    return _scan(text, _inner(ctx)._replace(folders=folders, script=True, whole=True))


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
# flock's options that take a value (util-linux): the short letters, which a
# cluster ending in one (`-nw 5`) shares, and the long names, which getopt also
# accepts abbreviated (`--time 5`).
FLOCK_VALUE_LETTERS = "wE"
FLOCK_VALUE_LONGS = ("--timeout", "--wait", "--conflict-exit-code")


def _nested_commands(base, args):
    """Return the command strings this one wraps, to be scanned in turn."""
    nested = []

    if base in SHELLS:
        # Any short option cluster holding `c` takes the string: `-c`, `-lc`, `-ec`, `-euxc`.
        nested.extend(args[i + 1] for i, a in enumerate(args[:-1])
                      if a.startswith("-") and not a.startswith("--") and "c" in a)

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

    elif base == "flock":
        wrapped = _flock_command(args)
        if wrapped:
            nested.append(wrapped)

    elif base in ("su", "sg", "runuser", "script"):
        # A command string after `-c` (`su -c '…'`, `su -lc '…'`, `sg docker -c '…'`,
        # `script -qc '…' out`); runuser's command after its user (`runuser -u x -- cmd`);
        # sg's command after its group (review of /ship, 17.11.2026).
        strings = [args[i + 1] for i, a in enumerate(args[:-1])
                   if a == "--command" or (a.startswith("-") and not a.startswith("--") and a.endswith("c"))]
        strings += [a.split("=", 1)[1] for a in args if a.startswith("--command=")]
        user = next((i for i, a in enumerate(args) if a in ("-u", "--user")), None)
        rest = args[user + 2:] if base == "runuser" and user is not None else (
            args[1:] if base == "sg" and not strings else [])
        rest = rest[1:] if rest[:1] == ["--"] else rest
        nested.extend(strings + ([" ".join(rest)] if rest and not rest[0].startswith("-") else []))

    elif base == "watch":
        # watch hands its words, joined, to `sh -c`.
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if _takes_next(args[i], "n", ("--interval",)) else 1
        if i < len(args):
            nested.append(" ".join(args[i:]))

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


def _flock_command(args):
    """The command `flock [opts] <lock> <cmd…>` or `flock [opts] <lock> -c
    '<cmd>'` runs, or None. Options stop at the lock path: `-c` is only a
    command string right after it, and `flock <fd>` runs nothing."""
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "-":
        i += 2 if _takes_next(args[i], FLOCK_VALUE_LETTERS, FLOCK_VALUE_LONGS) else 1
    rest = args[i + 1:]
    if rest[:1] in (["-c"], ["--command"]):
        return rest[1] if len(rest) > 1 else None
    return shlex.join(rest) if rest else None


def _substitutions(command):
    """The commands of the `$(…)` and backticks of `command` outside single
    quotes: the shell runs them first, wherever they sit (`cid="$(docker run
    …)"`). A backslash escapes; `$((…))` is arithmetic; in ANSI-C quoting
    (`$'don\\'t'`) a backslash escapes the quote too."""
    found, i, quote = [], 0, None
    while i < len(command):
        char = command[i]
        if quote == "'":
            quote = None if char == "'" else quote
        elif quote == "$'":
            if char == "\\":
                i += 1
            elif char == "'":
                quote = None
        elif char == "\\":
            i += 1
        elif quote is None and command.startswith("$'", i):
            quote, i = "$'", i + 1
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
            quote = None if quote == '"' else '"'
        elif command.startswith("$((", i):
            end = command.find("))", i + 3)
            i = len(command) if end < 0 else end + 1
        elif command.startswith("$(", i):
            end = _closing_paren(command, i + 2)
            if end is None:
                break
            found.append(command[i + 2:end])
            i = end
        elif char == "`":
            end = command.find("`", i + 1)
            while end > 0 and command[end - 1] == "\\":
                end = command.find("`", end + 1)
            if end < 0:
                break
            found.append(command[i + 1:end].replace("\\`", "`"))
            i = end
        i += 1
    return found


def _closing_paren(text, i):
    """The index of the `)` closing a `$(` whose content starts at `i`, quotes
    inside skipped, or None."""
    depth, quote = 1, None
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\" and quote != "'":
                i += 1
            elif char == quote[-1]:
                quote = None
        elif char == "\\":
            i += 1
        elif text.startswith("$'", i):
            quote, i = "$'", i + 1
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


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
    """Who runs the heredoc as a script (_reads_script), or None: `bash
    <<'EOF'`, `cat <<'EOF' | sh`, `source /dev/stdin <<'EOF'`, `ssh vm <<'EOF'`."""
    before_segments = segments(tokenize(before) or [])
    reader = before_segments and _text_reads_script(shlex.join(before_segments[-1]))
    if not reader and re.match(r"\s*\|", after):
        reader = _text_reads_script(after.lstrip()[1:].lstrip("&"))
    return reader or None


# What one pass of scan() carries into the scans it nests: `cwd`, the working
# tree's root for classify_target(); `hardware`, the second pass (the host's
# hardware only); `depth`. The second pass's, from the scans that wrap this
# one: `folders`, where compose files, scripts and Makefiles are looked up
# (the working directory, every `cd` target, a script's own folder; none on a
# remote host, or in the stage that reads no file); `writes`, what the call
# writes to files, as (where, text); `named`, the files the call hands
# Compose (_compose_named), one list for the call; `script`, inside a script
# read whole, whose `ssh <host> '<cmd>'` is read too; `whole`, the script's
# own text, not a command it nests.
Context = collections.namedtuple(
    "Context", "cwd hardware depth folders writes named script whole",
    defaults=(False, 0, (), (), None, False, False))


def _inner(ctx, reader=None):
    """The context of a scan nested one level down, on a remote host when
    `reader` is "remote"."""
    return ctx._replace(depth=ctx.depth + 1, whole=False,
                        folders=() if reader == "remote" else ctx.folders)


class _Expired(Exception):
    """The second pass outlived its budget."""


def _expire(signum, frame):
    raise _Expired()


@contextlib.contextmanager
def _deadline(seconds):
    """Raise _Expired in the block after `seconds`, where SIGALRM exists and
    this is the main thread; then put the previous handler back. It stops a
    slow loop and a backtracking pattern, not a long single-class run
    (CPython's regular expression engine checks for signals between its
    steps): the patterns over file text are kept linear. Another hook keeps
    its own copy of this guard: it must not import this one."""
    armed = hasattr(signal, "setitimer")
    if armed:
        try:
            previous = signal.signal(signal.SIGALRM, _expire)
        except ValueError:  # not the main thread: no budget
            armed = False
    if armed:
        signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        if armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def scan(command, cwd=None):
    """Return a block reason for `command`, or None to allow.

    Two passes over the call. The first runs every rule but the host's
    hardware, reading no file. The second, only when the first allows, looks
    for privileged containers under HARDWARE_SECONDS: first without reading a
    file (a plain `docker run --privileged` must not wait on a slow file, review
    of /ship), then reading compose files, scripts and Makefiles. A fault or an
    overrun there allows the call. Both would otherwise end the hook (an
    exception exits 1, the host kills it at 10 s), which hosts treat as
    non-blocking: a refusal of the first pass, such as the `rm -rf ~` after a
    long script, was lost that way (outside control of /ship, 17.11.2026).
    """
    if cwd is None:
        cwd = os.getcwd()
    reason = _scan(command, Context(cwd))
    if reason:
        return reason
    for cache in (_read_file, _compose_defaults, _compose_file, _says_privileged):
        cache.cache_clear()
    try:
        with _deadline(HARDWARE_SECONDS):
            return (_scan(command, Context(cwd, hardware=True, named=[]))
                    or _scan(command, Context(cwd, hardware=True, folders=(cwd,), named=[])))
    except Exception:  # noqa: BLE001 - see the docstring: allow, never lose the first pass
        return None


def _scan(command, ctx):
    """One pass of scan() over `command`: every rule but the host's hardware,
    or with `ctx.hardware` that rule only."""
    if ctx.depth > MAX_DEPTH:
        return None

    original = command
    command, bodies = _flatten(command)
    tokens = tokenize(command)
    segs = segments(tokens or [])
    if ctx.hardware:
        # The working directory and every `cd` target of the call, in any
        # order: a `cd` in a subshell does not move the next command, so each
        # is read. The other rules keep `cwd`, the root for classify_target().
        ctx.named.extend(_compose_named(segs))
        ctx = ctx._replace(folders=_visited_folders(segs, ctx.folders),
                           writes=ctx.writes + tuple(_writes(tokens or [], bodies)))
    for before, after, body, expands in bodies:
        reader = _read_by_shell(before, after)
        reason = reader and _scan(body, _inner(ctx, reader))
        if reason:
            return reason + " (in a heredoc a shell reads)"
    # An unquoted delimiter (`<<EOF`) expands the body's `$(…)` and backticks.
    expanded = [inner for _b, _a, body, expands in bodies if expands for inner in _substitutions(body)]
    for inner in _substitutions(command) + expanded:
        reason = _scan(inner, _inner(ctx))
        if reason:
            return reason + " (in a command substitution)"
    if tokens is None:
        if ctx.hardware:
            return _unparseable_privileged(command, original, ctx)
        # Unbalanced quotes used to disable the whole hook: `rm -rf /etc "` was
        # allowed because shlex raised and we failed open. Fall back to a raw
        # scan of the destructive shapes. A false positive here only costs a
        # retry, whereas failing open costs the filesystem.
        if UNPARSEABLE_DANGER.search(command):
            return "destructive command in an unparseable shell string (unbalanced quotes)"
        return None

    saw_net_fetch = False
    for seg, cmd, args in _readings(segs, ctx.hardware):
        base = cmd.split("/")[-1]
        saw_net_fetch = saw_net_fetch or base in ("curl", "wget")
        reason = (_hardware_rule(cmd, base, args, ctx) if ctx.hardware
                  else _floor_rule(seg, cmd, base, args, ctx.cwd, saw_net_fetch))
        if reason:
            return reason

        for inner in _nested_commands(base, args):
            reason = _scan(inner, _inner(ctx))
            if reason:
                return reason + f" (wrapped in `{base}`)"
        remote = ctx.script and base == "ssh" and _ssh_remote(args)
        if remote:
            # A script read whole: what it sends through ssh is read too.
            reason = _scan(remote, _inner(ctx, "remote"))
            if reason:
                return reason + " (sent through ssh)"

        # A here-string is data, unless a shell reads it: `bash <<< 'rm -rf /'`.
        reader = "<<<" in args[:-1] and _reads_script(cmd, args)
        for j, t in enumerate(args[:-1]):
            if t == "<<<" and reader:
                reason = _scan(args[j + 1], _inner(ctx, reader))
                if reason:
                    return reason + " (in a here-string a shell reads)"

    if ctx.hardware:
        return PRIVILEGED_REASON if ctx.whole and _wrapped_privileged(segs, tokens) else None
    # Fork bomb (string-level; the shape is distinctive enough).
    if re.search(r':\s*\(\s*\)\s*\{\s*:\s*\|\s*:?\s*&?\s*\}\s*;\s*:', command):
        return "fork bomb"
    return None


def _readings(segs, hardware):
    """(segment, command, args) of each segment; for the first pass, also
    d5c9106's reading of a segment where it differs (LEGACY_VALUE_OPTS)."""
    for seg in segs:
        readings = [command_word(seg)]
        if not hardware and command_word(seg, legacy=True) != readings[0]:
            readings.append(command_word(seg, legacy=True))
        yield from ((seg, cmd, args) for cmd, args in readings if cmd is not None)


def _floor_rule(seg, cmd, base, args, cwd, saw_net_fetch):
    """The first pass's rules for one command: destruction, a download piped
    into a shell, an interactive command."""
    if base == "rm" and _rm_dangerous(args, cwd):
        return "recursive rm on the working tree itself or on a path outside it"
    if base == "git" and _git_dangerous(args):
        return "destructive git command (reset --hard / push --force / clean -fdx)"
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
    return None


def _hardware_rule(cmd, base, args, ctx):
    """The second pass's rule for one command: a privileged container, a
    Compose start of a privileged service, or one in what it runs as a script."""
    if base in CONTAINER_CLIS and (_container_privileged(args) or _kube_privileged(args, ctx)):
        return PRIVILEGED_REASON
    if _compose_privileged(base, args, ctx):
        return COMPOSE_REASON
    return _script_privileged(cmd, base, args, ctx)


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
