"""Headless launcher checks; no Bubblewrap or namespaces needed."""

import contextlib
import io
import ipaddress
import json
import os
import shlex
import signal
import threading
import importlib.machinery
import importlib.util
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

LAUNCHER = Path(__file__).resolve().parents[1] / "sandkasten"
loader = importlib.machinery.SourceFileLoader("headless", str(LAUNCHER))
spec = importlib.util.spec_from_loader(loader.name, loader)
headless = importlib.util.module_from_spec(spec)
loader.exec_module(headless)
# setUp replaces host_subnets; keep the original to test it directly.
real_host_subnets = headless.host_subnets
real_check_tiocsti = headless.check_tiocsti
real_run_sandbox = headless.run_sandbox

DNS_SERVERS = [ipaddress.ip_address("192.0.2.53")]
HOST_SUBNETS = [ipaddress.ip_network("198.51.100.96/27")]

DEFAULT_CONFIG = (
    ".bashrc", ".inputrc", ".gitconfig", ".gitignore.global",
    ".config/git", ".config/helix", ".pi/agent/auth.json", ".pi/agent/settings.json",
)


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.project = self.root / "project with spaces; not shell code"
        self.project.mkdir()
        self.home = self.root / "sandbox-home"
        self.home.mkdir()
        self.enterContext(patch("pathlib.Path.home", return_value=self.root))
        self.enterContext(patch("os.getuid", return_value=1000))
        self.enterContext(patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name="tester")))
        self.enterContext(patch("shutil.which", return_value="/usr/bin/tool"))
        # Launches default to the private network; do not read this host's DNS.
        self.enterContext(patch.object(headless, "host_dns_servers", return_value=DNS_SERVERS))
        self.enterContext(patch.object(headless, "host_subnets", return_value=HOST_SUBNETS))
        # Launches check the host's TIOCSTI sysctl; tested separately below.
        self.enterContext(patch.object(headless, "check_tiocsti"))
        # Launch tests mock subprocess.run; run_sandbox is tested separately below.
        self.enterContext(patch.object(headless, "run_sandbox",
                                       lambda argv, own_process_group: subprocess.run(argv, check=False).returncode))

    def parse_args(self, argv=()):
        """Parse fixture arguments through the current command-line entry point."""
        with patch("sys.argv", [str(LAUNCHER), *argv]):
            return headless.make_parser().parse_args()

    def command(self, set_julia_path=False):
        """Return the headless argv without dry-run line markers."""
        marked = headless.build_command(
            self.project, self.home, self.root / "tmp", "tester",
            {"TERM": "screen", "UNRELATED_SECRET": "hidden", "OPENAI_API_KEY": "test-key"},
            set_julia_path,
        )
        return [argument for argument in marked if argument is not None]

    def test_optional_environment_helper(self):
        self.assertEqual(headless.setenv_opt("OPENAI_API_KEY", {"OPENAI_API_KEY": "key"}),
                         ["--setenv", "OPENAI_API_KEY", "key"])
        self.assertEqual(headless.setenv_opt("OPENAI_API_KEY", {}), [])

    def test_bind_helpers_default_target_to_source(self):
        self.assertEqual(headless.ro_bind("/usr"), ["--ro-bind", "/usr", "/usr"])
        self.assertEqual(headless.bind("/project"), ["--bind", "/project", "/project"])
        self.assertEqual(headless.ro_bind("/source", "/target"),
                         ["--ro-bind", "/source", "/target"])
        self.assertEqual(headless.bind("/source", "/target"),
                         ["--bind", "/source", "/target"])

    def test_command_has_line_markers_and_string_arguments(self):
        marked = headless.build_command(
            self.project, self.home, self.root / "tmp", "tester",
            {"OPENAI_API_KEY": "test-key"}, False,
        )
        self.assertIn(None, marked)
        self.assertTrue(all(isinstance(argument, str) for argument in self.command()))

    def test_formatter_uses_markers_and_preserves_shell_arguments(self):
        arguments = ["space in path", "'quotes'", "$HOME; echo unsafe", "\\", "line\nbreak"]
        marked = ["printf", "%s\\0", None, None, *arguments, None]
        formatted = headless.format_command(marked)
        self.assertIn(" \\\n    ", formatted)
        result = subprocess.run(["bash", "-c", formatted], check=True, capture_output=True)
        self.assertEqual(result.stdout, b"".join(value.encode() + b"\0" for value in arguments))
        self.assertEqual(headless.format_command([None, None]), "")

    def test_default_command(self):
        command = self.command()
        self.assertEqual(command[-5:], ["--chdir", str(self.project), "/bin/bash", "--noprofile", "-i"])
        self.assertIn("--clearenv", command)
        self.assertIn("--share-net", command)
        self.assertIn("--die-with-parent", command)
        self.assertNotIn("--new-session", command)
        self.assertNotIn("UNRELATED_SECRET", command)
        self.assertIn("test-key", command)
        self.assertNotIn("/home/tester/.juliaup/bin:/usr/local/bin:/usr/bin:/bin", command)
        self.assertNotIn("/nix", command)
        self.assertNotIn("WAYLAND_DISPLAY", command)

    def test_julia_path(self):
        self.assertIn("/home/tester/.juliaup/bin:/usr/local/bin:/usr/bin:/bin", self.command(set_julia_path=True))

    def test_git_directories_are_read_only_including_nested_repositories(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        nested = self.project / "nested"
        nested.mkdir()
        subprocess.run(["git", "init", "-q", str(nested)], check=True)
        binds = headless.git_binds(self.project)
        self.assertEqual(binds, [
            "--ro-bind", str(self.project / ".git"), str(self.project / ".git"),
            "--ro-bind", str(nested / ".git"), str(nested / ".git"),
        ])
        command = self.command()
        self.assertLess(command.index("--bind"), command.index(str(self.project / ".git")))

    def test_git_pointer_file_is_read_only_without_following_its_target(self):
        pointer = self.project / ".git"
        pointer.write_text("gitdir: ../metadata\n")
        self.assertEqual(headless.git_binds(self.project), [
            "--ro-bind", str(pointer), str(pointer),
        ])

    def test_symlinked_git_entries_rejected(self):
        target = self.root / "metadata"
        target.mkdir()
        pointer = self.project / ".git"
        pointer.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked Git metadata"):
            self.command()
        pointer.unlink()
        pointer.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "symlinked Git metadata"):
            self.command()

    def test_config_copy_follows_nested_links(self):
        # Home Manager links into /nix/store must become real files.
        store = self.root / "store"
        store.mkdir()
        original = store / "original"
        original.write_text("host config")
        (self.root / "config/sub").mkdir(parents=True)
        (self.root / "config/link").symlink_to(original)
        (self.root / "config/sub/real").write_text("inner")
        (self.root / "config/inner-link").symlink_to("sub/real")  # Within the copy.
        with patch.object(headless, "NIX_STORE", store):
            headless.copy_into_sandbox_home(self.root, self.home, "config")
        copied = self.home / "config/link"
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_text(), "host config")
        self.assertEqual((self.home / "config/inner-link").read_text(), "inner")
        self.assertEqual(original.read_text(), "host config")

    def test_config_copy_refuses_links_to_other_host_files(self):
        secrets = self.root / ".ssh"
        secrets.mkdir()
        (secrets / "id_ed25519").write_text("private key")
        (self.root / "config").mkdir()
        (self.root / "config/keys").symlink_to(secrets, target_is_directory=True)
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), \
                 self.assertRaisesRegex(ValueError, "config/keys -> .*\\.ssh"):
                headless.copy_into_sandbox_home(self.root, self.home, "config", dry_run=dry_run)
        self.assertFalse((self.home / "config").exists())

    def test_config_copy_refuses_link_to_an_ancestor(self):
        (self.root / "config").mkdir()
        (self.root / "config/loop").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "config/loop"):
            headless.copy_into_sandbox_home(self.root, self.home, "config")

    def test_config_source_may_link_anywhere_but_its_inner_links_are_checked(self):
        # E.g. ~/.bashrc or ~/.config/helix linking to a dotfiles checkout elsewhere.
        with tempfile.TemporaryDirectory() as directory:
            dotfiles = Path(directory)
            (dotfiles / "bashrc").write_text("alias ll='ls -l'")
            (dotfiles / "helix").mkdir()
            (dotfiles / "helix/config.toml").write_text("theme")
            (self.root / ".config").mkdir()
            (self.root / ".bashrc").symlink_to(dotfiles / "bashrc")
            (self.root / ".config/helix").symlink_to(dotfiles / "helix", target_is_directory=True)
            headless.copy_into_sandbox_home(self.root, self.home, ".bashrc")
            headless.copy_into_sandbox_home(self.root, self.home, ".config/helix")
            self.assertEqual((self.home / ".bashrc").read_text(), "alias ll='ls -l'")
            self.assertEqual((self.home / ".config/helix/config.toml").read_text(), "theme")
            # Inside the linked directory, the same rule as for any copy applies.
            (dotfiles / "git").mkdir()
            (dotfiles / "git/keys").symlink_to(self.root, target_is_directory=True)
            (self.root / ".config/git").symlink_to(dotfiles / "git", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "git/keys"):
                headless.copy_into_sandbox_home(self.root, self.home, ".config/git")

    def test_config_copy_allows_dotfiles_links_to_the_same_path(self):
        # E.g. ~/.config/git/config -> <dotfiles>/.config/git/config.
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory, "dotfiles")
            (checkout / ".config/git/hooks").mkdir(parents=True)
            (checkout / ".config/git/config").write_text("[user]")
            (checkout / ".config/git/hooks/pre-commit").write_text("exit 0")
            (self.root / ".config/git").mkdir(parents=True)
            (self.root / ".config/git/config").symlink_to(checkout / ".config/git/config")
            (self.root / ".config/git/hooks").symlink_to(checkout / ".config/git/hooks",
                                                         target_is_directory=True)
            headless.copy_into_sandbox_home(self.root, self.home, ".config/git")
            self.assertEqual((self.home / ".config/git/config").read_text(), "[user]")
            self.assertEqual((self.home / ".config/git/hooks/pre-commit").read_text(), "exit 0")
            # Inside a linked dotfiles directory, links are checked the same way.
            (checkout / ".config/git/hooks/keys").symlink_to(self.root, target_is_directory=True)
            (self.home / ".config/git").rename(self.home / "first-copy")
            with self.assertRaisesRegex(ValueError, "hooks/keys"):
                headless.copy_into_sandbox_home(self.root, self.home, ".config/git")
            # A different path in the same checkout is not a dotfiles link.
            (checkout / "keys").write_text("private key")
            (self.root / ".config/tool").mkdir()
            (self.root / ".config/tool/keys").symlink_to(checkout / "keys")
            with self.assertRaisesRegex(ValueError, "tool/keys"):
                headless.copy_into_sandbox_home(self.root, self.home, ".config/tool")

    def test_saved_copies_are_not_rechecked(self):
        (self.root / "config").mkdir()
        (self.root / "config/keys").symlink_to(self.root, target_is_directory=True)
        (self.home / "config").mkdir()
        headless.copy_into_sandbox_home(self.root, self.home, "config")  # Kept; no error.

    def test_data_copy_keeps_nested_links_and_falls_back_from_snapshot(self):
        source = self.root / ".julia"
        source.mkdir()
        (source / "data").write_text("host data")
        (source / "dangling").symlink_to("missing")
        real_run = subprocess.run

        def failed_snapshot(command, **kwargs):
            if command[0] == "btrfs":
                return subprocess.CompletedProcess(command, 1)
            return real_run(command, **kwargs)

        with patch("subprocess.run", side_effect=failed_snapshot) as run:
            headless.copy_into_sandbox_home(self.root, self.home, ".julia", keep_links=True)
        self.assertEqual(run.call_args_list[0].args[0][0], "btrfs")
        self.assertEqual((self.home / ".julia/data").read_text(), "host data")
        self.assertTrue((self.home / ".julia/dangling").is_symlink())

    def test_data_copy_skips_cp_after_successful_snapshot(self):
        (self.root / ".juliaup").mkdir()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            headless.copy_into_sandbox_home(self.root, self.home, ".juliaup", keep_links=True)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["btrfs"])

    def test_copy_rejects_destination_links_out_of_the_home(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.home / ".config").symlink_to(outside, target_is_directory=True)
        source = self.root / ".config/tool"
        source.parent.mkdir()
        source.write_text("data")
        with self.assertRaisesRegex(ValueError, "outside the sandbox home"):
            headless.copy_into_sandbox_home(self.root, self.home, ".config/tool")
        self.assertEqual(list(outside.iterdir()), [])

    def test_julia_copy_materializes_linked_startup_only_once(self):
        startup = self.root / "startup.jl"
        startup.write_text("# host startup\n")
        (self.root / ".julia/config").mkdir(parents=True)
        (self.root / ".julia/config/startup.jl").symlink_to(startup)
        with patch("shutil.which", return_value=None):
            headless.copy_julia(self.root, self.home)
        copied = self.home / ".julia/config/startup.jl"
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_text(), "# host startup\n")
        copied.write_text("# sandbox startup\n")
        headless.copy_julia(self.root, self.home)
        self.assertEqual(copied.read_text(), "# sandbox startup\n")

    def test_julia_startup_is_not_unlinked_through_linked_config(self):
        dotfiles = self.root / "dotfiles"
        dotfiles.mkdir()
        (dotfiles / "real.jl").write_text("# host startup\n")
        (dotfiles / "startup.jl").symlink_to(dotfiles / "real.jl")
        (self.root / ".julia").mkdir()
        (self.root / ".julia/config").symlink_to(dotfiles, target_is_directory=True)
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "outside the sandbox home"):
                headless.copy_julia(self.root, self.home)
        self.assertTrue((dotfiles / "startup.jl").is_symlink())

    def test_missing_optional_config_is_skipped(self):
        headless.copy_into_sandbox_home(self.root, self.home, "missing/config")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_copy_missing_policy_applies_to_dry_runs_too(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                headless.copy_into_sandbox_home(self.root, self.home, "missing", dry_run=dry_run)
                with self.assertRaisesRegex(ValueError, "Copy source is missing"):
                    headless.copy_into_sandbox_home(self.root, self.home, "missing",
                                            missing_ok=False, dry_run=dry_run)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_copy_keeps_existing_destination(self):
        source = self.root / ".bashrc"
        source.write_text("host")
        (self.home / ".bashrc").write_text("sandbox")
        headless.copy_into_sandbox_home(self.root, self.home, source)
        self.assertEqual((self.home / ".bashrc").read_text(), "sandbox")

    def test_extra_copy_preserves_home_relative_path_and_permissions(self):
        source = self.root / ".config/private file"
        source.parent.mkdir()
        source.write_text("credentials")
        source.chmod(0o600)
        headless.copy_into_sandbox_home(self.root, self.home, source, missing_ok=False)
        copied = self.home / ".config/private file"
        self.assertEqual(copied.read_text(), "credentials")
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)

    def test_extra_copy_outside_home_is_rejected(self):
        source = self.root / "external"
        source.write_text("data")
        host_home = self.root / "host-home"
        host_home.mkdir()
        for dry_run in (False, True):
            for missing_ok in (False, True):
                for path in (source, self.root / "missing"):
                    with self.subTest(dry_run=dry_run, missing_ok=missing_ok, path=path):
                        with self.assertRaisesRegex(ValueError, "inside your home directory"):
                            headless.copy_into_sandbox_home(host_home, self.home, path,
                                                    missing_ok=missing_ok, dry_run=dry_run)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_extra_copy_expands_tilde_and_dereferences_symlink(self):
        source = self.root / "original"
        source.write_text("credentials")
        (self.root / ".netrc").symlink_to(source)
        with patch.dict("os.environ", {"HOME": str(self.root)}):
            headless.copy_into_sandbox_home(self.root, self.home, "~/.netrc", missing_ok=False)
        self.assertEqual((self.home / ".netrc").read_text(), "credentials")
        self.assertFalse((self.home / ".netrc").is_symlink())

    def test_extra_copy_merges_directory_without_extra_nesting(self):
        source = self.root / "config"
        source.mkdir()
        (source / "settings").write_text("data")
        for _ in range(2):
            headless.copy_into_sandbox_home(self.root, self.home, source, missing_ok=False)
        self.assertEqual((self.home / "config/settings").read_text(), "data")
        self.assertFalse((self.home / "config/config").exists())

    def test_extra_copy_rejects_missing_recursive_and_reserved_paths(self):
        reserved = self.root / ".sandbox-etc"
        reserved.mkdir()
        for source in (self.root / "missing", self.root, self.home, reserved):
            with self.subTest(source=source), self.assertRaises(ValueError):
                headless.copy_into_sandbox_home(self.root, self.home, source, missing_ok=False)

    def test_repeatable_copy_option_is_parsed(self):
        with patch("sys.argv", [str(LAUNCHER), "-c", "first", "--copy", "second"]), \
             patch("os.getuid", return_value=0):
            # Reaching the root check proves argparse accepts both occurrences.
            with self.assertRaisesRegex(ValueError, "normal user"):
                headless.launch(headless.make_parser().parse_args())

    def test_dry_run_prints_command_without_side_effects_or_api_keys(self):
        source = self.root / ".netrc"
        source.write_text("credentials")
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [str(LAUNCHER), "--dry-run",
                "-c", str(source), str(self.project)]
        with patch("sys.argv", argv), \
             patch("os.getuid", return_value=1000), \
             patch("pwd.getpwuid") as user, \
             patch("pathlib.Path.home", return_value=self.root), \
             patch("shutil.which", return_value="/usr/bin/git"), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "secret-openai", "ANTHROPIC_API_KEY": "secret-anthropic"}), \
             patch("subprocess.run") as run, \
             patch("shutil.copy2") as copy, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            user.return_value.pw_name = "tester"
            self.assertEqual(headless.launch(headless.make_parser().parse_args()), 0)
        run.assert_not_called()
        copy.assert_not_called()
        self.assertFalse((self.root / ".cache").exists())
        # shlex reads each line continuation as a "\n" argument.
        command = [value for value in shlex.split(stdout.getvalue()) if value != "\n"]
        self.assertEqual(command[:4], ["setpriv", "--pdeathsig", "SIGKILL", "pasta"])
        self.assertIn("bwrap", command)
        self.assertIn("reject", stderr.getvalue())
        self.assertIn(str(self.project), command)
        self.assertIn("<redacted>", command)
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("secret-openai", output)
        self.assertNotIn("secret-anthropic", output)
        self.assertIn("Would copy:", stderr.getvalue())
        temp = Path(command[command.index("--bind") + 1])
        self.assertFalse(temp.exists())

    def session_args(self, **options):
        args = dict(project=str(self.project), fresh=False, tmp=False, host_net=True,
                    host_config=False, julia=False, copy=[], dry_run=False)
        args.update(options)
        return SimpleNamespace(**args)

    def test_parse_args_accepts_command_line_options(self):
        args = self.parse_args([
            "--no-julia", "--no-host-config", "--fresh", "--dry-run",
            "-c", "first", "--copy", "second", str(self.project),
        ])
        self.assertEqual(args.project, str(self.project))
        self.assertEqual(args.copy, ["first", "second"])
        self.assertFalse(args.julia or args.host_config or args.host_net)
        self.assertTrue(args.fresh and args.dry_run)
        self.assertTrue(self.parse_args(["--host-net"]).host_net)
        defaults = self.parse_args([])
        self.assertEqual(defaults.project, ".")
        self.assertEqual(defaults.copy, [])
        self.assertTrue(defaults.julia and defaults.host_config)
        self.assertFalse(defaults.fresh or defaults.dry_run)
        self.assertEqual(
            headless.files_to_copy(defaults),
            [(path, True) for path in DEFAULT_CONFIG],
        )
        self.assertEqual(headless.files_to_copy(args), [
            (Path("first").absolute(), False), (Path("second").absolute(), False),
        ])

    def test_files_to_copy_preserve_order_and_missing_policy(self):
        args = self.session_args(host_config=True, julia=True, copy=[str(self.root / ".netrc")])
        copies = headless.files_to_copy(args)
        self.assertEqual(copies[:-1], [(path, True) for path in DEFAULT_CONFIG])
        self.assertEqual(copies[-1], (self.root / ".netrc", False))
        self.assertEqual(headless.files_to_copy(self.session_args()), [])

    def test_make_sandbox_home_creates_private_home_and_identity_without_copying(self):
        home = self.root / "new-home"
        headless.make_sandbox_home(home, "tester")
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        self.assertIn("/home/tester:/bin/bash", (home / ".sandbox-etc/passwd").read_text())
        self.assertIn("tester:x:", (home / ".sandbox-etc/group").read_text())
        self.assertFalse((home / ".bashrc").exists())

    def test_nameservers_skip_loopback_scoped_and_other_lines(self):
        text = ("# comment\nsearch example.org\nnameserver 127.0.0.53\nnameserver ::1\n"
                "nameserver fe80::1%eth0\nnameserver 203.0.113.5\nnameserver 2001:db8::53\n")
        self.assertEqual([str(server) for server in headless.nameservers(text)],
                         ["203.0.113.5", "2001:db8::53"])

    def test_firewall_allows_dns_servers_then_rejects_networks(self):
        networks = headless.blocked_networks(HOST_SUBNETS)
        rules = headless.firewall_rules([ipaddress.ip_address("192.168.1.1")], networks)
        lines = [line.strip() for line in rules.splitlines()]
        self.assertIn("ip daddr 192.168.1.1 udp dport 53 accept", lines)
        self.assertIn("ip daddr 192.168.1.1 tcp dport 53 accept", lines)
        reject = lines.index("ip daddr { 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, "
                             "172.16.0.0/12, 192.168.0.0/16, 198.51.100.96/27 } reject")
        # nft applies rules in order: the DNS exceptions must come first.
        self.assertLess(lines.index("ip daddr 192.168.1.1 udp dport 53 accept"), reject)
        self.assertIn('meta nfproto ipv6 oifname != "lo" reject', lines)

    def test_blocked_networks_merge_overlapping_host_subnets(self):
        host = [ipaddress.ip_network("192.168.1.0/24"), ipaddress.ip_network("100.112.207.39/32"),
                ipaddress.ip_network("198.51.100.96/27")]
        self.assertEqual([str(network) for network in headless.blocked_networks(host)],
                         ["10.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16",
                          "172.16.0.0/12", "192.168.0.0/16", "198.51.100.96/27"])

    def test_host_subnets_read_ip_json_without_loopback(self):
        output = json.dumps([
            {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "prefixlen": 8}]},
            {"ifname": "eth0", "addr_info": [{"local": "198.51.100.124", "prefixlen": 27}]},
            {"ifname": "down0"},
        ])
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output)) as run:
            subnets = real_host_subnets()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["ip", "-j", "-4", "addr"])
        self.assertEqual(subnets, [ipaddress.ip_network("198.51.100.96/27")])

    def test_private_network_ignores_ipv6_dns_servers(self):
        servers = [ipaddress.ip_address("2001:db8::53"), ipaddress.ip_address("192.0.2.1")]
        with patch.object(headless, "host_dns_servers", return_value=servers):
            resolv_conf, rules = headless.network_files(host_net=False)
        self.assertEqual(resolv_conf, "nameserver 192.0.2.1\n")
        self.assertNotIn("2001:db8::53", rules)

    def test_private_network_needs_a_dns_server(self):
        with patch.object(headless, "host_dns_servers", return_value=[]), \
             self.assertRaisesRegex(ValueError, "--host-net"):
            headless.network_files(host_net=False)

    def test_run_sandbox_returns_status_and_sigterm_kills_the_sandbox(self):
        self.assertEqual(real_run_sandbox(["sh", "-c", "exit 3"], own_process_group=True), 3)
        # SIGTERM to the launcher kills sleep, which stands in for pasta's bwrap child.
        threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
        status = real_run_sandbox(["sh", "-c", "sleep 30; exit 0"], own_process_group=False)
        self.assertEqual(status, 0)  # sh outlives its killed child, as pasta does.
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_bwrap_pid_is_the_child_of_pasta(self):
        self.assertEqual(headless.bwrap_pid(123, ["bwrap", "--unshare-user"]), 123)
        with subprocess.Popen(["sleep", "5"]) as child:  # Stands in for pasta's bwrap child.
            try:
                self.assertEqual(headless.bwrap_pid(os.getpid(), ["setpriv", "pasta"]), child.pid)
            finally:
                child.kill()

    def test_private_network_wraps_bwrap_and_writes_network_files(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(headless.launch(self.session_args(host_net=False)), 0)
        argv = run.call_args.args[0]
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        rules = saved_home / ".sandbox-etc/firewall.nft"
        self.assertEqual(argv[:4], ["setpriv", "--pdeathsig", "SIGKILL", "pasta"])
        for option in ("--config-net", "-4", "--no-map-gw"):
            self.assertIn(option, argv)
        for option in ("-t", "-u", "-T", "-U"):
            self.assertEqual(argv[argv.index(option) + 1], "none")
        # sh loads the rules, then execs bwrap with the unchanged headless argv.
        start = argv.index("sh")
        self.assertEqual(argv[start:start + 5],
                         ["sh", "-c", 'nft -f "$1" && shift && exec "$@"', "sh", str(rules)])
        self.assertEqual(argv[start + 5:start + 8], ["env", "--default-signal", "bwrap"])
        self.assertIn("192.0.2.53 udp dport 53 accept", rules.read_text())
        self.assertEqual((saved_home / ".sandbox-etc/resolv.conf").read_text(),
                         "nameserver 192.0.2.53\n")

    def test_host_net_runs_bwrap_directly_without_firewall(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(headless.launch(self.session_args(host_net=True)), 0)
        argv = run.call_args.args[0]
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        self.assertEqual(argv[0], "bwrap")
        self.assertNotIn("pasta", argv)
        self.assertFalse((saved_home / ".sandbox-etc/firewall.nft").exists())
        self.assertTrue((saved_home / ".sandbox-etc/resolv.conf").exists())

    def test_command_keeps_real_ids_and_mounts_generated_resolv_conf(self):
        command = self.command()
        start = command.index("--unshare-user")
        self.assertEqual(command[start:start + 5],
                         ["--unshare-user", "--uid", "1000", "--gid", str(os.getgid())])
        self.assertIn(["--ro-bind", str(self.home / ".sandbox-etc/resolv.conf"), "/etc/resolv.conf"],
                      [command[i:i + 3] for i in range(len(command))])

    def test_tiocsti_check_requires_the_sysctl_to_be_zero(self):
        setting = self.root / "legacy_tiocsti"
        with patch.object(headless, "Path", side_effect=lambda path: setting):
            setting.write_text("0\n")
            real_check_tiocsti()
            setting.write_text("1\n")
            with self.assertRaisesRegex(ValueError, "sysctl dev.tty.legacy_tiocsti=0"):
                real_check_tiocsti()
            setting.unlink()
            with self.assertRaisesRegex(ValueError, "Linux 6.2"):
                real_check_tiocsti()

    def test_launch_checks_tiocsti_only_when_sharing_the_terminal(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)), \
             contextlib.redirect_stderr(io.StringIO()):
            headless.launch(self.session_args())
            headless.check_tiocsti.assert_called_once()
            headless.check_tiocsti.reset_mock()
            headless.launch(self.session_args(),
                            command_builder=lambda *args: ["bwrap", "--new-session", "true"])
            headless.check_tiocsti.assert_not_called()

    def test_session_runs_mkdir_copy_build_then_execute_in_order(self):
        trace = Mock()
        with patch.object(headless, "make_sandbox_home", wraps=headless.make_sandbox_home) as mkdir, \
             patch.object(headless, "copy_into_sandbox_home") as copy, \
             patch.object(headless, "build_command", wraps=headless.build_command) as build, \
             patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             contextlib.redirect_stderr(io.StringIO()):
            trace.attach_mock(mkdir, "mkdir")
            trace.attach_mock(copy, "copy")
            trace.attach_mock(build, "build")
            trace.attach_mock(run, "run")
            self.assertEqual(headless.launch(self.session_args(copy=[str(self.root / "extra")])), 0)
        self.assertEqual([call[0] for call in trace.mock_calls], ["mkdir", "copy", "build", "run"])

    def test_launch_holds_lock_through_callbacks_execution_and_cleanup(self):
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        remove_temp = headless.shutil.rmtree

        def check_lock(*args, **kwargs):
            with self.assertRaisesRegex(ValueError, "already in use"):
                with headless.lock_sandbox_home(saved_home):
                    self.fail("Launch released its lock too early")
            return ["bwrap"]

        def execute(command, **kwargs):
            check_lock()
            return subprocess.CompletedProcess(command, 7)

        def cleanup(temp):
            check_lock()
            remove_temp(temp)

        prepare = Mock(side_effect=check_lock)
        build = Mock(side_effect=check_lock)
        with patch("subprocess.run", side_effect=execute), \
             patch("shutil.rmtree", side_effect=cleanup), \
             contextlib.redirect_stderr(io.StringIO()):
            result = headless.launch(self.session_args(),
                                     prepare_home=prepare, command_builder=build)
        self.assertEqual(result, 7)
        prepare.assert_called_once_with(self.root, saved_home, "tester", dry_run=False)
        build.assert_called_once()
        self.assertFalse((Path("/tmp") / f"sandkasten.tmp.{saved_home.name}").exists())
        with headless.lock_sandbox_home(saved_home):
            pass  # The lock is released after cleanup, not while it runs.

    def test_prepare_temp_replaces_old_contents_and_rejects_symlinks(self):
        temp = self.root / "session-temp"
        temp.mkdir()
        (temp / "old-file").write_text("discard")
        headless.prepare_temp(temp)
        self.assertEqual(list(temp.iterdir()), [])
        self.assertEqual(temp.stat().st_mode & 0o777, 0o700)
        temp.rmdir()
        temp.symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked temporary directory"):
            headless.prepare_temp(temp)
        self.assertTrue(self.home.is_dir())

    def test_dry_run_preserves_saved_identity_and_existing_temp(self):
        with tempfile.TemporaryDirectory(prefix="sandkasten.tmp.") as directory:
            temp = Path(directory)
            saved_home = self.root / ".cache/sandkasten" / temp.name.removeprefix("sandkasten.tmp.")
            identity = saved_home / ".sandbox-etc"
            identity.mkdir(parents=True)
            (identity / "passwd").write_text("saved identity")
            (temp / "keep").write_text("saved temporary data")
            source = self.root / ".netrc"
            source.write_text("host credentials")
            args = self.session_args(dry_run=True, copy=[str(source)])
            with patch("subprocess.run") as run, \
                 patch.object(headless, "sandbox_home_name", return_value=saved_home.name), \
                 patch.object(headless, "prepare_temp") as prepare_temp, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(headless.launch(args), 0)
            run.assert_not_called()
            prepare_temp.assert_not_called()
            self.assertEqual((identity / "passwd").read_text(), "saved identity")
            self.assertFalse((identity / "group").exists())
            self.assertFalse((saved_home / ".netrc").exists())
            self.assertEqual((temp / "keep").read_text(), "saved temporary data")

    def test_failed_copy_removes_temp(self):
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        temp = Path("/tmp") / f"sandkasten.tmp.{saved_home.name}"
        with patch.object(headless, "copy_into_sandbox_home", side_effect=ValueError("setup failed")), \
             patch("subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "setup failed"):
                headless.launch(self.session_args(copy=[str(self.root / "extra")]))
        run.assert_not_called()
        self.assertTrue(saved_home.is_dir())
        self.assertFalse(temp.exists())
        with headless.lock_sandbox_home(saved_home):
            pass

    def test_home_names_are_project_specific_and_fresh_is_separate(self):
        self.assertRegex(headless.sandbox_home_name(self.project), r"^project-[0-9a-f]{12}$")
        self.assertEqual(headless.sandbox_home_name(self.project), headless.sandbox_home_name(self.project))
        self.assertNotEqual(headless.sandbox_home_name(self.project), headless.sandbox_home_name(self.root))
        self.assertNotEqual(headless.sandbox_home_name(self.project, fresh=True), headless.sandbox_home_name(self.project))
        self.assertNotEqual(headless.sandbox_home_name(self.project, fresh=True), headless.sandbox_home_name(self.project, fresh=True))

    def test_existing_8_character_home_is_kept(self):
        cache = self.root / ".cache/sandkasten"
        name = headless.sandbox_home_name(self.project)
        legacy = cache / name[:len("project-") + 8]
        legacy.mkdir(parents=True)
        self.assertEqual(headless.check_request(self.session_args())[2], legacy)
        (cache / name).mkdir()  # Once a 12-character home exists, it wins.
        self.assertEqual(headless.check_request(self.session_args())[2], cache / name)
        fresh = headless.check_request(self.session_args(fresh=True))[2]
        self.assertNotIn(fresh, (legacy, cache / name))

    def test_cache_directory_is_private(self):
        cache = self.root / ".cache/sandkasten"
        cache.mkdir(parents=True, mode=0o755)
        cache.chmod(0o755)
        with headless.lock_sandbox_home(cache / headless.sandbox_home_name(self.project)):
            pass
        self.assertEqual(cache.stat().st_mode & 0o777, 0o700)

    def test_restart_preserves_home_and_does_not_overwrite_copies(self):
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        (self.root / ".bashrc").write_text("host bashrc")
        source = self.root / ".netrc"
        source.write_text("host credentials")
        args = self.session_args(host_config=True, copy=[str(source)])
        with patch("subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            run.return_value.returncode = 0
            self.assertEqual(headless.launch(args), 0)
            (saved_home / ".bashrc").write_text("sandbox bashrc")
            (saved_home / ".netrc").write_text("sandbox credentials")
            (saved_home / "work").write_text("saved work")
            self.assertEqual(headless.launch(args), 0)
        self.assertEqual((saved_home / ".bashrc").read_text(), "sandbox bashrc")
        self.assertEqual((saved_home / ".netrc").read_text(), "sandbox credentials")
        self.assertEqual((saved_home / "work").read_text(), "saved work")
        self.assertEqual(saved_home.stat().st_mode & 0o777, 0o700)
        self.assertFalse((Path("/tmp") / f"sandkasten.tmp.{saved_home.name}").exists())

    def test_julia_path_is_enabled_by_default(self):
        args = self.parse_args([str(self.project)])
        with patch("subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            run.return_value.returncode = 0
            headless.launch(args)
        command = run.call_args.args[0]
        user = headless.pwd.getpwuid(headless.os.getuid()).pw_name
        self.assertIn(f"/home/{user}/.juliaup/bin:/usr/local/bin:/usr/bin:/bin", command)

    def test_no_julia_disables_path_but_preserves_saved_files(self):
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        juliaup = saved_home / ".juliaup"
        juliaup.mkdir(parents=True)
        (juliaup / "keep").write_text("saved Julia data")
        args = self.parse_args(["--no-julia", "--no-host-config", str(self.project)])
        with patch("subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            run.return_value.returncode = 0
            headless.launch(args)
        command = run.call_args.args[0]
        self.assertIn("/usr/local/bin:/usr/bin:/bin", command)
        self.assertFalse(any(".juliaup/bin" in argument for argument in command))
        self.assertEqual((juliaup / "keep").read_text(), "saved Julia data")

    def test_lock_prevents_concurrent_launch_and_is_released(self):
        with headless.lock_sandbox_home(self.home):
            with self.assertRaisesRegex(ValueError, "already in use"):
                with headless.lock_sandbox_home(self.home):
                    self.fail("Second launch obtained the same home lock")
        with headless.lock_sandbox_home(self.home):
            pass

    def test_identity_file_symlink_is_replaced_without_touching_target(self):
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        identity = saved_home / ".sandbox-etc"
        identity.mkdir(parents=True)
        target = self.root / "host-passwd"
        target.write_text("do not overwrite")
        (identity / "passwd").symlink_to(target)
        with patch("subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            run.return_value.returncode = 0
            headless.launch(self.session_args())
        self.assertEqual(target.read_text(), "do not overwrite")
        self.assertFalse((identity / "passwd").is_symlink())

    def test_tmp_home_is_separate_and_removed_after_the_session(self):
        cache = self.root / ".cache/sandkasten"
        homes_during_session = []

        def run(command, **kwargs):
            homes_during_session.extend(path for path in cache.iterdir() if path.is_dir())
            return subprocess.CompletedProcess(command, 0)

        with patch("subprocess.run", side_effect=run), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(headless.launch(self.session_args(tmp=True)), 0)
        # One dated home existed during the session, not the project's saved home.
        [home] = homes_during_session
        self.assertRegex(home.name, r"^\d{4}-\d{2}-\d{2}_[0-9a-f]{16}$")
        self.assertFalse(home.exists())
        self.assertFalse((Path("/tmp") / f"sandkasten.tmp.{home.name}").exists())
        self.assertEqual([path.name for path in cache.iterdir()], [home.name + ".lock"])

    def test_failed_launch_keeps_home_but_removes_temp(self):
        saved_home = self.root / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        temp = Path("/tmp") / f"sandkasten.tmp.{saved_home.name}"
        with patch("subprocess.run", side_effect=OSError("launch failed")), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(OSError):
            headless.launch(self.session_args())
        self.assertTrue(saved_home.is_dir())
        self.assertFalse(temp.exists())
        with headless.lock_sandbox_home(saved_home):
            pass

    def test_refuses_root(self):
        with patch("sys.argv", [str(LAUNCHER)]), patch("os.getuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "normal user"):
                headless.launch(headless.make_parser().parse_args())


if __name__ == "__main__":
    unittest.main()
