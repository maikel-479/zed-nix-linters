# Nix Linters for Zed

A Zed extension that makes `.nix` files feel first-class: syntax highlighting, a language server that suggests real nixpkgs package names, linting, formatting, quick fixes and a solid set of snippets.

The goal was simple: **open a Nix file and have everything work**, without spending an afternoon wiring up `settings.json`. The extension probes your `$PATH` at runtime (so direnv, nix-shell and flake devshells are picked up) and runs whatever you actually have installed. Missing tools are skipped gracefully instead of throwing errors.

## What you get

- **Syntax highlighting** for Nix, with 2-space indentation, bracket matching, comments and outline support.
- **A language server with opinions.** `nixd` runs by default, which means you get completion of real nixpkgs package names — type `pkgs.cow` and pick `cowsay`, or ask for NixOS and Home-Manager options. If you only have `nil` installed, the extension uses that instead.
- **Linting while you type.** [statix](https://github.com/oppiliappan/statix) flags antipatterns, [deadnix](https://github.com/oppiliappan/deadnix) finds unused bindings. Both show up as ordinary squiggles, debounced so fast typing doesn't spawn processes per keystroke.
- **Formatting** with [nixfmt](https://github.com/NixOS/nixfmt) (the official formatter) if you have it, falling back to [alejandra](https://github.com/kamadorueda/alejandra).
- **Quick fixes** from the code-action menu: *Fix all statix issues* and *Remove dead code (deadnix)* fix the whole file in one go.
- **Snippets** for the boilerplate you actually write (see the [cheat sheet](#snippets) below).

## Requirements

You only strictly need Python 3 — that's what runs the lint/format bridge. Everything else is detected at runtime:

```sh
nix profile install nixpkgs#nixd nixpkgs#statix nixpkgs#deadnix nixpkgs#nixfmt
```

Install whichever subset you like. Without `nixd` (or `nil`) you lose completions, without linters you lose squiggles, and the extension tells you what's missing in the language-server status instead of failing silently.

## Install

Open the command palette and run **zed: install dev extension**, then pick this directory. Zed compiles everything itself, including the tree-sitter grammar (it downloads `wasi-sdk` automatically the first time). After changing Rust code, run **zed: rebuild dev extension** to pick up the changes.

One important note: **uninstall the stock [`nix`](https://github.com/zed-extensions/nix) extension first.** Both extensions claim the `Nix` language, the `nix` grammar and the `nil`/`nixd` server names. When two extensions fight over the same ids, Zed picks whichever it scanned last, so you'd get nondeterministic behavior between restarts. This extension is a drop-in replacement for everything the stock one provides, plus the linting and formatting it doesn't.

## Snippets

Type a prefix in a `.nix` file and accept the suggestion to expand it. Placeholders are tab-stops, so `Tab` walks you through the parts worth editing.

**Everyday expressions**

| Prefix | Expands to |
|---|---|
| `let` | `let ... in` with one binding |
| `lambda` | `arg: …` anonymous function |
| `fn` (or `nix-func`) | `{ args }: …` attribute-set function |
| `if` | `if … then … else …` |
| `with` | `with set; …` |
| `import` | `import ./path` |
| `inherit` | `inherit name;` |
| `rec` | recursive attribute set |
| `mstr` | `'' … ''` multi-line string |

**NixOS / Home-Manager modules**

| Prefix | Expands to |
|---|---|
| `module` | `{ config, lib, pkgs, ... }:` module header |
| `mkOption` | full `lib.mkOption` declaration with type and description |
| `mkEnableOption` | `lib.mkEnableOption "…"` |
| `mkIf` | `lib.mkIf condition { … }` |
| `optionals` | `lib.optionals condition [ … ]` |
| `imports` | `imports = [ ./module.nix ];` |
| `service` | `services.<name>.enable = true;` |
| `program` | `programs.<name>.enable = true;` |
| `systemPackages` | `environment.systemPackages = with pkgs; [ … ];` |
| `homePackages` | `home.packages = with pkgs; [ … ];` |

**Packages and scripts**

| Prefix | Expands to |
|---|---|
| `mkDerivation` | `stdenv.mkDerivation` package skeleton |
| `callPackage` | `callPackage ./path { … }` |
| `mkShell` | `pkgs.mkShell` development shell |
| `writeShellApplication` | ship a shell script as a package |
| `nix-shell` | `#!nix-shell` shebang header |

**Flakes**

| Prefix | Expands to |
|---|---|
| `flake` | `flake.nix` skeleton with per-system packages |
| `devShell` | a `devShells` entry for a flake's outputs |

## Configuration

Everything above works without touching `settings.json`. The snippets below are the optional bits — Zed doesn't let extensions set these, which is why they live here rather than in the extension itself.

**Format on save.** Zed's `format_on_save` defaults to `off`, and there's no extension API for it:

```json
{ "languages": { "Nix": { "format_on_save": "on" } } }
```

Formatting already works on demand (`editor: format`) either way, since the default `"formatter": "auto"` uses the language server.

**Two-space indentation.** The shipped language config sets `tab_size = 2`, but a global `tab_size` in your settings beats language defaults. If you set one (say, `1`), add this to keep Nix at 2:

```json
{ "languages": { "Nix": { "tab_size": 2 } } }
```

**Prefer nil, or run both.** `nixd` runs by default and `nil` is registered as opt-in. To flip that around:

```json
{ "languages": { "Nix": { "language_servers": ["nil", "nix-lint", "!nixd"] } } }
```

**Flake users: point nixd at your flake's nixpkgs.** Out of the box nixd evaluates `import <nixpkgs> { }`. If you manage everything through flakes, give it your input instead — your settings deep-merge over the extension's defaults, so your keys win:

```json
{
  "lsp": {
    "nixd": {
      "initialization_options": {
        "nixpkgs": { "expr": "(builtins.getFlake \"/home/you/your-flake\").inputs.nixpkgs { }" }
      }
    }
  }
}
```

**Tuning the linters.** statix reads a [`statix.toml`](https://github.com/oppiliappan/statix#configuration) from the project root, so drop one in to enable or silence lints. deadnix runs with its defaults (unused bindings and arguments; names starting with `_` are left alone).

## Troubleshooting

- **Both `nil` and `nixd` are running.** The manifest opt-in for `nil` (`opt_in_languages`, [documented here](https://zed.dev/docs/extensions/languages#opt-in-language-servers)) shipped in Zed 1.24. On older stable builds it's ignored, so `nil` starts alongside `nixd` until Zed auto-updates. Until then, add `"language_servers": ["nixd", "nix-lint", "!nil"]` under `languages.Nix`.
- **"no Nix language server found"** — install one: `nix profile install nixpkgs#nixd` (or `#nil`).
- **"no Nix linter found"** — `nix profile install nixpkgs#statix nixpkgs#deadnix`.
- **Syntax highlighting or snippets not showing up** — run **zed: rebuild dev extension**.

## How it works

Zed extensions speak LSP, but statix, deadnix and the formatters are plain command-line tools with no LSP mode. So the extension is a thin Rust core plus one embedded Python bridge:

```
extension.wasm (Rust)
 ├─ probes $PATH for python3, nixd/nil, statix, deadnix, nixfmt, alejandra
 ├─ starts the language server (nixd preferred, nil as fallback)
 └─ starts the bridge: python3 -c '<embedded script>' --statix /path …

scripts/nix-lint-lsp.py (embedded in the binary)
 ├─ statix + deadnix  → publishDiagnostics (debounced)
 ├─ nixfmt/alejandra  → textDocument/formatting
 └─ statix fix, -e    → source.fixAll.* code actions
```

Because tool discovery happens on your project's `$PATH` at language-server start, switching projects (or entering a devshell) changes what the extension runs — no configuration needed.

## Development

```sh
cargo build --release --target wasm32-wasip2
cp target/wasm32-wasip2/release/zed_nix_linters.wasm extension.wasm
# command palette → zed: rebuild dev extension
```

The bridge script lives at `scripts/nix-lint-lsp.py` and is compiled into the binary with `include_str!`, so there's no separate file to ship. Snippets are plain JSON — after editing them, run `python3 validate_snippets.py` to check prefixes, descriptions and duplicates.

Build outputs are deliberately not committed: `extension.wasm`, `grammars/nix.wasm`, the `grammars/nix/` source checkout and `target/` are all gitignored, because Zed regenerates them from `extension.toml` (grammar included) on every dev rebuild and on registry publishes.

When it's ready for the official registry, publishing is a small PR against [`zed-industries/extensions`](https://github.com/zed-industries/extensions): add this repo as a submodule and register the version in `extensions.toml`.

## Credits

The language config and tree-sitter queries are adapted from [zed-extensions/nix](https://github.com/zed-extensions/nix) (MIT). The grammar is [nix-community/tree-sitter-nix](https://github.com/nix-community/tree-sitter-nix). The tools it drives are [statix](https://github.com/oppiliappan/statix), [deadnix](https://github.com/oppiliappan/deadnix), [nixfmt](https://github.com/NixOS/nixfmt), [alejandra](https://github.com/kamadorueda/alejandra), [nixd](https://github.com/nix-community/nixd) and [nil](https://github.com/oxalica/nil).

## License

[MIT](LICENSE)
