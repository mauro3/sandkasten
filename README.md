# Sandkasten

A sandboxed and mostly secure development environment for safe AI-assisted coding. It uses [Bubblewrap](https://github.com/containers/bubblewrap), a lightweight Linux container also used by, e.g., Flatpak. The idea is to have `sandkasten` to be as frictionless as possible to use whilst giving a decent level of protection: likely good enough to guard against accidental AI-havok but not good enough to protect against a malicious actor.

Running
```
$ sandkasten
```
will spawn a new WezTerm window which is sandboxed (including all its tabs and programs spawned by it) with read-write access to the current folder (but not parent folders). From there other sandboxed programs can be started, including graphical ones, such as a web browser.

#### Health warning
The setup is geared towards my use cases, namely, Julia coding and LaTeX writing using an AI command line agent such as opencode (whose configs are copied into the sandbox). However as the `sandkasten` bash script is pretty simple it could easily be adjusted. Albeit, there are many other options around which may be more plug-and-play (but may well be a lot more complicated too), see below for links to a few of them.

## Usage

```bash
# Run sandbox in current directory
# (do not run as root!)
sandkasten

# Run sandbox in specific project directory
sandkasten /path/to/project
```

Without Julia Support
```bash
# Disable Julia
sandkasten --no-julia
```
which avoids copying `~/.julia`, `~/.juliaup`.

Cleanup: remove old sandbox directories by running
```bash
sandkasten-cleanup
```

## Installation

Requirements:
- Linux system with bubblewrap (`bwrap`) installed
- Nix with flakes enabled
- WezTerm terminal emulator
- Fish shell
- Optional: Julia, Helix, Tectonic, opencode AI coder or other agentic AI assistant
- BTRFS filesystem (recommended for efficient copying of a large Julia install)

1. Install dependencies:
```bash
# Arch Linux
sudo pacman -S bubblewrap wezterm fish helix

# Ubuntu/Debian
sudo apt install bubblewrap wezterm fish-shell helix
```

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

5. BTRFS setup (if using BTRFS):

- make `~/.julia` a subvolume
- use the `user_subvol_rm_allowed` mount option on the parent btrfs mount to allow deletion without root permissions

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

1. **Sandbox Creation**: Creates isolated environment in `~/.cache/sandkasten/YYYY-MM-DD_XXXXXXXX` which is the home directory of the sandbox
   - Project directory gets read-write mounted in its normal filesystem location
   - Everything else in the home directory is not accessible
   - Some dev-tools settings and binaries are copied from the host to the sandbox:
     - **Configuration Copying**: Copies essential config files and binaries (Fish, WezTerm, Helix, Git)
     - **Nix Tools**: Read-only mounts only the runtime closure of the allowlisted
       `.sandkasten-nix-profile`, exposed inside the sandbox at
       `/opt/sandkasten-tools`
     - **Julia Environment** (if enabled): Creates copy-on-write snapshots of Julia depot and related directories (`~/.julia` and `~/.juliaup`). Note that I symlink the folders with configurations, namely, `~/.julia/dev`, `config` and `environments` from elsewhere and thus I copy those over.
2. **Container Launch**: Uses `bwrap` to create isolated namespace with restricted system access
3. **Development Session**: Launches WezTerm with custom environment and project access
4. **Cleanup**: Optionally removes temporary files after session ends, alternatively use `sandkasten-cleanup` script

## Security Model

### Protection Provided
- **Filesystem Isolation**: Limited access to system directories (read-only)
- **Process Isolation**: Separate PID namespace prevents interference
- **User Namespace**: Isolated user context
- **Network Monitoring**: All network access is visible but not restricted

### What's Isolated
- System directories (`/usr`, `/bin`, `/lib`) are read-only
- Only the selected Nix tools and their runtime dependencies are mounted from
  `/nix/store`; the complete store and Nix daemon are not exposed
- Julia depot and related directories are copied (not shared)
- Temporary files are contained within sandbox
- Configuration changes don't affect main system

### What's NOT Isolated
- **Network Access**: Full internet connectivity (for AI tools, package downloads)
- **Project Directory**: Direct read/write access to your project files
- **GPU/Hardware**: Direct access to graphics hardware
- **API Keys**: Environment variables are passed through

⚠️ **Important**: This sandbox provides protection against **accidental damage** but has significant limitations against **malicious intent**.

### Known Security Issues
Below points 1-3 are required for project work and AI interaction (unless using local AI). Point 4 is partially mitigated. Point 5 assumes configuration files contain no sensitive data.

1. **Network Access**: Full network connectivity allows:
   - Data exfiltration of project files
   - Downloading/uploading arbitrary content
   - Communication with external services

2. **Project Directory Exposure**: 
   - AI has full read/write access to your project
   - Can modify, delete, or corrupt project files
   - Can access sensitive data in project directory

3. **API Key Exposure**:
   - OpenAI and Anthropic API keys are passed through
   - Could be logged or misused by AI tools

4. **Hardware Access**:
   - Full GPU access could potentially be exploited
   - Access to `/dev` directory (though containerized)

5. **Configuration Leakage**:
   - Git configuration and other sensitive configs are copied
   - Could expose personal information or credentials

## Similar projects

There are a ton of similar projects out there... a few:

- https://github.com/bigattichouse/scratchpad 
- https://docs.anthropic.com/en/docs/claude-code/devcontainer
- https://github.com/nikvdp/cco
- https://github.com/RchGrav/claudebox

There are many like it, but this one is mine. 

## TODO / ideas

- allow re-starting with a previous sandboxed environment to get presistent sessions
