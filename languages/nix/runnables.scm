; Matches the `outputs = { self, nixpkgs }: ...` binding in a flake.nix,
; providing a gutter "run" button bound to the nix-check tag.
;
; Tags bind to the task templates in tasks.json — users can override
; those in a workspace .zed/tasks.json or in their global tasks.json.

((binding
    attrpath: (attrpath
      (identifier) @run)
    expression: (function_expression)
    (#eq? @run "outputs")
  )
  @_nix-flake-check
  (#set! tag nix-check)
)