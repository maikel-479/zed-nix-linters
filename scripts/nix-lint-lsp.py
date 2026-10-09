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

send_lock = threading.Lock()
lint_lock = threading.Lock()
lint_timer = None

LINT_DEBOUNCE_SECONDS = 0.3


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
    diagnostics = run_statix(text) + run_deadnix(text)
    diagnostics.sort(key=lambda d: (d["range"]["start"]["line"], d["range"]["start"]["character"]))
    publish(uri, diagnostics)


def schedule_lint(uri, text):
    """Debounced linting, so fast typing doesn't spawn tools per keystroke."""
    global lint_timer
    with lint_lock:
        if lint_timer is not None:
            lint_timer.cancel()
        lint_timer = threading.Timer(LINT_DEBOUNCE_SECONDS, lint, args=(uri, text))
        lint_timer.daemon = True
        lint_timer.start()


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


# ── request handling ──────────────────────────────────────────────────


def code_actions(uri, params):
    text = documents.get(uri, "")
    actions = []
    for kind, title in (("statix", "Fix all statix issues"), ("deadnix", "Remove dead code (deadnix)")):
        tool = STATIX if kind == "statix" else DEADNIX
        if not tool:
            continue
        fixed = fix_all(kind, text)
        if fixed is None:
            continue
        actions.append(
            {
                "title": title,
                "kind": f"source.fixAll.{kind}",
                "edit": {"changes": {uri: full_document_edit(text, fixed)}},
            }
        )
    return actions


def handle(message):
    global shutdown_requested
    method = message.get("method")
    params = message.get("params", {}) or {}
    request_id = message.get("id")

    if method == "initialize":
        reply(
            request_id,
            {
                "capabilities": {
                    "textDocumentSync": {"openClose": True, "change": 1, "save": True},
                    "documentFormattingProvider": bool(NIXFMT or ALEJANDRA),
                    "codeActionProvider": {"codeActionKinds": ["source.fixAll.statix", "source.fixAll.deadnix"]},
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
            lint(uri, documents[uri])
    elif method == "textDocument/didClose":
        uri = params["textDocument"]["uri"]
        documents.pop(uri, None)
        publish(uri, [])
    elif method == "textDocument/formatting":
        uri = params["textDocument"]["uri"]
        text = documents.get(uri, "")
        fixed = format_text(text)
        reply(request_id, full_document_edit(text, fixed) if fixed is not None else None)
    elif method == "textDocument/codeAction":
        reply(request_id, code_actions(params["textDocument"]["uri"], params))
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
