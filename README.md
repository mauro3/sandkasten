# Sandkasten

Bubblewrap sandboxes for working with AI agents on a project. The aim is to
limit accidental damage, not to contain deliberately malicious code (see
[Security model](#security-model)). Two launchers (written in Python 3.12 with only
the standard library):

- `sandkasten`: a headless Bash shell in the current terminal, e.g. an outer
  tmux pane on a server. Needs Bubblewrap, GNU `cp`, and system tools; no Nix,
  Wayland, or GUI.
  - this script should be fairly usable for anyone
- `sandkasten3`: a desktop sandbox running WezTerm and Fish on Wayland, with a
  private D-Bus and selected tools from this repository's pinned Nix flake.
  - this script is heavily customized for my setup

Both share the saved project homes, copy rules, locks, and private network.
The default private network also needs pasta, nftables, `setpriv`, and `ip`
(see [Private network](#private-network)).

## Health warning

This tool is vibe-coded. Whilst I do review the code, sandboxes are anything
but my speciality.

# Usage

## sandkasten -- open a sandboxed, headless bash-shell

```bash
./sandkasten /path/to/project
./sandkasten --no-julia --no-host-config /path/to/project
./sandkasten -c ~/.netrc -c ~/.config/mytool /path/to/project
./sandkasten --dry-run /path/to/project
./sandkasten --fresh /path/to/project     # Separate one-off home, kept
./sandkasten --tmp /path/to/project       # Separate one-off home, deleted on exit
./sandkasten --host-net /path/to/project  # Share the host network instead
```

The project defaults to the current directory. On a server, start tmux
**outside** the sandbox and run `sandkasten` in that pane; an SSH disconnect
then leaves tmux and the sandbox running. Closing the shell ends the session.

The project is writable and system directories are read-only. So is Git
metadata: the `.git` directories and pointer files found at launch, and the
Git directories that pointers or `commondir` files name inside the project
(e.g. a sibling bare repository of a linked worktree). Pointer targets outside
the project are not exposed, so such worktrees may not work. `/tmp` is
private, and the environment is cleared except for `TERM`, `LANG`, and
`OPENAI_API_KEY`, `OPENAI_API_BASE`, `ANTHROPIC_API_KEY`. User namespaces
must be permitted; the launcher does not bypass AppArmor or other host limits.

`--dry-run` prints the Bubblewrap command (stdout) and the planned copies and
firewall rules (stderr), with API keys redacted. It creates, copies, and
launches nothing, so the printed paths are a preview, not a ready-to-run
command. Bubblewrap and `cp` need not be installed for it.

### Security
The shell runs in your terminal as its controlling terminal (no --new-session),
so job control (Ctrl-Z, fg, Ctrl-C) works.
A program could then push keystrokes into the terminal with the
`TIOCSTI` ioctl, to be run by the host shell after the sandbox exits, so the
launcher refuses to start unless the kernel blocks this
(`dev.tty.legacy_tiocsti = 0`, Linux 6.2 or later; Fedora's default). Escape
sequences still reach the terminal: with tmux's `set-clipboard on`, OSC 52 lets
sandboxed programs set the host clipboard. `set -g set-clipboard external` in
`~/.tmux.conf` prevents that and keeps tmux's own copy mode working.


## sandkasten3 -- open sandboxed wezterm in wayland

```bash
./sandkasten3 /path/to/project
./sandkasten3 --gpu /path/to/project           # Render nodes instead of software rendering
./sandkasten3 --full-wayland /path/to/project  # Normal Wayland socket (see below)
```

`sandkasten3` loads `sandkasten` and extends its Bubblewrap command, so all
headless options work too. It adds Wayland, a private D-Bus (WezTerm runs
through `dbus-run-session`, so the bus and services it starts stay in the
sandbox), the Nix tools, and WezTerm running Fish. WezTerm runs with
`--always-new-process`, so the launcher keeps its lock and `/tmp` until WezTerm
exits. Ctrl-C in the launcher's terminal does not end the session; it prints
the `kill -9` command that does. Closing that terminal ends it. FUSE, desktop portals, PipeWire, RealtimeKit, and the host session bus
are not exposed; related warnings are expected.

GPU access is off by default: WezTerm uses software rendering, selected on the
command line, so the saved WezTerm config is not changed. `--gpu` binds the DRI
render nodes (`/dev/dri/renderD*`), and `/sys/dev` and `/sys/devices`
read-only, which Mesa needs to pick the hardware driver; without them it falls
back to software rendering (`llvmpipe`). To check, open WezTerm's debug
overlay (Ctrl+Shift+L): its `OpenGL:` line names the GPU with `--gpu` and
`llvmpipe` without.

### Restricted Wayland socket

A client of the normal Wayland socket may use the compositor's privileged
protocols. On niri and other wlroots-style compositors that includes typing
into other windows (virtual keyboard, e.g. into a host terminal), screenshots
of all outputs (screencopy), background clipboard access (data control),
controlling all windows, and full-screen overlays or a fake lock screen.

So, like Flatpak, `sandkasten3` asks the compositor through
`wp_security_context_v1` for a separate socket in a private directory under
`$XDG_RUNTIME_DIR`, and mounts only that one as `wayland-0`. The compositor
hides the privileged protocols from its clients (on niri 31 instead of 45
globals). After the session the launcher closes the context's close pipe, so
the compositor stops listening, and removes the directory. Ordinary windows,
input to focused windows, and the clipboard while a sandbox window has focus
work as usual. Still offered, and much less severe: idle notification, DRM
leases, focus requests (the compositor decides), and shortcut inhibition while
a sandbox window has focus.

If the compositor lacks `wp_security_context_v1`, launch stops. `--full-wayland`
mounts the normal socket instead; use it only with a compositor that does not
offer privileged protocols to every client, or if you accept that risk.

### Desktop installation

The desktop launcher also needs Nix with flakes, WezTerm, Fish, and
`dbus-run-session` in `/usr/bin`, and an active Wayland session
(`XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY`). BTRFS with `btrfs-progs` is optional:
make the large source directories (`.julia`, ...) subvolumes to get cheap
snapshots, and mount with `user_subvol_rm_allowed` so cleanup can delete them
without root. On Fedora:

```bash
sudo dnf install bubblewrap passt nftables wezterm fish helix git dbus-daemon btrfs-progs
nix build .#sandbox-tools -o .sandkasten-nix-profile
```

The profile link must stay beside the launchers; they resolve their real
location, so a symlink in `PATH` works. Desktop dry-run still needs the Wayland
socket and the Nix profile, which it only inspects.

## Saved homes

The canonical project path selects a home,
`~/.cache/sandkasten/project-<first 12 hex characters of its SHA-256>`
(homes created earlier with 8 characters keep being used). Both launchers
share it, and later runs resume its files, credentials, and tool state; running
processes are not restored (use outer tmux). Symlinks to the project select the
same home; a moved project gets a new one. A host-side lock rejects
simultaneous launches with one home, from either launcher. `--fresh` uses a
separate `YYYY-MM-DD_<random>` home for that run, kept like any other; `--tmp`
does the same but deletes the home when the session ends (if the launcher is
killed with `kill -9`, `sandkasten-cleanup` removes it later).

Homes and `~/.cache/sandkasten` have mode 0700 and contain **any copied
credentials**. They survive exit and reboot but live in a cache directory,
which cache-cleaning tools may remove; back up valuable state elsewhere. `/tmp`
is recreated for each launch and removed on exit.

`sandkasten-cleanup <home-name>` deletes a home, `sandkasten-cleanup` all
inactive homes. It refuses locked homes. Lock files are kept on purpose, so
launch and cleanup always lock the same file; they hold no state.

## Copied files

New homes are seeded from the host; existing destinations (including whole
directories) are never overwritten, so sandbox changes survive restarts. To
recopy something, delete it in the sandbox home or use `--fresh`. These are
copies, not live binds.

- **Host config** (skip with `--no-host-config`): `.bashrc`, `.inputrc`,
  `.gitconfig`, `.gitignore.global`, `.config/git`, `.config/helix`, and Pi's
  `agent/auth.json` and `agent/settings.json`. The desktop adds Fish, WezTerm,
  Fontconfig, Zathura, Vale, and OpenCode config, some local scripts, and Pi
  (`~/.pi`) and OpenCode (`~/.local/share/opencode`) state. Pi is seeded entry
  by entry, so a `.pi/agent` from headless does not block the rest.
- **Julia** (skip with `--no-julia`, which also drops Juliaup from `PATH`):
  `.julia` and `.juliaup`; the desktop also copies `~/julia/dot-julia-dev` and
  `~/julia/dot-julia-environments`. A linked `startup.jl` (Home Manager's) is
  made a real file when the depot is first copied. Julia must be installed in
  the system paths or the copied Juliaup.
- **Tectonic** cache (desktop, always).
- **Extra paths** with `-c PATH` (repeatable): relative to the current
  directory, inside your home, copied to the same home-relative location
  (`~/.netrc` becomes sandbox `~/.netrc`). Missing sources are errors.
  `.sandbox-etc`, which holds generated files, is reserved.

Opting out does not delete saved files. Copied config is trusted code and may
contain credentials or references to tools missing in the sandbox.

**Links.** Configuration is copied with `cp -rL`, so links become real files:
links into `/nix/store` (Home Manager's), links within the copied directory,
and dotfiles links, which point to the same home-relative path in a checkout
(as Home Manager's out-of-store links and GNU Stow make them), e.g.
`~/.config/git/config` to `~/dotfiles/.config/git/config`. The copied path
itself may link anywhere. Any other link inside a copied directory, e.g. to
`~/.ssh`, stops the launch with a list of such links, since its target would be
copied too. Data (Julia, Tectonic, Pi/OpenCode state) keeps its inner links
(`cp -rH`) and is first tried as a writable BTRFS snapshot. Copies use
`--reflink=auto`.

**Desktop config.** Config, scripts, and their link targets are checked for
`/nix/store` references outside the mounted tools; launch stops and lists
them (fix them or add the packages to `sandbox-tools.nix`). AI histories, Julia
artifacts, and Tectonic caches are not scanned, and dry-run cannot check what
it has not copied. A newly copied `wezterm.lua` gets sandbox tab decorations
once. A managed `sandbox.fish`, sourced with `--init-command`, sets the prompt
and editor; it, the font config, and the identity files are rewritten on every
launch, while the private machine ID stays the same. API keys come from the
environment and are not written to files.

## Private network

Unless `--host-net` is given, both launchers run Bubblewrap in a private
network namespace created by [pasta](https://passt.top/):

```
setpriv --pdeathsig SIGKILL pasta --config-net -4 --no-map-gw -t none -u none -T none -U none \
    -- sh -c 'nft -f "$1" && shift && exec "$@"' sh <home>/.sandbox-etc/firewall.nft \
    env --default-signal bwrap ...
```

- **Internet** works through pasta, which forwards connections with ordinary
  host sockets. It is IPv4 only: home LAN devices often have global IPv6
  addresses that no fixed rule can tell from the internet. The rules also
  reject IPv6 except on the sandbox's loopback.
- **Host localhost** is unreachable: the gateway is not mapped to the host
  (`--no-map-gw`), and no ports are forwarded either way (`-t/-u/-T/-U none`).
  The sandbox has its own loopback, so servers inside it (e.g. HTTP MCP
  servers) work as usual.
- **LAN**: nftables rules, loaded before Bubblewrap starts, reject private
  ranges (`10/8`, `172.16/12`, `192.168/16`), link-local (`169.254/16`),
  Tailscale's `100.64/10`, and the subnets of the host's own IPv4 addresses.
  The last covers a LAN with public addresses, but only the host's own
  segment: the rest of e.g. a university's network, and public ranges routed
  through a VPN, stay reachable. Bubblewrap's own user namespace cannot change
  the rules.
- **DNS**: a generated `/etc/resolv.conf` lists the host's upstream servers
  (from `/run/systemd/resolve/resolv.conf` with systemd-resolved, otherwise
  `/etc/resolv.conf`), and the rules allow port 53 to exactly those. Loopback,
  link-local, and IPv6 servers are skipped; launch stops if none remain.
  Per-interface DNS (e.g. Tailscale MagicDNS) is not available.
- **Abstract Unix sockets** belong to the network namespace, so the host's
  (e.g. Xwayland's `@/tmp/.X11-unix/X0`) are unreachable.
- **Signals**: `env --default-signal` undoes pasta's ignored SIGPIPE.
- **Lifetime**: on SIGTERM or SIGHUP (e.g. closing its terminal), the launcher
  kills the sandbox and cleans up. pasta's death does not end the sandbox, so
  after `kill -9` of the launcher it keeps running, without network (with
  `--host-net`, Bubblewrap's `--die-with-parent` ends it).

DNS servers and subnets are read at launch: after switching networks (e.g.
from work to home), restart the sandbox; the home resumes. Requirements:
`pasta` (package `passt`), `nft` (`nftables`), `setpriv` (`util-linux`), and
`ip` (`iproute2`).

### Ubuntu: AppArmor profile for pasta

Since 24.04, Ubuntu lets only programs whose AppArmor profile allows it create
unprivileged user namespaces (`kernel.apparmor_restrict_unprivileged_userns =
1`). Bubblewrap has such a profile; pasta does not, since the `passt` package's
profile covers only `/usr/bin/passt`, and `/usr/bin/pasta` (a hard link to it)
runs unconfined. pasta then fails with
`Couldn't write to /proc/self/uid_map: Operation not permitted`. Give pasta the
same profile as Bubblewrap, once:

```bash
sudo tee /etc/apparmor.d/pasta >/dev/null <<'EOF'
# Allow pasta to create unprivileged user namespaces (for Sandkasten).

abi <abi/4.0>,
include <tunables/global>

profile pasta /usr/bin/pasta flags=(unconfined) {
  userns,
  include if exists <local/pasta>
}
EOF
sudo apparmor_parser -r /etc/apparmor.d/pasta
```

This gives pasta no more than Bubblewrap already has; prefer it to disabling
the restriction system-wide. First check that the `passt` profile does not
already cover `/usr/bin/pasta` (newer packages may):
`grep -n profile /etc/apparmor.d/usr.bin.passt`.

pasta may print `Couldn't get any nameserver address` or `Multiple default
IPv4 routes, picked first`. Both are harmless: the sandbox does not use pasta
for DNS, and the host's routing decides the actual path.

## Managing the Nix tools

The desktop's tools come from `sandbox-tools.nix` with the Nixpkgs revision
pinned in `flake.lock`, independent of Home Manager: updating either flake does
not update the other. At launch, the profile's runtime closure is mounted
read-only (only those store paths), and the profile appears as
`/opt/sandkasten-tools`, first in `PATH`. The rest of `/nix/store` and the Nix
daemon are not mounted; Nix builds inside the sandbox do not work. Run these
in the repository:

```bash
nix search nixpkgs package-name                   # Find a package for `paths` in sandbox-tools.nix
nix build .#sandbox-tools -o .sandkasten-nix-profile  # Rebuild after editing; restart sessions
nix flake update                                  # Newer versions (then rebuild; commit flake.lock)
.sandkasten-nix-profile/bin/opencode --version    # Check a version
nix path-info --recursive .#sandbox-tools         # The whole closure, with dependencies
nix-collect-garbage                               # Remove store paths no longer referenced
```

The profile link is updated only after a successful build, and protects the
current tools from garbage collection. Avoid `nix-collect-garbage -d` unless
you also want to delete old profile generations and their rollbacks.

## Security model

**Isolated:**
- **Filesystem:** system directories and the Nix tools are read-only; the rest
  of the host home is not mounted. Git metadata in the project is read-only.
  Copied config and data can change without affecting the host.
- **Namespaces:** PID, IPC, UTS, cgroup, and user namespaces are unshared
  (launch fails without user namespaces; nested ones are disabled, which some
  browsers and container tools may not like). Sandbox
  processes are killed if the launcher dies. `/dev` is Bubblewrap's minimal
  private one (null, zero, full, random, urandom, tty, pts, shm).
- **Environment:** cleared except for the variables listed above.
- **Network (default):** internet only; LAN, host localhost, and host abstract
  sockets such as X11 are unreachable (see [Private network](#private-network)).
- **Terminal:** the desktop uses `--new-session`; headless requires the kernel
  to block `TIOCSTI`.
- **Desktop:** private D-Bus; a restricted Wayland socket without privileged
  protocols (see [Restricted Wayland socket](#restricted-wayland-socket)).

**Not isolated:**
- **The project:** fully writable, so files can be changed or deleted, and
  host tools may run what the sandbox writes there (e.g. `.envrc`, Makefiles,
  editor config, a planted `.git` in a new subdirectory). Git metadata
  protection guards against accidental damage (history rewrites, deleting
  `.git`), not against deliberate attacks on host Git; Git directories
  the launch-time scan does not find stay writable. Consider
  `git config --global safe.bareRepository explicit`.
- **The internet:** anything readable in the sandbox can be exfiltrated.
  With `--host-net`, also the LAN, host localhost services, and X11 (input
  injection through Xwayland's abstract socket).
- **Credentials:** API keys are passed in, and homes contain copied Pi/OpenCode
  credentials and whatever programs save there. Give the sandbox its own API
  keys, with a spending limit where available, and a separate Pi/OpenCode login
  if possible, so a leak is limited and fixed by revoking one key or session.
  A credential-injecting proxy is not implemented.
- **GPU with `--gpu`:** render nodes expose the GPU driver, and the read-only
  `/sys/dev` and `/sys/devices` reveal hardware details (e.g. device serial
  numbers).
- **Saved homes:** programs can change the sandbox's copies (Pi, OpenCode,
  config), and the changes persist. Do not trust a home tampered with by
  hostile code; an interrupted copy can leave a partial destination. Project
  hashes are truncated to 48 bits, so collisions are possible but very unlikely.

## Similar projects

There are a ton of similar projects out there... a few:

- 
- https://github.com/microsoft/mxc
- https://github.com/bigattichouse/scratchpad
- https://docs.anthropic.com/en/docs/claude-code/devcontainer
- https://github.com/nikvdp/cco
- https://github.com/RchGrav/claudebox

There are many like it, but this one is mine.

## Future Work

See [TODOs.md](TODOs.md) for the backlog, including security hardening work.
