#!/usr/bin/env python3
"""Validate Zed editor snippet JSON files.

Checks:
- Valid JSON syntax
- Required fields (prefix, body)
- Correct field types (string or string array)
- No empty prefix/body values
- No duplicate prefixes within a file
- Prefix format (no whitespace)
- Optional: description presence

Usage:
    python3 validate_snippets.py                 # validate all .json in snippets/
    python3 validate_snippets.py file1.json ...  # validate specific files
"""

import json
import sys
from pathlib import Path

SNIPPETS_DIR = Path(__file__).parent / "snippets"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def error(msg):
    print(f"  {RED}ERROR{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def ok(msg):
    print(f"  {GREEN}OK{RESET}    {msg}")


def is_string_or_string_array(value):
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        return all(isinstance(v, str) for v in value)
    return False


def flatten_prefixes(prefix):
    if isinstance(prefix, str):
        return [prefix]
    return prefix


def validate_file(path):
    print(f"\n{BOLD}{path.name}{RESET}")

    # JSON syntax
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        error(f"Invalid JSON: {e}")
        return 1

    if not isinstance(data, dict):
        error("Top-level value must be a JSON object")
        return 1

    if not data:
        warn("File is empty (no snippets)")
        return 0

    errors = 0
    all_prefixes = []

    for name, snippet in data.items():
        if not isinstance(snippet, dict):
            error(f"[{name}] Snippet must be an object, got {type(snippet).__name__}")
            errors += 1
            continue

        # prefix
        if "prefix" not in snippet:
            error(f"[{name}] Missing required field: prefix")
            errors += 1
        else:
            prefix = snippet["prefix"]
            if not is_string_or_string_array(prefix):
                error(
                    f"[{name}] prefix must be a string or array of strings, got {type(prefix).__name__}"
                )
                errors += 1
            else:
                prefixes = flatten_prefixes(prefix)
                for p in prefixes:
                    if not p:
                        error(f"[{name}] prefix contains empty string")
                        errors += 1
                    elif " " in p or "\t" in p:
                        error(f"[{name}] prefix '{p}' contains whitespace")
                        errors += 1
                    else:
                        all_prefixes.append(p)

        # body
        if "body" not in snippet:
            error(f"[{name}] Missing required field: body")
            errors += 1
        else:
            body = snippet["body"]
            if not is_string_or_string_array(body):
                error(
                    f"[{name}] body must be a string or array of strings, got {type(body).__name__}"
                )
                errors += 1
            else:
                body_str = "\n".join(body) if isinstance(body, list) else body
                if not body_str.strip():
                    warn(f"[{name}] body is empty")

        # description (optional but warn)
        if "description" not in snippet:
            warn(f"[{name}] Missing optional field: description")

        # unknown fields
        known = {"prefix", "body", "description"}
        extra = set(snippet.keys()) - known
        if extra:
            warn(f"[{name}] Unknown field(s): {', '.join(sorted(extra))}")

    # duplicate prefixes
    seen = {}
    for p in all_prefixes:
        if p in seen:
            seen[p] += 1
        else:
            seen[p] = 1

    dupes = {p: count for p, count in seen.items() if count > 1}
    for p, count in sorted(dupes.items()):
        error(f"Duplicate prefix '{p}' (appears {count} times)")
        errors += 1

    total = len(data)
    if errors == 0:
        ok(f"{total} snippet(s), all valid")
    else:
        print(f"  {RED}{errors} error(s) in {total} snippet(s){RESET}")

    return errors


def main():
    if len(sys.argv) > 1:
        files = [Path(f) for f in sys.argv[1:]]
    else:
        if not SNIPPETS_DIR.is_dir():
            print(f"{RED}Snippets directory not found:{RESET} {SNIPPETS_DIR}")
            sys.exit(1)
        files = sorted(SNIPPETS_DIR.glob("*.json"))

    if not files:
        print(f"{YELLOW}No JSON files found.{RESET}")
        sys.exit(0)

    total_errors = 0
    for path in files:
        if not path.is_file():
            error(f"File not found: {path}")
            total_errors += 1
            continue
        total_errors += validate_file(path)

    print()
    if total_errors == 0:
        print(f"{GREEN}All files passed validation.{RESET}")
    else:
        print(f"{RED}{total_errors} total error(s) across {len(files)} file(s).{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
