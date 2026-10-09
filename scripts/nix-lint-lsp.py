#!/usr/bin/env python3
"""LSP bridge that gives Zed linting, formatting and autofixes for Nix.

statix, deadnix and the Nix formatters are plain CLI tools without an LSP
mode, so this bridge runs whichever ones are installed against the editor
buffer and reports the results as standard LSP diagnostics, formatting
edits and code actions.

It is started by the nix-linters Zed extension (the source of this file is
embedded in the extension and fed to python3 via -c):

    python3 -c '<this source>' \
        [--statix /path] [--deadnix /path] [--nixfmt /path] [--alejandra /path]

Every tool argument is optional; when omitted the bridge looks the tool up
on $PATH itself. Missing tools are skipped, so everything works with
whatever subset is installed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

STATIX = None
DEADNIX = None
NIXFMT = None
ALEJANDRA = None

documents = {}
shutdown_requested = False
# Whether the client said it will call codeAction/resolve. Clients that
# support it get edits computed on demand; the rest need them up front.
resolve_supported = False

LINT_DEBOUNCE_SECONDS = 0.3
# Each debounced lint spawns statix and deadnix, so a burst of edits across
# many open files could otherwise flood the machine with processes.
LINT_CONCURRENCY = 2

send_lock = threading.Lock()
lint_lock = threading.Lock()
lint_timers = {}
lint_slots = threading.Semaphore(LINT_CONCURRENCY)


# ── JSON-RPC plumbing ─────────────────────────────────────────────────


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.partition(b":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get(b"content-length", 0))
    return json.loads(sys.stdin.buffer.read(length))


def send(message):
    body = json.dumps(message).encode()
    with send_lock:
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        sys.stdout.buffer.flush()


def reply(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def reply_error(request_id, code, message):
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def publish(uri, diagnostics):
    send(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": diagnostics},
        }
    )


# ── helpers ───────────────────────────────────────────────────────────


def range_(start_line, start_col, end_line, end_col):
    return {
        "start": {"line": max(start_line, 0), "character": max(start_col, 0)},
        "end": {"line": max(end_line, 0), "character": max(end_col, 0)},
    }


def scratch_file(text):
    """Write `text` to a throwaway .nix file (deadnix has no stdin mode)."""
    fd, path = tempfile.mkstemp(suffix=".nix")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(cmd, **kwargs):
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("timeout", 30)
    try:
        return subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None


# ── linters ───────────────────────────────────────────────────────────


def run_statix(text):
    """Lint `text` with statix (errfmt output over stdin)."""
    if not STATIX:
        return []
    result = run([STATIX, "check", "--stdin", "-o", "errfmt"], input=text.encode())
    if result is None:
        return []

    diagnostics = []
    for line in result.stdout.decode(errors="replace").splitlines():
        # errfmt shape: <stdin>>line:col:SEVERITY:CODE:message
        try:
            _, rest = line.split(">", 1)
            line_s, col_s, sev, code, message = rest.lstrip(">").split(":", 4)
            ln = int(line_s) - 1
            col = int(col_s) - 1
        except (ValueError, IndexError):
            continue
        severity = {"E": 1, "W": 2, "I": 3, "H": 4}.get(sev.strip().upper()[:1], 2)
        diagnostics.append(
            {
                "range": range_(ln, col, ln, col + 1),
                "severity": severity,
                "code": code.strip(),
                "source": "statix",
                "message": message.strip(),
            }
        )
    return diagnostics


def run_deadnix(text):
    """Lint `text` with deadnix (JSON output; reads from a file)."""
    if not DEADNIX:
        return []
    path = scratch_file(text)
    try:
        result = run([DEADNIX, "-o", "json", path])
    finally:
        os.unlink(path)
    if result is None:
        return []

    diagnostics = []
    try:
        report = json.loads(result.stdout.decode(errors="replace") or "{}")
    except json.JSONDecodeError:
        return []
    for finding in report.get("results", []):
        ln = finding.get("line", 1) - 1
        col = finding.get("column", 1) - 1
        end_col = finding.get("endColumn", col + 2) - 1
        diagnostics.append(
            {
                "range": range_(ln, col, ln, max(end_col, col + 1)),
                "severity": 2,
                "source": "deadnix",
                "message": finding.get("message", "dead code"),
            }
        )
    return diagnostics


def lint(uri, text):
    # Bound how many documents are linted at once: the semaphore is held for
    # the whole run so a burst of timers queues instead of piling up
    # statix/deadnix processes.
    with lint_slots:
        diagnostics = run_statix(text) + run_deadnix(text)
    # Linting takes long enough that the buffer can move on (a didSave, or a
    # newer keystroke's lint) before we get here. Publishing then would replace
    # correct diagnostics with stale ones, so drop the result instead.
    if documents.get(uri) != text:
        return
    diagnostics.sort(key=lambda d: (d["range"]["start"]["line"], d["range"]["start"]["character"]))
    publish(uri, diagnostics)


def schedule_lint(uri, text, delay=LINT_DEBOUNCE_SECONDS):
    """Schedule a lint of `text` for `uri` after `delay` seconds.

    Debounce so fast typing doesn't spawn tools per keystroke. `delay=0`
    runs as soon as a slot frees up — used for saves, where the text is
    final but the JSON-RPC loop must still not block on subprocesses.

    Timers are keyed by URI: editing one file must not cancel a pending lint
    for another.
    """
    with lint_lock:
        pending = lint_timers.pop(uri, None)
        if pending is not None:
            pending.cancel()
        timer = threading.Timer(delay, lint, args=(uri, text))
        timer.daemon = True
        lint_timers[uri] = timer
        timer.start()


def cancel_lint(uri):
    """Drop any pending debounced lint for `uri`."""
    with lint_lock:
        pending = lint_timers.pop(uri, None)
        if pending is not None:
            pending.cancel()


# ── fixing / formatting ───────────────────────────────────────────────


def fix_all(kind, text):
    """Run a tool's autofix over `text`; return the fixed text or None."""
    try:
        if kind == "statix" and STATIX:
            result = run([STATIX, "fix", "--stdin"], input=text.encode())
            if result is not None and result.returncode == 0:
                fixed = result.stdout.decode(errors="replace")
                return fixed if fixed != text else None
        elif kind == "deadnix" and DEADNIX:
            path = scratch_file(text)
            try:
                run([DEADNIX, "-e", path])
                with open(path, encoding="utf-8") as f:
                    fixed = f.read()
            finally:
                os.unlink(path)
            return fixed if fixed != text else None
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def format_text(text):
    """Format `text` with the preferred installed formatter.

    nixfmt is the official Nix formatter and wins when both are present.
    """
    for tool in (NIXFMT, ALEJANDRA):
        if not tool:
            continue
        result = run([tool, "-"], input=text.encode())
        if result is None or result.returncode != 0:
            continue
        fixed = result.stdout.decode(errors="replace")
        return fixed if fixed != text else None
    return None


def full_document_edit(text, new_text):
    return [{"range": range_(0, 0, len(text.splitlines()) + 1, 0), "newText": new_text}]


# The fix-all code actions this server can offer, in display order.
FIX_ACTIONS = (
    ("statix", "Fix all statix issues"),
    ("deadnix", "Remove dead code (deadnix)"),
)


# ── request handling ──────────────────────────────────────────────────


def available_actions(uri):
    """The fix-all actions this server offers, without computing any edits."""
    actions = []
    for kind, title in FIX_ACTIONS:
        tool = STATIX if kind == "statix" else DEADNIX
        if not tool:
            continue
        actions.append(
            {
                "title": title,
                "kind": f"source.fixAll.{kind}",
                "data": {"uri": uri},
            }
        )
    return actions


def kind_matches(action_kind, requested):
    """Does `action_kind` satisfy a requested `CodeActionKind`?

    Kinds are hierarchical, so a request for `source` or `source.fixAll`
    covers the actions nested under it, not just an exact string match.
    """
    return action_kind == requested or action_kind.startswith(requested + ".")


def with_edit(action, uri):
    """Run the action's tool and attach the resulting full-document edit.

    Returns None when the tool is unavailable or found nothing to fix, which
    is how an action is filtered out of the list.
    """
    kind = action["kind"].rsplit(".", 1)[-1]
    fixed = fix_all(kind, documents.get(uri, ""))
    if fixed is None:
        return None
    action["edit"] = {"changes": {uri: full_document_edit(documents.get(uri, ""), fixed)}}
    return action


def code_actions(uri, params):
    """Offer fix-all actions for `uri`.

    Running a tool costs a subprocess, and clients ask for code actions on
    every cursor move, so edits are computed lazily and `codeAction/resolve`
    fills them in. Clients that cannot resolve edits must get them up front,
    otherwise the action does nothing when it is picked.
    """
    actions = available_actions(uri)

    only = (params.get("context") or {}).get("only") or []
    if only:
        actions = [a for a in actions if any(kind_matches(a["kind"], k) for k in only)]

    if not resolve_supported:
        actions = [a for a in (with_edit(a, uri) for a in actions) if a is not None]

    return actions


def resolve_code_action(action):
    """Fill in the edit for an action returned without one."""
    if "edit" in action:
        return action
    uri = (action.get("data") or {}).get("uri")
    kind = action.get("kind", "").rsplit(".", 1)[-1]
    if uri is None or kind not in ("statix", "deadnix"):
        return action
    text = documents.get(uri, "")
    fixed = fix_all(kind, text)
    if fixed is not None:
        action["edit"] = {"changes": {uri: full_document_edit(text, fixed)}}
    return action


def handle(message):
    global shutdown_requested
    method = message.get("method")
    params = message.get("params", {}) or {}
    request_id = message.get("id")

    if method == "initialize":
        global resolve_supported
        code_action = ((params.get("capabilities") or {}).get("textDocument") or {}).get(
            "codeAction", {}
        )
        resolve_support = code_action.get("resolveSupport") or {}
        # Deferring is only safe when the client can both resolve the `edit`
        # property and round-trip our `data` back to us; `resolveSupport` only
        # guarantees the properties it lists, so a client may resolve `command`
        # without `edit`, and it drops `data` unless `dataSupport` is set.
        resolve_supported = "edit" in (resolve_support.get("properties") or []) and bool(
            code_action.get("dataSupport")
        )
        reply(
            request_id,
            {
                "capabilities": {
                    "textDocumentSync": {"openClose": True, "change": 1, "save": True},
                    "documentFormattingProvider": bool(NIXFMT or ALEJANDRA),
                    "codeActionProvider": {
                        "codeActionKinds": [f"source.fixAll.{kind}" for kind, _ in FIX_ACTIONS],
                        "resolveProvider": resolve_supported,
                    },
                },
                "serverInfo": {"name": "nix-lint", "version": "0.3.0"},
            },
        )
    elif method == "initialized":
        pass
    elif method == "textDocument/didOpen":
        doc = params["textDocument"]
        uri = doc["uri"]
        documents[uri] = doc["text"]
        schedule_lint(uri, doc["text"])
    elif method == "textDocument/didChange":
        uri = params["textDocument"]["uri"]
        changes = params.get("contentChanges", [])
        if changes:
            documents[uri] = changes[-1]["text"]
            schedule_lint(uri, documents[uri])
    elif method == "textDocument/didSave":
        uri = params["textDocument"]["uri"]
        if uri in documents:
            # The text is final on save, but statix/deadnix are subprocesses:
            # run through the same background machinery so the JSON-RPC read
            # loop keeps serving requests meanwhile.
            schedule_lint(uri, documents[uri], delay=0)
    elif method == "textDocument/didClose":
        uri = params["textDocument"]["uri"]
        cancel_lint(uri)
        documents.pop(uri, None)
        publish(uri, [])
    elif method == "textDocument/formatting":
        uri = params["textDocument"]["uri"]
        text = documents.get(uri, "")
        fixed = format_text(text)
        reply(request_id, full_document_edit(text, fixed) if fixed is not None else None)
    elif method == "textDocument/codeAction":
        uri = params["textDocument"]["uri"]
        reply(request_id, code_actions(uri, params))
    elif method == "codeAction/resolve":
        reply(request_id, resolve_code_action(params))
    elif method == "shutdown":
        shutdown_requested = True
        reply(request_id, None)
    elif method == "exit":
        sys.exit(0 if shutdown_requested else 1)
    elif request_id is not None:
        reply_error(request_id, -32601, "Method not found")


def main():
    global STATIX, DEADNIX, NIXFMT, ALEJANDRA

    tools = {
        "--statix": "STATIX",
        "--deadnix": "DEADNIX",
        "--nixfmt": "NIXFMT",
        "--alejandra": "ALEJANDRA",
    }
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg in tools and i + 1 < len(args):
            globals()[tools[arg]] = args[i + 1]
    # fall back to $PATH for anything the caller didn't pin down
    if not STATIX:
        STATIX = shutil.which("statix")
    if not DEADNIX:
        DEADNIX = shutil.which("deadnix")
    if not NIXFMT:
        NIXFMT = shutil.which("nixfmt")
    if not ALEJANDRA:
        ALEJANDRA = shutil.which("alejandra")

    while True:
        message = read_message()
        if message is None:
            break
        handle(message)


if __name__ == "__main__":
    main()
