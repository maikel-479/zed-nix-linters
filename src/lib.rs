//! nix-linters: a Zed extension that gives Nix files a complete, zero-config
//! setup — language support, a language server, linting and formatting.
//!
//! * Language + tree-sitter grammar for Nix (see `languages/`, `extension.toml`).
//! * `nixd`/`nil` language server with runtime detection: nixd is preferred
//!   (nixpkgs package + NixOS option completion), nil is the fallback.
//!   nil is opt-in via the `language_servers` setting.
//! * `nix-lint`: an LSP bridge that runs statix, deadnix, nixfmt and
//!   alejandra — whichever are installed — for diagnostics, formatting
//!   and autofix code actions.
//!
//! Everything is discovered at runtime from the worktree's $PATH, so the
//! extension works on any machine without configuration.

use zed_extension_api::{self as zed, Command, LanguageServerId, Result, Worktree};

/// The LSP bridge, embedded into the extension binary at compile time and
/// fed to `python3 -c` at runtime so no separate file has to be shipped.
const BRIDGE_SOURCE: &str = include_str!("../scripts/nix-lint-lsp.py");

/// Interpreter candidates for the bridge, best first.
const PYTHON_CANDIDATES: &[&str] = &["python3", "python"];

/// Linters and formatters the bridge can drive, with the flag each one is
/// passed to the bridge under.
const TOOLS: &[(&str, &str)] = &[
    ("--statix", "statix"),
    ("--deadnix", "deadnix"),
    ("--nixfmt", "nixfmt"),
    ("--alejandra", "alejandra"),
];
const LINTER_NAMES: &[&str] = &["statix", "deadnix"];

/// Formatters preferred for language-server formatting, best first.
const FORMATTER_CANDIDATES: &[&str] = &["nixfmt", "alejandra"];

/// Language server IDs served by this extension, and their preferred
/// binary order: the first installed candidate wins.
const LSP_CANDIDATES: &[(&str, &[&str])] = &[
    ("nixd", &["nixd", "nil"]),
    ("nil", &["nil", "nixd"]),
];

struct NixLinters;

impl NixLinters {
    /// Find the first available candidate on the worktree's $PATH.
    fn find(worktree: &Worktree, candidates: &[&str]) -> Option<String> {
        candidates.iter().find_map(|name| worktree.which(name))
    }

    /// The binary a language server id would actually run on this machine.
    fn lsp_binary(&self, id: &str, worktree: &Worktree) -> Option<String> {
        LSP_CANDIDATES
            .iter()
            .find(|(server, _)| *server == id)
            .and_then(|(_, candidates)| Self::find(worktree, candidates))
    }

    fn bridge_command(&self, id: &LanguageServerId, worktree: &Worktree) -> Result<Command> {
        let python = Self::find(worktree, PYTHON_CANDIDATES).ok_or_else(|| {
            "python3 is required to run the nix-lint bridge. Install Python 3 and make sure \
             it is on $PATH."
                .to_string()
        })?;

        let mut args = vec!["-c".to_string(), BRIDGE_SOURCE.to_string()];

        // Pin the bridge to whichever tools this machine actually has.
        // Passing paths explicitly means tools installed outside $PATH in
        // the extension host still work.
        let mut found_linter = false;
        for (flag, name) in TOOLS {
            if let Some(path) = worktree.which(*name) {
                if LINTER_NAMES.contains(name) {
                    found_linter = true;
                }
                args.push(flag.to_string());
                args.push(path);
            }
        }

        if !found_linter {
            zed::set_language_server_installation_status(
                id,
                &zed::LanguageServerInstallationStatus::Failed(
                    "no Nix linter found; install statix or deadnix \
                     (e.g. `nix profile install nixpkgs#statix nixpkgs#deadnix`)"
                        .to_string(),
                ),
            );
        }

        Ok(Command {
            command: python,
            args,
            env: Vec::new(),
        })
    }

    fn lsp_command(&self, id: &str, worktree: &Worktree) -> Result<Command> {
        let binary = self.lsp_binary(id, worktree).ok_or_else(|| {
            format!(
                "no Nix language server found. Install nixd (recommended, gives nixpkgs \
                 package completion) or nil, e.g. `nix profile install nixpkgs#nixd`."
            )
        })?;
        Ok(Command {
            command: binary,
            args: Vec::new(),
            env: Vec::new(),
        })
    }

    /// Default configuration for the language server `id`, shaped for
    /// whichever binary will actually run. User `lsp.<id>` settings in
    /// settings.json are merged on top by Zed and override these.
    fn lsp_initialization_options(&self, id: &str, worktree: &Worktree) -> Result<Option<zed::serde_json::Value>> {
        if !LSP_CANDIDATES.iter().any(|(server, _)| *server == id) {
            return Ok(None);
        }

        let picked = self.lsp_binary(id, worktree);
        let nixd_binary = worktree.which("nixd");
        let picked_is_nixd = picked.is_some() && picked == nixd_binary;

        let mut config = zed::serde_json::json!({});

        // Formatter for the language server's own format capability
        // (nix-lint formats independently; this covers `formatter: "language_server"`).
        if let Some(formatter) = Self::find(worktree, FORMATTER_CANDIDATES) {
            config["formatting"] = zed::serde_json::json!({ "command": [formatter] });
        }

        if picked_is_nixd {
            // Package completion (cowsay, lolcat, ...) and NixOS options.
            config["nixpkgs"] = zed::serde_json::json!({ "expr": "import <nixpkgs> { }" });
            config["diagnostic"] =
                zed::serde_json::json!({ "suppress": ["sema-extra-with"] });
        } else if picked.is_none() {
            return Ok(None);
        }

        Ok(Some(config))
    }
}

impl zed::Extension for NixLinters {
    fn new() -> Self {
        Self
    }

    fn language_server_command(
        &mut self,
        language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<Command> {
        match language_server_id.as_ref() {
            "nix-lint" => self.bridge_command(language_server_id, worktree),
            id => self.lsp_command(id, worktree),
        }
    }

    fn language_server_initialization_options(
        &mut self,
        language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<Option<zed::serde_json::Value>> {
        self.lsp_initialization_options(language_server_id.as_ref(), worktree)
    }

    fn language_server_workspace_configuration(
        &mut self,
        language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<Option<zed::serde_json::Value>> {
        // nixd fetches its configuration via workspace/configuration; return
        // the same defaults there so an empty response doesn't reset them.
        self.lsp_initialization_options(language_server_id.as_ref(), worktree)
    }
}

zed::register_extension!(NixLinters);
