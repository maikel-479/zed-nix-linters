# TODO — closing the gap with stock (`zed-extensions/nix` issues)

Source: triage of all 12 open issues at
https://github.com/zed-extensions/nix/issues (2026-10-11).
Items are ordered smallest-first. Check off as merged.

## 1. README troubleshooting entries (issues #36, #58)
- [ ] #36 "Some flake inputs are not available": add entry explaining this
      is nil failing to resolve flake inputs; fix is the "Flake users"
      section (point nixd/nil at the flake) or switching to nixd.
- [ ] #58 Remote/WSL servers never launch: add Requirements line that
      the *remote* needs `python3` plus the tools, and link the
      `wsl.extraBin` coreutils workaround from the issue thread.

## 2. Highlighting fixes (issues #56, #29)
- [ ] #56: style `${…}` interpolation delimiters distinctly from `$var`
      bash variables in injected shells; highlight `''` escape as an
      escape char. Files: `languages/nix/highlights.scm`
      (+ `injections.scm` if needed).
- [ ] #29: fix brace coloring in nested interpolation (`"${a b}"`
      case). Helix renders it correctly with the same grammar rev, so
      the fix is in our queries, not the parser.
- [ ] Verify both against the issue repro snippets in Zed
      (`zed: rebuild dev extension`, reopen test file).

## 3. Runnable flake tasks (issue #26)
- [ ] Ship `languages/nix/runnables.scm` exposing at least
      `nix flake check` (and ideally `nix build .#<attr>`) as gutter
      runnables. Check Zed's `runnables.scm` docs for the query shape.
- [ ] Verify the run button appears on a test `flake.nix`.

## 4. Needs-info / blocked (no code yet)
- [ ] #27 "Confirmation support for nil": title + screenshot only, zero
      comments. Open the image and decide what it asks for.
- [ ] #25 Arbitrary language injections: blocked upstream on
      `nix-community/tree-sitter-nix#139`. Track only.

## Deliberately out of scope
- #35, #28, #40: already solved (fallback chain, formatter defaults,
  README `format_on_save` docs).
- #24 (NixOS wasm load failure, 2024-era env issue), #15 (maintainers
  meta-issue).
