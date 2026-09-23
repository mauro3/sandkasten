# Sandkasten - Agent Context

## What It Is

Sandkasten is a Bash launcher around Bubblewrap (`bwrap`). It starts a
sandboxed WezTerm session for AI-assisted development with read-write access to
one project directory. It is intended to limit accidental damage, not to contain
malicious code.

The host is Fedora on x86-64 Linux. Fish, WezTerm, Helix, Git, Julia, and
Juliaup come from Fedora or other non-Nix host installations. Selected
development tools come from the repository's pinned Nix flake.

## Nix Tools

- `sandbox-tools.nix` is the explicit package allowlist, built with
  `pkgs.buildEnv`.
- `flake.nix` uses `nixpkgs-unstable`; `flake.lock` pins the exact revision.
- Build or rebuild on the host with:
  `nix build .#sandbox-tools -o .sandkasten-nix-profile`.
- `.sandkasten-nix-profile` is ignored by Git but must exist next to the real
  `sandkasten` script. The launcher resolves its own path, so invocation through
  a symlink in `PATH` works.
- At launch, `nix-store --query --requisites` resolves the profile's runtime
  closure. Each required store path is mounted read-only at its original
  `/nix/store/...` path.
- The profile is exposed inside the sandbox as `/opt/sandkasten-tools` and its
  `bin` directory is first in the controlled sandbox `PATH`.
- The complete `/nix/store`, host Home Manager profile, and Nix daemon socket
  are not mounted. Nix builds and package installation are not supported inside
  the sandbox.
- Home Manager and Sandkasten have independent flakes and lock files. Updating
  one does not update the other.

## Sandbox Home And Configuration

- `$HOME/.cache/sandkasten/YYYY-MM-DD_XXXXXXXXXXXXXXXX/` is the disposable
  sandbox home (`$SANDBOX_HOME`). The random suffix comes from
  `openssl rand -hex 8` and is 16 hexadecimal characters.
- Selected host files are copied directly into the sandbox home. Configuration
  directories use `cp -rL` so Home Manager symlinks are dereferenced rather
  than left dangling.
- After the initial copies, the launcher scans the sandbox home for literal
  `/nix/store/` references. It refuses to launch if any copied file still
  depends on a store path.
- WezTerm configuration is patched after copying. A sandbox-specific Fish file
  sets the prompt, editor, graphics option, and API keys.
- The generated Fish configuration contains `OPENAI_API_KEY` and
  `ANTHROPIC_API_KEY` values. Retained sandbox homes therefore contain these
  secrets until cleaned up.
- There is no separate sandbox Home Manager configuration or generated home
  output. The launcher contains the current explicit file allowlist.

## Writable And Persistent State

- `$PROJECT_ROOT` is resolved with `realpath` and bind-mounted read-write at its
  normal absolute path.
- `$HOME/.local/share/opencode` is bind-mounted live and read-write so OpenCode
  data and history persist across sessions. This is intentional host state, not
  part of the disposable home.
- `~/.julia`, `~/.juliaup`, and `~/.cache/Tectonic` use writable BTRFS snapshots
  when their source is a subvolume. Snapshot failure falls back to `cp -r`; an
  absent source becomes an empty directory.
- Known Home Manager handling fixes `~/.julia/config/startup.jl` after the Julia
  snapshot.
- `--no-julia` skips the Julia and Juliaup snapshots and removes Juliaup from
  `PATH`. Tectonic cache handling still runs.
- Sandbox `/tmp` is backed by a dedicated host directory named
  `/tmp/sandkasten.tmp.*`.
- On exit, the launcher offers to delete the sandbox home and temporary
  directory. `sandkasten-cleanup` removes retained homes and matching temporary
  directories; do not run it while sessions are active.

## Isolation And Host Integration

- System directories and selected resources are mounted read-only.
- PID, UTS, and cgroup namespaces are unshared. User namespace isolation uses
  `--unshare-user-try` and can fall back if unavailable.
- The host network namespace is shared, including internet, LAN, and localhost
  services.
- A private D-Bus session is started for the sandbox.
- Wayland is required. The Wayland socket and selected DRI devices are exposed.
- Device isolation uses a blacklist over `/dev`; it is not a strong hardware
  security boundary.
- `OPENAI_API_KEY`, `OPENAI_API_BASE`, and `ANTHROPIC_API_KEY` are passed into
  the sandbox.
- No `--hostname` flag is passed to `bwrap`; this may affect `flatpak-spawn`.

## Implementation Style

- Keep the launcher as a single Bash script with helper functions first and the
  main flow below.
- Prefer the smallest correct change and preserve the existing direct approach.
- Use Bash arrays for generated Bubblewrap argument groups; do not construct
  unquoted argument strings.
- Section comments inside the continued `bwrap` command use the existing
  backtick-comment form: `` `# === Section ===` ``.
- Send informational and error output to stderr where appropriate.
- Do not expose all of `/nix`, the Nix daemon, or host Home Manager generations.
- Do not change the live OpenCode bind or disposable Julia/Tectonic behavior
  without treating it as a security and persistence decision.

## Testing And Validation

There is no automated test suite. At minimum:

```bash
bash -n sandkasten
git diff --check
```

Host-only manual validation:

```bash
nix build .#sandbox-tools -o .sandkasten-nix-profile
./sandkasten --no-julia /path/to/test-project
./sandkasten /path/to/test-project
```

Inside the sandbox, verify selected Nix commands, Fedora Fish/WezTerm/Helix,
configuration loading, OpenCode persistence, Julia behavior, and that an
unrelated `/nix/store/...` path and the Nix daemon are inaccessible.

## Relevant Paths

- `sandkasten` - main launcher
- `sandkasten-cleanup` - removes retained sandbox homes and temporary dirs
- `sandbox-tools.nix` - Nix package allowlist
- `flake.nix` / `flake.lock` - flake definition and pinned Nixpkgs revision
- `.sandkasten-nix-profile` - generated tools profile; ignored by Git
- `README.md` - user-facing setup, maintenance, and security documentation
