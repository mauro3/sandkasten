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
- Minimal `passwd`, `group`, and `machine-id` files are generated under
  `$SANDBOX_HOME/.sandbox-etc` and mounted read-only at `/etc`. This lets D-Bus
  and other applications resolve the sandbox user without exposing the host
  account database or machine ID.
- Selected host files are copied directly into the sandbox home. Configuration
  directories use `cp -rL` so Home Manager symlinks are dereferenced rather
  than left dangling.
- After the initial copies, the launcher scans the sandbox home for literal
  `/nix/store/` references. References whose store hash is in the mounted tools
  closure are allowed; the launcher refuses unresolved store paths.
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
- `.git` entries and resolved Git/common directories are over-mounted read-only
  after the project bind. This covers ordinary repositories, nested repos,
  submodules, and linked worktrees. `GIT_OPTIONAL_LOCKS=0` keeps inspection
  commands from attempting optional index writes.
- `$HOME/.local/share/opencode` is bind-mounted live and read-write so OpenCode
  data and history persist across sessions. `$HOME/.pi` is also bind-mounted
  live and read-write so Pi credentials, settings, extensions, MCP credentials,
  and session history persist across sessions. These are intentional host state,
  not part of the disposable home.
- `~/.julia`, `~/.juliaup`, and `~/.cache/Tectonic` use writable BTRFS snapshots
  when their source is a subvolume. Snapshot failure falls back to
  `cp -r --reflink=auto`; an absent source becomes an empty directory.
- Known Home Manager handling fixes `~/.julia/config/startup.jl` after the Julia
  snapshot.
- `--no-julia` skips the Julia and Juliaup snapshots and removes Juliaup from
  `PATH`. Tectonic cache handling still runs.
- GPU access is disabled by default. `--gpu` passes through selected DRI devices,
  leaves the copied WezTerm renderer unchanged, and does not set
  `LIBGL_ALWAYS_SOFTWARE` in the generated Fish configuration.
- Sandbox `/tmp` is backed by a dedicated host directory named
  `/tmp/sandkasten.tmp.*`.
- On exit, the launcher offers to delete the sandbox home and temporary
  directory. `sandkasten-cleanup` removes retained homes and matching temporary
  directories; do not run it while sessions are active.

## Isolation And Host Integration

- System directories and selected resources are mounted read-only.
- PID, IPC, UTS, cgroup, and user namespaces are unshared. User namespace
  isolation is mandatory, and nested user namespaces are disabled. Some
  browsers and container tools may require removing `--disable-userns`.
- `--new-session` prevents controlling-terminal input injection, and
  `--die-with-parent` terminates sandbox processes if the launcher dies.
- The host network namespace is shared, including internet, LAN, and localhost
  services.
- WezTerm is launched through `dbus-run-session` inside Bubblewrap. The private
  bus daemon and any D-Bus-activated services therefore remain in the sandbox.
- FUSE, host desktop portals, PipeWire, and RealtimeKit are not exposed;
  related warnings from private desktop services are expected.
- Wayland is required. The Wayland socket is exposed; selected DRI devices are
  exposed only with `--gpu`.
- Device isolation uses a blacklist over `/dev`; it is not a strong hardware
  security boundary.
- `OPENAI_API_KEY`, `OPENAI_API_BASE`, and `ANTHROPIC_API_KEY` are passed into
  the sandbox.
- Bubblewrap uses `--clearenv`; every retained environment variable must be
  explicitly set by the launcher.
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
- Do not change the live OpenCode/Pi binds or disposable Julia/Tectonic behavior
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
./sandkasten --gpu /path/to/test-project
./sandkasten /path/to/test-project
```

Inside the sandbox, verify selected Nix commands, Fedora Fish/WezTerm/Helix,
configuration loading, OpenCode and Pi persistence, Julia behavior, and that an
unrelated `/nix/store/...` path and the Nix daemon are inaccessible.

## Relevant Paths

- `sandkasten` - main launcher
- `sandkasten-cleanup` - removes retained sandbox homes and temporary dirs
- `sandbox-tools.nix` - Nix package allowlist
- `flake.nix` / `flake.lock` - flake definition and pinned Nixpkgs revision
- `.sandkasten-nix-profile` - generated tools profile; ignored by Git
- `README.md` - user-facing setup, maintenance, and security documentation
