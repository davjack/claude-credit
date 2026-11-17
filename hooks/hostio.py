#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Davjack
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Normalize hook I/O across agent harnesses (Claude Code, Codex, Kimi Code).

The hooks each implement one behavior; every host wraps it in a slightly
different stdin/stdout contract. This module is the single place that knows the
differences, so the hooks stay host-neutral and their pure functions (such as
the firewall's `scan`) never grow a host branch — which is what keeps them
testable in isolation.

Host resolution: read once from the `WORKFLOW_HOST` environment variable, which
each *non-Claude* hook registration sets. Absent, the host is `claude`. A Claude
Code install never sets the variable, so its hooks keep byte-identical behavior
— that default is what makes "no regression on Claude Code" hold without
touching the Claude Code hook registration.

Verified host contracts (sources in each item):

  - **PreToolUse block** — exit 2 with a reason on stderr blocks the tool call on
    all three hosts (code.claude.com/docs/en/hooks;
    learn.chatgpt.com/docs/hooks;
    github.com/MoonshotAI/kimi-cli/blob/main/docs/en/customization/hooks.md).
    So `deny()` needs no host branch.

  - **Stop block** — Claude and Codex both accept
    `{"decision": "block", "reason": ...}` on stdout with exit 0 (Codex turns the
    reason into a continuation prompt). Kimi blocks a Stop via exit 2 + stderr.
    So `block_stop()` is the one output helper that varies by host.

Tool names select the input contract: both Codex's shell and patch tools carry
`tool_input.command`, but only Bash is shell text. Kimi calls its shell `Shell`.
Unknown explicit tool names are ignored; they may carry a `command` or `path`
argument with unrelated semantics. Legacy payloads without `tool_name` keep the
original command/path aliases for existing integrations.

`payload()` returns None on unreadable input rather than an empty dict, so each
hook can reproduce its own fail-open exit instead of inheriting a single policy
here.
"""

import json
import os
import sys

CLAUDE = "claude"
CODEX = "codex"
KIMI = "kimi"
KNOWN_HOSTS = (CLAUDE, CODEX, KIMI)

# Scalar aliases are retained for legacy payloads; tool dispatch comes first.
_COMMAND_KEYS = ("command", "cmd")
_PATH_KEYS = ("file_path", "path", "filePath")
_SHELL_TOOLS = ("Bash", "Shell")
_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "WriteFile", "StrReplaceFile")
_PATCH_HEADERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ")


def host():
    """The harness this hook runs under: `claude` (default), `codex` or `kimi`.

    An unknown or empty value resolves to `claude`, never to an error: a hook
    must not fail because an installer wrote a value this module has not heard
    of. The default is also the no-regression guarantee — Claude Code sets
    nothing.
    """
    value = os.environ.get("WORKFLOW_HOST", "").strip().lower()
    return value if value in KNOWN_HOSTS else CLAUDE


def payload():
    """Parse the hook's stdin JSON object, or None when it cannot be read.

    None is the fail-open signal every caller checks first. Returning an empty
    dict instead would erase the difference between "no input" and "input said
    nothing", which a hook may depend on.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _tool_input(data):
    inner = data.get("tool_input") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else {}


def _matches_tool(data, names):
    """Accept a known tool, or the explicitly supported legacy absent name."""
    return isinstance(data, dict) and (
        "tool_name" not in data or data["tool_name"] in names
    )


def bash_command(data):
    """The command of Bash (Claude/Codex) or Shell (Kimi), otherwise `""`.

    A patch or MCP tool's `command` is data, even when it resembles shell text.
    Missing names retain the old command-only payload contract; malformed or
    unknown explicit names fail open without being guessed into a shell tool.
    """
    if not _matches_tool(data, _SHELL_TOOLS):
        return ""
    inner = _tool_input(data)
    for key in _COMMAND_KEYS:
        value = inner.get(key)
        if isinstance(value, str):
            return value
    return ""


def edited_path(data):
    """A scalar target of a known edit/write tool, or None.

    Call `edited_paths` to also cover apply_patch, which can affect many files.
    """
    if not _matches_tool(data, _FILE_TOOLS):
        return None
    inner = _tool_input(data)
    for key in _PATH_KEYS:
        value = inner.get(key)
        if isinstance(value, str):
            return value
    return None


def patch_paths(command):
    """Extract apply_patch file headers, including both paths of a move.

    This is a filename inventory, not a second patch validator. Unrecognized
    envelopes/non-string payloads fail open; the host validates hunk contents.
    Added/removed text and update context lines never become file headers.

    Whitespace and the literal EOF wrapper follow Codex 0.153.4's parser:
    https://github.com/openai/codex/tree/rust-v0.153.4/codex-rs/apply-patch/src
    In an update hunk leading spaces are context, while add/delete/begin states
    allow indentation of headers. Preserve that distinction before stripping.
    """
    if not isinstance(command, str):
        return []
    # Rust's str::lines splits LF/CRLF, not Unicode separators within filenames.
    lines = [line.removesuffix("\r") for line in command.strip().split("\n")]
    if (len(lines) >= 4 and lines[0] in ("<<EOF", "<<'EOF'", '<<"EOF"')
            and lines[-1].endswith("EOF")):
        lines = lines[1:-1]
    if (len(lines) < 2 or lines[0].strip() != "*** Begin Patch"
            or lines[-1].strip() != "*** End Patch"):
        return []

    paths = []
    updating = False
    for line in lines[1:-1]:
        header = line.rstrip() if updating else line.strip()
        for prefix in _PATCH_HEADERS:
            if header.startswith(prefix):
                paths.append(header[len(prefix):])
                updating = prefix == "*** Update File: "
                break
        else:
            if updating and header.startswith("*** Move to: "):
                paths.append(header[len("*** Move to: "):])
    return paths


def edited_paths(data):
    """All targets affected by a supported edit/write/patch payload."""
    if isinstance(data, dict) and data.get("tool_name") == "apply_patch":
        return patch_paths(_tool_input(data).get("command"))
    path = edited_path(data)
    return [path] if path is not None else []


def cwd(data):
    """The working directory the host reports, or the process cwd."""
    value = data.get("cwd") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else os.getcwd()


# Parts Kimi Code itself adds to a user message: a bundled skill's rendered
# body and an earlier UserPromptSubmit hook's output. Neither is the user's words.
_MACHINE_PART_SOURCES = ("skill activation", "user prompt submit hook")


def prompt_text(data):
    """The submitted prompt as the user typed it, or `""`.

    Claude Code and Codex send a string. Kimi Code 2.3.2 sends the message's
    content parts, `[{"type": "text", "text": ...}, ...]`; its text parts are
    joined, minus the ones it tags as its own in `meta.source`.
    """
    value = data.get("prompt") if isinstance(data, dict) else None
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    texts = []
    for part in value:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        meta = part.get("meta")
        if isinstance(meta, dict) and meta.get("source") in _MACHINE_PART_SOURCES:
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts)


def deny(reason):
    """Block a PreToolUse call: reason on stderr, exit 2. Never returns.

    Uniform across the three hosts, so no host branch here.
    """
    sys.stderr.write(reason)
    sys.exit(2)


def block_stop(reason):
    """Block a Stop so the agent keeps working. Host-aware; never returns.

    Claude/Codex: `{"decision": "block", "reason": ...}` on stdout, exit 0.
    Kimi: exit 2 + stderr.
    """
    if host() == KIMI:
        sys.stderr.write(reason)
        sys.exit(2)
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def system_message(text):
    """Emit advisory output (for example from a PreCompact hook): `{"systemMessage": ...}`.

    On Codex this is a warning in the UI/event stream, not a model-context or
    compaction instruction. Hosts decide how to expose the field; emitting it
    does not guarantee that the compaction summary preserves any information.
    """
    print(json.dumps({"systemMessage": text}))
    sys.exit(0)
