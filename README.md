# Sandkasten

A mostly secure, isolated development environment for safe AI-assisted coding. It uses [Bubblewarp](https://github.com/containers/bubblewrap), a lightweight Linux container also used, e.g., by Flatpak. The idea is to have this as frictionless as possible whilst giving a decent level of protection, likely good enough to guard against accidental AI-havok but not good enough to protect against a malicious actor.

Sandkasten creates a sandboxed development environment running inside a WezTerm terminal session with access to Julia, Helix editor, LaTeX with Tectonic, graphics in wayland, and aider AI coder. It uses `bwrap` (bubblewrap) to provide containerized isolation while maintaining access to your project files and development tools.

Running
```
$ sandkasten
```
will spawn a new WezTerm window which is sandboxed (including all its tabs and programs spwned by it) with read-write access to the current folder (but not parent folders). From there other sandboxed programs can be started, including graphical ones, such as a web browser.

## Usage

### Basic Usage
```bash
# Run sandbox in current directory
# (do not run as root!)
sandkasten

# Run sandbox in specific project directory
sandkasten /path/to/project
```

### Without Julia Support
```bash
# Disable Julia
sandkasten --no-julia
```
disable Julia support (avoids copying ~/.julia, ~/.juliaup)

### Cleanup

Remove old sandbox directories by running:
```bash
sandkasten-cleanup
```

## Installation

Requirements:
- Linux system with bubblewrap (`bwrap`) installed
- WezTerm terminal emulator
- Fish shell
- Optional: Julia, Helix, Tectonic, aider AI coder or other agentic AI assistant
- BTRFS filesystem (recommended for efficient copying of a large Julia install)

1. Install dependencies:
```bash
# Arch Linux
sudo pacman -S bubblewrap wezterm fish helix

# Ubuntu/Debian
sudo apt install bubblewrap wezterm fish-shell helix
```

2. Make the script executable:
```bash
chmod +x sandkasten
```

3. Optionally, add to your PATH or create a symlink.

4. BTRFS setup (if using BTRFS):

- make `~/.julia` a subvolume
- use the `user_subvol_rm_allowed` mount option

## How It Works

1. **Sandbox Creation**: Creates isolated environment in `~/.cache/sandkasten/YYYY-MM-DD_XXXXXXXX` which is the home directory of the sandbox
   - Project directory gets read-write mounted in its normal filesystem location
   - Everything else in the home directory is not accessible
   - Some dev-tools settings and binaries are copied from the host to the sandbox:
2. **Configuration Copying**: Copies essential config files (Fish, WezTerm, Helix, Git)
3. **Julia Environment** (if enabled): Creates copy-on-write snapshots of Julia depot and related directories
4. **Container Launch**: Uses `bwrap` to create isolated namespace with restricted system access
5. **Development Session**: Launches WezTerm with custom environment and project access
6. **Cleanup**: Optionally removes temporary files after session ends

## Security Model

### Protection Provided
- **Filesystem Isolation**: Limited access to system directories (read-only)
- **Process Isolation**: Separate PID namespace prevents interference
- **User Namespace**: Isolated user context
- **Network Monitoring**: All network access is visible but not restricted

### What's Isolated
- System directories (`/usr`, `/bin`, `/lib`) are read-only
- Julia depot and related directories are copied (not shared)
- Temporary files are contained within sandbox
- Configuration changes don't affect main system

### What's NOT Isolated
- **Network Access**: Full internet connectivity (for AI tools, package downloads)
- **Project Directory**: Direct read/write access to your project files
- **GPU/Hardware**: Direct access to graphics hardware
- **API Keys**: Environment variables are passed through

## Security Considerations

⚠️ **Important**: This sandbox provides protection against **accidental damage** but has significant limitations against **malicious intent**.

### Known Security Issues
Points 1-3 are required for project work and AI interaction (unless using local AI). Point 4 is partially mitigated. Point 5 assumes configuration files contain no sensitive data.

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

