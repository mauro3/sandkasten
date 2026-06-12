# Sandkasten — Agent Context

## What it is
A bash script that wraps `bwrap` (Bubblewrap) to launch a sandboxed WezTerm session for safe AI-assisted coding. Configs and binaries are copied into the sandbox; the project directory is bind-mounted read-write; everything else is isolated.

## Key mechanics
- `$HOME/.cache/sandkasten/YYYY-MM-DD_XXXXXXXX/` is the sandbox home (`$SANDBOX_HOME`)
- Config dirs are copied from host into `$SANDBOX_HOME` with `cp -rL` (dereferences symlinks — important because Nix Home Manager manages many files as symlinks)
- Large tool dirs (`~/.julia`, `~/.juliaup`, `~/.cache/Tectonic`) use BTRFS snapshots first; fall back to `cp -r` (preserves symlinks as-is)
- After Julia snapshot, known Nix HM symlinks are fixed up: `~/.julia/config/startup.jl`
- `$HOME/.local/share/opencode` is bind-mounted live (not copied) — `auth.json` in that dir is handled via host-side symlink to `~/.config/opencode/auth.json`
- `$PROJECT_ROOT` is `realpath`'d before bind-mounting
- No `--hostname` flag is passed to bwrap (potential issue with flatpak-spawn)

## Nix Home Manager
Dotfiles are managed by Nix HM, meaning many files in `~/.config`, `~/.local`, `~/.julia/config` etc. are symlinks. These must be dereferenced before the sandbox uses them, otherwise they dangle.

## Style notes
- Single bash script, no dependencies beyond bwrap, WezTerm, and a BTRFS-capable filesystem
- Comments use backtick code fences inside bash comments: `` `# === Section ===` ``
- Helper functions at top, main logic below
- Use `echo "message" >&2` for informational output

## Testing / validation
- No formal test suite
- Validation is manual: run `./sandkasten [--no-julia] [dir]` and verify sandbox launches

## Relevant paths
- `sandkasten` — main script
- `sandkasten-cleanup` — removes stale sandbox dirs
