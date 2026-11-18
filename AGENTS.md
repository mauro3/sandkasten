# Sandkasten - Agent Context

Bubblewrap launchers for working with AI agents, meant to limit accidental
damage, not to contain malicious code. `README.md` describes the behaviour
(copy allowlists, link rules, network, Wayland, security model); keep it in
sync with changes.
The host is Fedora on x86-64 Linux; Fish, WezTerm, Helix, Git, Julia, and
Juliaup come from non-Nix host installations.

## Coding conventions (all launchers)

- KISS: favor straightforward Python that a reader rusty in Python can follow.
  Readability matters more than cleverness or minimum line count.
- Use explicit `if`/`elif`/`else` branches for related, mutually exclusive cases
  when they make the logic clearer. Do not replace them with early returns
  merely for style; an `else` after a `return` is acceptable.
- Prefer explicit argument lists and command-shaped helpers such as `ro_bind`,
  `bind`, and `setenv`. Avoid dictionaries translated into command-line options,
  generic frameworks, and unnecessary classes or modules.
- Keep one source of truth: share preparation and command building between real
  execution and dry-run output. Do not duplicate option definitions in a formatter.
  Use `nl = None` markers to specify dry-run line breaks beside the arguments;
  remove them before execution and quote arguments when rendering shell text.
- Extract small, clearly named functions for distinct tasks. Keep orchestration
  functions and `main()` easy to scan; keep argparse setup in `make_parser()`.
  Avoid splitting code merely to hide duplicated logic in another function.
- Use descriptive names that distinguish intent, e.g. `set_julia_path` for PATH
  configuration versus `args.julia` for requesting Julia copies.
- Give functions PEP 257 docstrings. Use short explanatory comments and section
  headings where they help a reader understand the flow.
- Keep dependencies to Python 3.12's standard library and the required external
  commands; prefer simple, direct implementations over additional dependencies.
- Inspect current files and Git state before editing: others may edit concurrently.
  Preserve their naming, layout, and branching style. Respect requests for review
  or explanation without edits; avoid unsolicited style-only rewrites.

## Structure

- `sandkasten` (headless) holds the shared code. `launch()` runs the whole
  session: `check_request()`, the lock, `make_sandbox_home()` and the network
  files, the `files_to_copy()` and `copy_julia()` copies, an optional
  `prepare_home`, the command builder, the TIOCSTI check (only without
  `--new-session`), the `private_network_command()` prefix (unless
  `--host-net`), then execution or dry-run output, and `/tmp` removal. Keep
  dry-run and cleanup in this one function, with the lock held through
  cleanup; do not split out another session runner. `files_to_copy()` only
  returns the list.
- `sandkasten3` (desktop) loads the extensionless `sandkasten` with importlib;
  do not add a third shared module. It passes two functions to `launch()`,
  bound with `functools.partial`: `prepare_sandbox_home` (desktop copies,
  managed files, Nix reference check) and `build_command`, which cuts off the
  known headless Bash tail and appends mounts, environment overrides, and
  WezTerm. No monkeypatching of headless globals; headless behaviour is
  unchanged without these arguments. `main()` creates the restricted Wayland
  socket (`security_context_socket()`, a small stdlib Wayland client) around
  `headless.launch()`.
- `sandkasten-cleanup` removes unlocked homes and their `/tmp` directories.
- `sandbox-tools.nix` is the Nix package allowlist (`pkgs.buildEnv`);
  `flake.lock` pins nixpkgs-unstable. `.sandkasten-nix-profile` (ignored by
  Git) must sit beside the real launcher scripts.
- `sandkasten-old-bash` is an archived feature reference, not an active launcher.

## Invariants

- Build Bubblewrap commands as argument lists and run them with `subprocess`;
  never run a rendered shell string. `--clearenv`: every variable kept in the
  sandbox is set explicitly. Informational and error output goes to stderr.
- Homes: `~/.cache/sandkasten/project-<12 hex>` (an existing 8-hex home is
  still used), or `YYYY-MM-DD_<16 hex>` with `--fresh` or `--tmp` (deleted on
  exit by `remove_sandbox_home()`, shared with cleanup); the cache and homes are
  0700. Lock files stay beside the homes, outside the mount, and are never
  unlinked. Cleanup's name pattern must match both launchers' names.
- Copies seed only missing destinations, through `copy_into_sandbox_home()`.
  Config follows links (`cp -rL`) after `check_config_links()`; data
  (`keep_links=True`) keeps inner links and tries a BTRFS snapshot. Files the
  launcher writes go through `write_config()`, which never writes through a
  link saved in the sandbox. Managed files are rewritten each launch; a newly
  copied `wezterm.lua` is patched only once; software rendering is a
  command-line override, not a config edit.
- Git: `.git` directories and pointer files are bound read-only; symlinked
  ones are refused; pointers are not followed.
- Never mount all of `/nix`, the Nix daemon, Home Manager generations, the host
  runtime directory, or live host AI state (Pi, OpenCode). No disposable-home
  cleanup prompts.
- Network: nft rejects overlapping set entries, so blocked networks are merged
  with `ipaddress.collapse_addresses`. bwrap must keep its own user namespace
  (`--unshare-user`), or the sandbox could change the firewall. No
  `--hostname` is passed (may affect `flatpak-spawn`).

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
python3 -m py_compile sandkasten sandkasten3 sandkasten-cleanup
./sandkasten --help && ./sandkasten3 --help
git diff --check
```

`.github/workflows/tests.yml` runs the first three on Python 3.12 and the
latest 3.x; keep it in step with this list. The tests mock Bubblewrap, pasta, and the compositor; namespaces, the network,
Wayland, and Nix must still be checked on the host, e.g.:

```bash
nix build .#sandbox-tools -o .sandkasten-nix-profile
./sandkasten3 --dry-run --fresh /path/to/test-project
./sandkasten /path/to/test-project
./sandkasten3 /path/to/test-project
```

Inside, check the Nix tools, Fish/WezTerm/Helix, config loading, Pi and
OpenCode persistence, Julia, internet versus LAN and host localhost, and that
unrelated `/nix/store` paths and the Nix daemon are inaccessible.
