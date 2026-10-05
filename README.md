# Sandkasten

A sandboxed development environment for safer AI-assisted coding. It uses
[Bubblewrap](https://github.com/containers/bubblewrap), a lightweight Linux
container tool also used by Flatpak. The aim is to remain frictionless while
providing useful protection against accidental damage. It is not intended to
contain malicious code or a malicious actor.

Running
```bash
sandkasten
```
spawns a sandboxed WezTerm window. Its tabs and child processes have read-write
access to the selected project directory, but not to its parent directories or
the rest of the host home. Other programs, including graphical programs, can be
started from the terminal.

#### Health warning
The setup is geared towards Julia coding and LaTeX writing with an AI command
line agent such as OpenCode or Pi. It is intentionally opinionated and copies selected
files from the host configuration. Other projects may be more general or more
plug-and-play; some are linked below.

## Usage

```bash
# Run sandbox in current directory
# (do not run as root!)
sandkasten

# Run sandbox in specific project directory
sandkasten /path/to/project
```

Without Julia support:
```bash
sandkasten --no-julia
```

This skips the Julia depot and Juliaup snapshots and omits Juliaup from the
sandbox `PATH`. It does not disable the Tectonic cache snapshot.

GPU access and hardware rendering are disabled by default. Enable the existing
DRI device passthrough and use the normal WezTerm renderer when needed with:

```bash
sandkasten --gpu
```

The options can be combined, for example `sandkasten --no-julia --gpu`.

The private desktop session may log warnings about FUSE, desktop portal
fallbacks, PipeWire, or RealtimeKit. These integrations are intentionally not
exposed by default. If the WezTerm window works and the launcher reports
`bwrap exited with code: 0`, those messages do not indicate a failed sandbox
launch.

After all sandbox sessions have exited, remove every retained sandbox home and
its matching temporary directory with:

```bash
sandkasten-cleanup
```

To remove only selected homes, pass either their full paths or their directory
names from `~/.cache/sandkasten`:

```bash
sandkasten-cleanup 2026-09-23_0123456789abcdef
```

## Installation

Requirements:
- x86-64 Linux system with Bubblewrap (`bwrap`) installed
- Nix with flakes enabled
- WezTerm at `/usr/bin/wezterm`
- Fish at `/usr/bin/fish`
- An active Wayland session (`XDG_RUNTIME_DIR` and `WAYLAND_DISPLAY` must be set)
- Helix (`hx`) and Git from the system installation
- `bash`, `dbus-daemon`, `openssl`, and standard GNU utilities
- Optional: Juliaup and Julia
- Optional: BTRFS and `btrfs-progs` for efficient writable snapshots

1. Install dependencies:
```bash
# Fedora
sudo dnf install bubblewrap wezterm fish helix git dbus-daemon openssl btrfs-progs

# Arch Linux
sudo pacman -S bubblewrap wezterm fish helix git dbus openssl btrfs-progs

# Ubuntu/Debian
sudo apt install bubblewrap wezterm fish git dbus-daemon openssl btrfs-progs
```

Package names and availability vary by distribution. The remaining development
tools are built from the allowlist in `sandbox-tools.nix`.

2. Build the allowlisted Nix tools environment:
```bash
nix build .#sandbox-tools -o .sandkasten-nix-profile
```

Re-run this command after changing `sandbox-tools.nix` or updating `flake.lock`.
The generated profile link is ignored by Git and must remain next to the
`sandkasten` script.

3. Make the script executable:
```bash
chmod +x sandkasten
```

4. Optionally, add to your PATH or create a symlink.

The launcher resolves repository-managed files relative to the real script, so
it can be invoked through a symlink from any project directory.

5. BTRFS setup (if using BTRFS):

- Make large source directories such as `~/.julia` BTRFS subvolumes.
- Use the `user_subvol_rm_allowed` mount option on the parent BTRFS mount so
  snapshots can be deleted without root permissions.

Sandkasten attempts a writable BTRFS snapshot for `~/.julia`, `~/.juliaup`, and
`~/.cache/Tectonic`. A source that is absent becomes an empty directory; a
source that cannot be snapshotted is copied normally.

## Managing the Nix tools

The Nix configuration in this repository is independent of the Nix packages
installed by Home Manager. It uses the package versions pinned in `flake.lock`
and exposes only the packages listed in `sandbox-tools.nix`.

Run all commands in this section from the Sandkasten repository.

### Add or remove a package

Edit the `paths` list in `sandbox-tools.nix`. Package names can be found with
[Nix package search](https://search.nixos.org/packages) or from the command
line:

```bash
nix search nixpkgs package-name
```

Then rebuild the profile:

```bash
nix build .#sandbox-tools -o .sandkasten-nix-profile
```

The profile link is updated only after a successful build. Restart Sandkasten
sessions to use the changed package set.

### Update package versions

An ordinary `nix build` continues to use the exact Nixpkgs revision recorded in
`flake.lock`. To select newer package versions, update the lock file and rebuild:

```bash
nix flake update
nix build .#sandbox-tools -o .sandkasten-nix-profile
```

Review and commit the resulting `flake.lock` change so other builds use the
same versions. Updating a separate Home Manager flake does not update this
profile, and updating this flake does not update Home Manager.

### Check package versions

Run a program through the generated profile, for example:

```bash
.sandkasten-nix-profile/bin/opencode --version
.sandkasten-nix-profile/bin/vale --version
.sandkasten-nix-profile/bin/python3 --version
```

To compare a sandbox package with the version available through the normal host
`PATH`:

```bash
opencode --version
.sandkasten-nix-profile/bin/opencode --version
```

To inspect the complete runtime closure, including libraries and other
dependencies:

```bash
nix path-info --recursive .#sandbox-tools
```

This list is intentionally much larger than the package list in
`sandbox-tools.nix`.

### Clean old packages

The `.sandkasten-nix-profile` link protects the current tools and dependencies
from garbage collection. After updating the profile, remove store paths that
are no longer referenced by any Nix profile or other garbage-collection root:

```bash
nix-collect-garbage
```

This does not remove packages still used by Sandkasten, Home Manager, or another
Nix profile. Avoid `nix-collect-garbage -d` unless you also intend to delete old
profile generations and give up their rollback options.

## How It Works

1. **Sandbox creation:** Creates a disposable home at
   `~/.cache/sandkasten/YYYY-MM-DD_XXXXXXXXXXXXXXXX` and a matching host-side
   `/tmp/sandkasten.tmp.*` directory that becomes the sandbox `/tmp`. It also
   generates minimal `passwd`, `group`, and `machine-id` files for the sandbox
   instead of exposing the host account database or machine ID.
2. **Configuration copying:** Copies selected Fish, WezTerm, Zathura, Helix,
   Vale, OpenCode, and Git configuration and selected local scripts. Directory
   copies dereference Home Manager symlinks so they remain usable and writable
   without modifying the host configuration. Literal `/nix/store/` references
   are allowed only when their store hash is already in the mounted tools
   closure; launch stops and reports references to unmounted paths.
3. **Nix tools:** Resolves the runtime closure of
   `.sandkasten-nix-profile`, mounts only those `/nix/store` paths read-only,
   and exposes the profile as `/opt/sandkasten-tools`. The Nix daemon and the
   rest of the store are not mounted.
4. **Writable state:** Bind-mounts the project read-write. OpenCode data and
   history at `~/.local/share/opencode` and Pi agent state at `~/.pi` are also
   bind-mounted read-write so credentials, settings, and session history persist
   between sessions. Julia, Juliaup, and Tectonic use disposable
   writable snapshots or copies instead. Git metadata is over-mounted read-only,
   including linked worktree and submodule metadata, while working-tree files
   remain writable. Git inspection works, but commits, staging, ref changes,
   hook changes, and repository configuration changes are blocked.
5. **Session launch:** Creates PID, IPC, UTS, cgroup, and mandatory user
   namespace isolation, starts a new terminal session, and terminates sandbox
   processes if the launcher dies. It exposes Wayland and launches WezTerm
   through `dbus-run-session`. The private D-Bus daemon and any services it
   activates run inside the Bubblewrap sandbox. GPU access is disabled and
   WezTerm uses software rendering unless `--gpu` is passed. Nested user
   namespaces are disabled; this may need to be relaxed for some browsers or
   container tools.
6. **Cleanup:** Offers to remove the disposable home and temporary directory
   when WezTerm exits. Declining keeps them until `sandkasten-cleanup` is run;
   there is currently no option to resume a retained home.

## Security Model

### Protection Provided
- **Filesystem isolation:** System files and the selected Nix closure are
  read-only; the rest of the host home is not mounted.
- **Process isolation:** A separate PID namespace hides host processes.
- **Namespace isolation:** IPC, UTS, cgroup, and user namespaces are unshared.
  Launch fails if a user namespace cannot be created, and nested user
  namespaces are disabled inside the sandbox.
- **Terminal and lifecycle isolation:** A new terminal session prevents
  controlling-terminal input injection, and sandbox processes are killed if
  the launcher dies.
- **D-Bus isolation:** Applications use a private session bus rather than the
  host session bus. The bus daemon and activated services run inside the
  sandbox.
- **Environment isolation:** Bubblewrap clears the inherited host environment
  and sets only the variables explicitly required by the sandbox.

### What's Isolated
- System directories (`/usr`, `/bin`, `/lib`) are read-only
- Only the selected Nix tools and their runtime dependencies are mounted from
  `/nix/store`; the complete store and Nix daemon are not exposed
- Julia, Juliaup, and Tectonic state use writable snapshots or copies rather
  than live host binds
- Sandbox `/tmp` uses a dedicated host directory
- Copied configuration can be modified without changing its host source
- Git metadata is read-only while project working-tree files remain writable

### What's NOT Isolated
- **Network Access**: The host network namespace is shared, including internet,
  LAN, and localhost services
- **Project Directory**: Direct read/write access to your project files
- **AI Agent State**: Direct read/write access to the host OpenCode data and
  history directory and Pi state under `~/.pi`
- **GPU/Hardware with `--gpu`**: Selected DRI devices are passed through; device
  hiding uses a blacklist and should not be treated as a strong hardware
  boundary
- **API Keys**: `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are passed through and
  written into the disposable Fish configuration

**Important:** This sandbox provides protection against accidental damage but
has significant limitations against malicious intent. Network access allows
anything readable inside the sandbox to be exfiltrated.

### Known Security Issues
1. **Network Access**: Sharing the host network namespace allows:
   - Data exfiltration of project files
   - Downloading/uploading arbitrary content
   - Communication with internet, LAN, and host-local services

2. **Project Directory Exposure**: 
   - AI has full read/write access to your project
   - Can modify, delete, or corrupt project files
   - Can access sensitive data in project directory

3. **API Key Exposure**:
   - OpenAI and Anthropic API keys are passed through
   - Keys can be logged or misused by sandboxed programs
   - Retained sandbox homes contain the generated Fish file with these keys

4. **Hardware Access**:
   - GPU devices passed through with `--gpu` could potentially be exploited
   - The device blacklist may not cover every host-specific device node

5. **Configuration Leakage**:
   - Git, OpenCode, and other selected configuration is copied
   - Configuration may expose personal information or credentials

6. **Persistent AI Agent State**:
   - Sandboxed programs can modify or delete the live OpenCode data/history
   - Sandboxed programs can modify or delete Pi credentials, settings, extensions,
     MCP credentials, and session history under `~/.pi`
   - Persistence is intentional, but it is outside the disposable-home boundary

## Similar projects

There are a ton of similar projects out there... a few:

- https://github.com/bigattichouse/scratchpad
- https://docs.anthropic.com/en/docs/claude-code/devcontainer
- https://github.com/nikvdp/cco
- https://github.com/RchGrav/claudebox

There are many like it, but this one is mine.

## Future Work

See [TODOs.md](TODOs.md) for the backlog, including security hardening work.
