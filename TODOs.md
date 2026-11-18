# TODOs

## Near Term

- [ ] Clean up partially initialized homes when setup fails (e.g. an
  interrupted copy or a failed Nix reference check). `/tmp` is already removed;
  the home is kept and a partial destination must be deleted by hand.
- [ ] Review copied local wrappers now that Nix provides `zathura`, `meld`, and
  `vale-ls`. Remove copies that are obsolete or intentionally document why a
  wrapper should override the Nix command.
- [ ] Verify that the `julia-mono` package is discoverable by Fedora Fontconfig
  from `/opt/sandkasten-tools`; add explicit font configuration if required.

## Security Hardening

- [ ] With the private network, `kill -9` of the launcher leaves the sandbox
  running (without network): pasta's death signal does not reach its child
  (on Fedora even a plain `sleep` survives). SIGTERM and SIGHUP are handled.
  A fix (tested): run pasta inside an outer `bwrap --unshare-pid
  --die-with-parent`, whose PID namespace the kernel kills as a whole; but
  bwrap's no-new-privileges then keeps pasta out of its SELinux domain.

- [ ] Make the generated `.sandbox-etc` files inaccessible through their
  writable sandbox-home source path if immutable identity files are desired.
  They are mounted read-only at `/etc` and rewritten each launch, but remain
  writable under the sandbox home during a session.
- [ ] Consider a seccomp policy if containment of malicious code becomes a
  goal. Filesystem and namespace isolation do not filter the syscall attack
  surface by themselves.
- [ ] Consider limits for process count, CPU, memory, file size, open file
  descriptors, and writable storage. Namespaces do not prevent host resource
  exhaustion or a runaway process from filling the host filesystem.

## Validation

- [ ] Run host smoke tests for `./sandkasten3 --no-julia`, `./sandkasten3 --gpu`,
  and the default Julia-enabled software-rendered mode.
- [ ] Confirm an unrelated `/nix/store/...` path and the Nix daemon socket are
  inaccessible inside the sandbox.
- [ ] Confirm copied configuration is writable without changing host files,
  Julia/Tectonic changes stay in the sandbox, and OpenCode state persists.
- [ ] Verify `env` inside the sandbox contains only the intended variables.
- [ ] Verify host IPC objects are inaccessible.
- [ ] Verify `.git` is read-only for ordinary repositories, linked worktrees,
  and submodules.
- [ ] Verify retained homes and `/tmp/sandkasten.tmp.*` are not readable by
  other local users.

## Flatpak Support - Deferred

Flatpak is not currently supported. The launcher does not mount system or user
Flatpak installations, export Flatpak data directories, expose host portals, or
share the host D-Bus session.

Supporting Flatpak would require a separate design because nested Bubblewrap,
user namespace behavior, portal access, runtime helper sockets, and writable
Flatpak state all interact with Sandkasten's isolation model. Raw access to the
host D-Bus session would materially weaken that model and should not be added as
a quick fix.

If Flatpak support becomes necessary:

- [ ] Define which applications require it and whether Nix packages can cover
  those use cases instead.
- [ ] Reproduce current failures on the host and document them without
  hard-coded UIDs or launcher line numbers.
- [ ] Investigate a narrowly filtered D-Bus/portal bridge rather than exposing
  the complete host session bus.
- [ ] Determine the minimum required Flatpak installation and runtime state and
  whether it should be read-only, copied, or snapshotted.
- [ ] Reassess user namespace and device implications and update the security
  documentation before implementation.

References:

- [Flatpak architecture](https://docs.flatpak.org/en/latest/under-the-hood.html)
- [XDG Desktop Portals](https://flatpak.github.io/xdg-desktop-portal/)
- [Bubblewrap](https://github.com/containers/bubblewrap)
