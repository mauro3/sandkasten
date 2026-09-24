# TODOs

## Near Term

- [ ] Add an explicit way to resume a retained sandbox home. Define how its
  matching `/tmp` directory, Julia snapshots, configuration refresh, and Nix
  profile updates should behave before implementing it.
- [ ] Make project-path handling explicit. The launcher currently calls
  `realpath` before `mkdir`, so a nonexistent project argument is not created as
  its comment suggests. It also silently accepts multiple project arguments and
  uses the last one. Require exactly zero or one project directory and either
  require it to exist with a clear error or create it before resolving it.
- [ ] Correct stale launcher diagnostics: `openssl rand -hex 8` produces 16 hex
  characters, and the `cp -r` snapshot fallback does not guarantee COW despite
  the current message.
- [ ] Clean up partially initialized homes and temporary directories when setup
  fails, including failures from the copied-file `/nix/store/` check.
- [ ] Review copied local wrappers now that Nix provides `zathura`, `meld`, and
  `vale-ls`. Remove copies that are obsolete or intentionally document why a
  wrapper should override the Nix command.
- [ ] Verify that the `julia-mono` package is discoverable by Fedora Fontconfig
  from `/opt/sandkasten-tools`; add explicit font configuration if required.

## Validation

- [ ] Run host smoke tests for both `./sandkasten --no-julia` and the default
  Julia-enabled mode.
- [ ] Confirm an unrelated `/nix/store/...` path and the Nix daemon socket are
  inaccessible inside the sandbox.
- [ ] Confirm copied configuration is writable without changing host files,
  Julia/Tectonic changes remain disposable, and OpenCode state persists.

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
