"""Check desktop policy and shared session lifetime without launching Bubblewrap."""

import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import ipaddress
import os
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


LAUNCHER = Path(__file__).resolve().parents[1] / "sandkasten3"
loader = importlib.machinery.SourceFileLoader("desktop", str(LAUNCHER))
spec = importlib.util.spec_from_loader(loader.name, loader)
desktop = importlib.util.module_from_spec(spec)
loader.exec_module(desktop)
headless = desktop.headless


def wayland_string(text):
    """Return a Wayland-encoded string, using the launcher's own encoder."""
    return desktop.wayland_request(0, 0, text)[8:]

class FakeCompositor:
    """A minimal Wayland server for the security-context handshake.

    It records every request and received file descriptor, offers the given
    (name, interface) globals, answers sync requests, and sends a protocol
    error for the request error_on = (object id, opcode).
    """

    def __init__(self, path, globals_, error_on=None):
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(path))
        self.server.listen()
        self.globals = globals_
        self.error_on = error_on
        self.requests = []
        self.fds = []
        self.thread = threading.Thread(target=self.serve)
        self.thread.start()

    def event(self, object_id, opcode, payload):
        return struct.pack("=II", object_id, (8 + len(payload)) << 16 | opcode) + payload

    def serve(self):
        connection, _ = self.server.accept()
        with connection:
            buffer = b""
            while True:
                try:
                    data, fds, _, _ = socket.recv_fds(connection, 65536, 8)
                except ConnectionResetError:
                    return  # The client hung up after an error.
                self.fds += fds
                if not data:
                    return  # The client is done.
                buffer += data
                while len(buffer) >= 8:
                    object_id, header = struct.unpack_from("=II", buffer)
                    size, opcode = header >> 16, header & 0xFFFF
                    if len(buffer) < size:
                        break
                    payload, buffer = buffer[8:size], buffer[size:]
                    self.requests.append((object_id, opcode, payload))
                    if (object_id, opcode) == self.error_on:
                        error = struct.pack("=II", object_id, 0) + wayland_string("refused")
                        connection.sendall(self.event(1, 0, error))
                    elif (object_id, opcode) == (1, 1):  # get_registry(new id)
                        (registry,) = struct.unpack("=I", payload)
                        for name, interface in self.globals:
                            announcement = (struct.pack("=I", name) + wayland_string(interface)
                                            + struct.pack("=I", 1))
                            connection.sendall(self.event(registry, 0, announcement))
                    elif (object_id, opcode) == (1, 0):  # sync(new id) -> done
                        (callback,) = struct.unpack("=I", payload)
                        connection.sendall(self.event(callback, 0, struct.pack("=I", 0)))

    def close(self):
        self.thread.join(5)
        self.server.close()
        for fd in self.fds:
            os.close(fd)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.host = self.root / "host"
        self.host.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.host / ".cache/sandkasten" / headless.sandbox_home_name(self.project)
        self.home.mkdir(parents=True)
        # Launches default to the private network; do not read this host's DNS.
        self.enterContext(patch.object(headless, "host_dns_servers",
                                       return_value=[ipaddress.ip_address("192.0.2.53")]))
        self.enterContext(patch.object(headless, "host_subnets", return_value=[]))
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.socket = self.runtime / "wayland-host"
        self.nix_binds = [*headless.ro_bind("/nix/store/" + "a" * 32 + "-tools", "/opt/sandkasten-tools"), None]

    def host_file(self, relative, text):
        path = self.host / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def marked_command(self, gpu=False, julia=True):
        return desktop.build_command(
            self.project, self.home, self.root / "temp", "tester", {}, julia,
            gpu=gpu, nix_binds=self.nix_binds, runtime=self.runtime, socket=self.socket,
        )

    def command(self, gpu=False, julia=True):
        return [value for value in self.marked_command(gpu, julia) if value is not None]

    def prepare(self, args, dry_run=False):
        """Run the shared copies, then the desktop preparation, as launch does."""
        with patch("shutil.which", return_value=None):
            for path, missing_ok in headless.files_to_copy(args):
                headless.copy_into_sandbox_home(self.host, self.home, path,
                                                missing_ok=missing_ok, dry_run=dry_run)
            if args.julia:
                headless.copy_julia(self.host, self.home, dry_run=dry_run)
            desktop.prepare_sandbox_home(self.host, self.home, "tester",
                                         args=args, nix_hashes=set(), dry_run=dry_run)

    def test_options_extend_headless_defaults(self):
        args = desktop.make_parser().parse_args([])
        self.assertTrue(args.julia and args.host_config)
        self.assertFalse(args.gpu or args.fresh or args.dry_run)
        args = desktop.make_parser().parse_args(["--gpu", "--no-julia", "--no-host-config"])
        self.assertTrue(args.gpu)
        self.assertFalse(args.julia or args.host_config)

    def test_command_keeps_base_protection_and_adds_private_desktop_resources(self):
        git = self.project / ".git"
        git.mkdir()
        command = self.command()
        self.assertNotIn(None, command)
        for flag in ("--unshare-user", "--disable-userns", "--share-net", "--new-session"):
            self.assertIn(flag, command)
        self.assertIn(["--ro-bind", str(git), str(git)], [command[i:i + 3] for i in range(len(command))])
        binds = [command[i + 1:i + 3] for i, value in enumerate(command) if value == "--bind"]
        self.assertEqual(len(binds), 3)  # Only /tmp, the saved home, and the project.
        self.assertNotIn(str(self.host / ".pi"), command)
        self.assertNotIn(str(self.host / ".local/share/opencode"), command)
        self.assertIn("/etc/machine-id", command)
        self.assertIn(str(self.socket), command)
        self.assertIn("LIBGL_ALWAYS_SOFTWARE", command)
        self.assertIn("front_end='Software'", command)
        self.assertNotIn("--dev-bind", command)
        self.assertIn("--always-new-process", command)
        self.assertEqual(command[-3:], ["/usr/bin/fish", "--init-command", "source ~/.config/fish/sandbox.fish"])
        env = {command[i + 1]: command[i + 2] for i, value in enumerate(command) if value == "--setenv"}
        self.assertEqual(env["SHELL"], "/usr/bin/fish")
        self.assertTrue(env["PATH"].startswith("/opt/sandkasten-tools/bin:"))
        self.assertIn("/home/tester/.juliaup/bin", env["PATH"])
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(env["XDG_RUNTIME_DIR"], str(self.runtime))

    def test_gpu_is_explicit_and_does_not_force_software_rendering(self):
        device = Path("/dev/dri/renderD128")
        with patch.object(Path, "glob", side_effect=lambda pattern: [device] if pattern == "renderD*" else []):
            command = self.command(gpu=True, julia=False)
        self.assertIn(["--dev-bind", str(device), str(device)],
                      [command[i:i + 3] for i in range(len(command))])
        for sysfs in ("/sys/dev", "/sys/devices"):  # Mesa needs these to pick the driver.
            self.assertIn(["--ro-bind", sysfs, sysfs], [command[i:i + 3] for i in range(len(command))])
        self.assertFalse(any(value.startswith("/sys") for value in self.command(gpu=False)))
        self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", command)
        self.assertNotIn("front_end='Software'", command)
        self.assertFalse(any(".juliaup/bin" in value for value in command))

    def test_dry_run_markers_render_the_same_argv(self):
        marked = self.marked_command()
        self.assertIn(None, marked)
        self.assertEqual([value for value in marked if value is not None], self.command())
        subprocess.run(["bash", "-n"], input=headless.format_command(marked), text=True, check=True)

    def test_wayland_request_encoding_pads_strings(self):
        request = desktop.wayland_request(5, 2, "abc", 7)
        self.assertEqual(request, struct.pack("=II", 5, 20 << 16 | 2)
                         + struct.pack("=I", 4) + b"abc\0" + struct.pack("=I", 7))
        self.assertEqual(desktop.wayland_string(request[8:], 0), ("abc", 8))
        self.assertEqual(len(desktop.wayland_request(1, 0, "abcd")), 8 + 4 + 8)

    def test_security_context_handshake_and_lifetime(self):
        compositor = FakeCompositor(self.socket, [(1, "wl_compositor"),
                                                  (9, "wp_security_context_manager_v1")])
        self.addCleanup(compositor.close)
        with desktop.security_context_socket(self.socket, self.runtime, "test-1") as path:
            self.assertTrue(path.is_socket())
            self.assertEqual(path.name, "wayland-0")
            self.assertEqual(path.parent.parent, self.runtime)
            compositor.thread.join(5)  # The setup connection is closed before the session.
            self.assertFalse(compositor.thread.is_alive())
            listen_fd, close_fd = compositor.fds
            # The compositor received our listening socket and the pipe's read end.
            with socket.socket(fileno=os.dup(listen_fd)) as received:
                self.assertEqual(received.getsockname(), str(path))
            os.set_blocking(close_fd, False)
            with self.assertRaises(BlockingIOError):
                os.read(close_fd, 1)  # Still open: the compositor keeps listening.
        self.assertEqual(os.read(close_fd, 1), b"")  # Closed: the compositor stops.
        self.assertFalse(path.parent.exists())
        requests = [(object_id, opcode) for object_id, opcode, _ in compositor.requests]
        self.assertEqual(requests, [(1, 1), (1, 0), (2, 0), (4, 1),
                                    (5, 1), (5, 2), (5, 3), (5, 4), (1, 0)])
        payloads = [payload for _, _, payload in compositor.requests]
        self.assertEqual(payloads[2], struct.pack("=I", 9) + wayland_string("wp_security_context_manager_v1")
                         + struct.pack("=II", 1, 4))
        self.assertEqual(payloads[3], struct.pack("=I", 5))
        self.assertEqual(payloads[4:7], [wayland_string("sandkasten"), wayland_string("sandkasten3"),
                                         wayland_string("test-1")])

    def test_security_context_requires_compositor_support(self):
        compositor = FakeCompositor(self.socket, [(1, "wl_compositor")])
        self.addCleanup(compositor.close)
        with self.assertRaisesRegex(ValueError, "--full-wayland"):
            with desktop.security_context_socket(self.socket, self.runtime, "test-2"):
                self.fail("No socket without wp_security_context_v1")
        self.assertEqual(list(self.runtime.glob("sandkasten-wayland-*")), [])

    def test_security_context_reports_protocol_errors(self):
        compositor = FakeCompositor(self.socket, [(9, "wp_security_context_manager_v1")],
                                    error_on=(5, 4))
        self.addCleanup(compositor.close)
        with self.assertRaisesRegex(ValueError, "protocol error: refused"):
            with desktop.security_context_socket(self.socket, self.runtime, "test-3"):
                self.fail("No socket after a protocol error")
        self.assertEqual(list(self.runtime.glob("sandkasten-wayland-*")), [])

    def test_wayland_validation_supports_relative_and_absolute_displays(self):
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(self.socket))
            for display in (self.socket.name, str(self.socket)):
                result = desktop.wayland_socket({"XDG_RUNTIME_DIR": str(self.runtime), "WAYLAND_DISPLAY": display})
                self.assertEqual(result, (self.runtime, self.socket))
        with self.assertRaisesRegex(ValueError, "active Wayland"):
            desktop.wayland_socket({})
        self.socket.unlink()
        self.socket.write_text("not a socket")
        with self.assertRaisesRegex(ValueError, "Wayland socket"):
            desktop.wayland_socket({"XDG_RUNTIME_DIR": str(self.runtime), "WAYLAND_DISPLAY": self.socket.name})

    def test_nix_closure_is_explicit_and_validated(self):
        (self.root / ".sandkasten-nix-profile").mkdir()
        store = Path("/nix/store/" + "a" * 32 + "-tools")
        dependency = Path("/nix/store/" + "b" * 32 + "-dependency")
        result = SimpleNamespace(stdout=f"{store}\n{dependency}\n")
        with patch.object(Path, "resolve", return_value=store), \
             patch("shutil.which", return_value="/usr/bin/nix-store"), \
             patch("subprocess.run", return_value=result) as run:
            binds, hashes = desktop.nix_tools(self.root)
        self.assertEqual(hashes, {"a" * 32, "b" * 32})
        self.assertIn(str(dependency), binds)
        self.assertIn("/opt/sandkasten-tools", binds)
        self.assertNotIn("/nix/var/nix/daemon-socket", binds)
        self.assertTrue(run.call_args.kwargs["check"])
        result.stdout += "/etc\n"
        with patch.object(Path, "resolve", return_value=store), \
             patch("shutil.which", return_value="/usr/bin/nix-store"), \
             patch("subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "Invalid Nix closure path"):
                desktop.nix_tools(self.root)

    def test_missing_nix_profile_has_build_instructions(self):
        with self.assertRaisesRegex(ValueError, "Build it with: nix build"):
            desktop.nix_tools(self.root)

    def test_managed_config_does_not_save_api_keys_or_accumulate_renderer_settings(self):
        self.host_file(".config/wezterm/wezterm.lua", "local config = {}\nreturn config\n")
        args = desktop.make_parser().parse_args(["--no-julia"])
        with patch.dict("os.environ", {"OPENAI_API_KEY": "do-not-save", "ANTHROPIC_API_KEY": "also-secret"}):
            self.prepare(args)
            self.prepare(args)
        fish = (self.home / ".config/fish/sandbox.fish").read_text()
        self.assertNotIn("do-not-save", fish)
        self.assertNotIn("also-secret", fish)
        self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", fish)
        self.assertNotIn("front_end", (self.home / ".config/wezterm/wezterm.lua").read_text())

    def test_desktop_seeding_and_restart_preserve_independent_state(self):
        self.host_file(".config/fish/config.fish", "# host fish\n")
        self.host_file(".config/wezterm/wezterm.lua", "local config = {}\n-- config.use_fancy_tab_bar = false\nreturn config\n")
        self.host_file(".pi/agent/auth.json", "host auth")
        self.host_file(".pi/agent/sessions/session", "host session")
        self.host_file(".local/share/opencode/state", "host opencode")
        self.host_file(".cache/Tectonic/cache", "host Tectonic")
        self.host_file("julia/dot-julia-dev/package", "host package")
        startup = self.host_file("startup.jl", "# Julia startup\n")
        (self.host / ".julia/config").mkdir(parents=True)
        (self.host / ".julia/config/startup.jl").symlink_to(startup)
        args = desktop.make_parser().parse_args([])
        self.prepare(args)
        self.assertIn("\nconfig.use_fancy_tab_bar = false\n", (self.home / ".config/wezterm/wezterm.lua").read_text())
        self.assertFalse((self.home / ".julia/config/startup.jl").is_symlink())
        self.assertEqual((self.home / ".julia/config/startup.jl").read_text(), "# Julia startup\n")
        machine_id = (self.home / ".sandbox-etc/machine-id").read_text()
        self.assertRegex(machine_id, r"^[0-9a-f]{32}\n$")
        self.assertIn("/usr/bin/fish", (self.home / ".sandbox-etc/passwd").read_text())
        (self.home / ".pi/agent/sessions/session").write_text("sandbox session")
        (self.home / ".local/share/opencode/state").write_text("sandbox opencode")
        (self.home / ".config/wezterm/wezterm.lua").write_text("-- sandbox config\nreturn {}\n")
        (self.home / ".julia/config/startup.jl").write_text("# sandbox startup\n")
        self.prepare(args)
        self.assertEqual((self.home / ".sandbox-etc/machine-id").read_text(), machine_id)
        self.assertEqual((self.home / ".pi/agent/sessions/session").read_text(), "sandbox session")
        self.assertEqual((self.home / ".local/share/opencode/state").read_text(), "sandbox opencode")
        self.assertEqual((self.home / ".config/wezterm/wezterm.lua").read_text(), "-- sandbox config\nreturn {}\n")
        self.assertEqual((self.home / ".julia/config/startup.jl").read_text(), "# sandbox startup\n")
        self.assertEqual((self.host / ".pi/agent/sessions/session").read_text(), "host session")
        self.assertEqual((self.host / ".local/share/opencode/state").read_text(), "host opencode")
        self.assertEqual((self.home / ".cache/Tectonic/cache").read_text(), "host Tectonic")
        self.assertEqual((self.home / "julia/dot-julia-dev/package").read_text(), "host package")
        self.assertEqual((self.home / ".config/fish/config.fish").read_text(), "# host fish\n")

    def test_home_manager_config_links_become_files_and_pass_nix_check(self):
        # Home Manager files are links into a store path that is not mounted.
        store = self.root / ("nix-store-" + "c" * 32)
        store.mkdir()
        (store / "config").write_text("[user]\n")
        for relative in (".config/git/config", ".config/helix/config.toml", ".config/vale/.vale.ini"):
            link = self.host / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(store / "config")
        args = desktop.make_parser().parse_args(["--no-julia"])
        with patch.object(desktop, "check_nix_references", wraps=desktop.check_nix_references) as check, \
             patch.object(headless, "NIX_STORE", store):
            self.prepare(args)
        check.assert_called_once()
        for relative in (".config/git/config", ".config/helix/config.toml", ".config/vale/.vale.ini"):
            self.assertFalse((self.home / relative).is_symlink())
            self.assertEqual((self.home / relative).read_text(), "[user]\n")

    def test_existing_headless_home_gets_missing_ai_state(self):
        self.host_file(".pi/agent/auth.json", "host auth")
        self.host_file(".pi/agent/sessions/session", "host session")
        self.host_file(".pi/agent/extensions/extension", "host extension")
        (self.home / ".pi/agent").mkdir(parents=True)
        (self.home / ".pi/agent/auth.json").write_text("saved auth")
        self.prepare(desktop.make_parser().parse_args(["--no-julia"]))
        self.assertEqual((self.home / ".pi/agent/auth.json").read_text(), "saved auth")
        self.assertEqual((self.home / ".pi/agent/sessions/session").read_text(), "host session")
        self.assertEqual((self.home / ".pi/agent/extensions/extension").read_text(), "host extension")

    def test_opt_out_skips_host_config_and_julia_but_keeps_managed_settings(self):
        self.host_file(".pi/agent/auth.json", "host auth")
        self.host_file(".julia/data", "host Julia")
        self.prepare(desktop.make_parser().parse_args(["--no-host-config", "--no-julia"]))
        self.assertFalse((self.home / ".pi").exists())
        self.assertFalse((self.home / ".julia").exists())
        self.assertTrue((self.home / ".config/fish/sandbox.fish").is_file())

    def test_nix_reference_check_includes_config_links_but_not_histories_or_artifacts(self):
        config = self.home / ".config/tool"
        config.parent.mkdir()
        config.write_text("/nix/store/" + "a" * 32 + "-allowed/bin/tool")
        for relative in (".pi/agent/sessions/history", ".julia/artifacts/data"):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("/nix/store/" + "b" * 32 + "-not-config")
        desktop.check_nix_references(self.home, {"a" * 32})
        link = self.home / ".config/link"
        link.symlink_to("/nix/store/" + "c" * 32 + "-unmounted")
        with self.assertRaisesRegex(ValueError, "Configuration refers to unmounted"):
            desktop.check_nix_references(self.home, {"a" * 32})
        desktop.check_nix_references(self.home, {"a" * 32, "c" * 32})

    def test_managed_writes_do_not_follow_output_links(self):
        target = self.root / "host-file"
        target.write_text("do not modify")
        (self.home / "config").symlink_to(target)
        headless.write_config(self.home, "config", "sandbox config")
        self.assertEqual(target.read_text(), "do not modify")
        self.assertFalse((self.home / "config").is_symlink())
        (self.home / "outside").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside the sandbox"):
            headless.write_config(self.home, "outside/host-file", "bad")
        self.assertEqual(target.read_text(), "do not modify")

    def test_main_dry_run_has_no_mutations_and_redacts_api_keys(self):
        self.host_file(".pi/agent/auth.json", "host auth")
        self.home.rmdir()
        stdout = io.StringIO()
        with patch("sys.argv", [str(LAUNCHER), "--dry-run", str(self.project)]), \
             patch.object(Path, "home", return_value=self.host), \
             patch("os.getuid", return_value=1000), \
             patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name="tester")), \
             patch("shutil.which", return_value="/usr/bin/mock"), \
             patch.object(desktop, "nix_tools", return_value=(self.nix_binds, set())), \
             patch.object(desktop, "wayland_socket", return_value=(self.runtime, self.socket)), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "secret-key", "ANTHROPIC_API_KEY": "other-secret"}), \
             patch("subprocess.run") as run, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(desktop.main(), 0)
        run.assert_not_called()
        self.assertFalse(self.home.exists())
        self.assertFalse(self.home.with_name(self.home.name + ".lock").exists())
        self.assertIn("<redacted>", stdout.getvalue())
        self.assertNotIn("secret-key", stdout.getvalue())
        self.assertNotIn("other-secret", stdout.getvalue())
        self.assertIn("--always-new-process", stdout.getvalue())
        # Dry-run creates no security context; it shows the planned socket.
        self.assertIn("sandkasten-wayland-XXXXXXXX/wayland-0", stdout.getvalue())
        self.assertEqual(list(self.runtime.glob("sandkasten-wayland-*")), [])
        self.assertEqual((self.host / ".pi/agent/auth.json").read_text(), "host auth")

    def test_shared_runner_holds_lock_until_desktop_exit_then_removes_temp(self):
        self.host_file(".pi/agent/auth.json", "host auth")
        args = desktop.make_parser().parse_args(["--no-julia", "--host-net", str(self.project)])
        real_popen = subprocess.Popen
        temp = Path("/tmp") / f"sandkasten.tmp.{self.home.name}"

        test = self

        class Sandbox:
            """Fake bwrap: checks the lock and /tmp, then exits after a Ctrl-C."""

            pid = 4242

            def __init__(self, command, **kwargs):
                test.assertEqual(kwargs, {"process_group": 0})
                test.assertIn("--always-new-process", command)
                self.interrupted = False

            def wait(self):
                test.assertTrue(temp.is_dir())
                lock_path = test.home.with_name(test.home.name + ".lock")
                with lock_path.open("a") as lock:
                    with test.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if not self.interrupted:
                    self.interrupted = True
                    raise KeyboardInterrupt  # The launcher must keep waiting.
                return 7

        def popen(command, **kwargs):
            if command[0] != "bwrap":
                return real_popen(command, **kwargs)  # cp and other host commands
            return Sandbox(command, **kwargs)

        with patch.object(Path, "home", return_value=self.host), \
             patch("os.getuid", return_value=1000), \
             patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name="tester")), \
             patch("shutil.which", side_effect=lambda name: None if name == "btrfs" else "/usr/bin/mock"), \
             patch("subprocess.Popen", side_effect=popen), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = headless.launch(
                args,
                prepare_home=partial(desktop.prepare_sandbox_home, args=args, nix_hashes=set()),
                command_builder=partial(desktop.build_command, gpu=False, nix_binds=self.nix_binds,
                                        runtime=self.runtime, socket=self.socket),
            )
        self.assertEqual(result, 7)
        self.assertIn("kill -9 4242", stderr.getvalue())
        self.assertFalse(temp.exists())
        self.assertTrue(self.home.is_dir())
        self.assertEqual((self.home / ".pi/agent/auth.json").read_text(), "host auth")
        with headless.lock_sandbox_home(self.home):
            pass


if __name__ == "__main__":
    unittest.main()
