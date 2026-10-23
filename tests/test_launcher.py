"""Process-level launch regressions. Standard library only; Windows and Linux."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bootstrap"))
from common import HTTP, atomic_json, data_directory, instance_id, read_instance
from recovery import activate, commit, recover, release_path


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in ("app", "releases/new"):
            (self.root / path).mkdir(parents=True)
            (self.root / path / "core.py").write_text("raise RuntimeError('must not import during recovery')")

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_update_rolls_back_before_import(self):
        activate(self.root, "releases/new")
        self.assertEqual(json.loads((self.root / "active-release.json").read_text())["path"], "releases/new")
        self.assertEqual(recover(self.root), self.root / "app")
        self.assertEqual(recover(self.root), self.root / "app")

    def test_committed_update_survives(self):
        activate(self.root, "releases/new")
        commit(self.root)
        self.assertEqual(recover(self.root), self.root / "releases/new")
        self.assertEqual(recover(self.root), self.root / "releases/new")

    def test_crash_between_journal_and_pointer(self):
        atomic_json(self.root / ".updates/transaction.json",
                    {"phase": "pending", "previous": "app", "candidate": "releases/new"})
        self.assertEqual(recover(self.root), self.root / "app")

    def test_bad_pointer_is_rejected(self):
        for target in ("../outside", "/tmp", "releases/../../app", "runtime", "releases/missing"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                release_path(self.root, target)

    def test_corrupt_journal_stops_instead_of_importing(self):
        (self.root / ".updates").mkdir()
        (self.root / ".updates/transaction.json").write_text("{broken")
        with self.assertRaises(ValueError):
            recover(self.root)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sakuya-tests-")
        base = Path(self.temp.name)
        self.root = base / "Диск и папка ! [тест] & ' Sakuya"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "runtime"))
        self.data = base / "Отдельные данные !"
        self.data.mkdir()
        self.env = os.environ.copy()
        self.env.update(SAKUYA_DATA_DIR=str(self.data), SAKUYA_DISABLE_TRAY="1",
                        SAKUYA_NO_BROWSER="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.processes = []

    def start(self, command=None, env=None):
        command = command or [sys.executable, "-X", "utf8", str(self.root / "bootstrap/launch.py"), "--mode", "diagnostic"]
        process = subprocess.Popen(command, cwd=self.temp.name, env=env or self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    def ready(self, process, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("Exited before ready: " + process.communicate()[0].decode("utf-8", "replace"))
            try:
                return read_instance(self.data, instance_id(self.data))
            except (OSError, ValueError, KeyError):
                time.sleep(.1)
        self.fail("Server readiness timeout")

    def shutdown(self, state):
        request = urllib.request.Request(state["url"] + "/api/shutdown", data=b"", method="POST",
                                         headers={"Authorization": "Bearer " + state["token"]})
        with HTTP.open(request, timeout=3) as response:
            self.assertEqual(response.status, 200)

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.temp.cleanup()

    def test_ready_and_graceful_exit_preserves_documents(self):
        (self.data / "documents").mkdir()
        document = self.data / "documents/Документ.txt"
        document.write_text("Сохранить меня", encoding="utf-8")
        process = self.start()
        state = self.ready(process)
        self.assertEqual(state["pid"], process.pid)
        with HTTP.open(state["url"], timeout=3) as response:
            self.assertIn("Sakuya", response.read().decode())
        self.shutdown(state)
        output = process.communicate(timeout=10)[0]
        self.assertEqual(process.returncode, 0, output.decode("utf-8", "replace"))
        self.assertEqual(document.read_text(encoding="utf-8"), "Сохранить меня")
        self.assertFalse((self.data / "run/instance.json").exists())
        self.assertTrue((self.data / "settings.json").exists())

    def test_occupied_port_uses_actual_other_port(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            atomic_json(self.data / "settings.json", {"port": port})
            process = self.start()
            state = self.ready(process)
            self.assertNotEqual(int(state["url"].rsplit(":", 1)[1]), port)
            self.shutdown(state)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0)

    def test_parallel_double_click_uses_one_instance(self):
        process = self.start()
        original = self.ready(process)
        duplicates = [self.start() for _ in range(4)]
        for duplicate in duplicates:
            output = duplicate.communicate(timeout=15)[0]
            self.assertEqual(duplicate.returncode, 0, output.decode("utf-8", "replace"))
        self.assertEqual(read_instance(self.data, instance_id(self.data))["session"], original["session"])
        self.shutdown(original)
        process.communicate(timeout=10)

    def test_crash_releases_os_lock_and_stale_state_is_replaced(self):
        first = self.start()
        original = self.ready(first)
        first.kill()
        first.communicate(timeout=10)
        self.assertTrue((self.data / "run/instance.json").exists())
        second = self.start()
        fresh = self.ready(second)
        self.assertNotEqual(fresh["session"], original["session"])
        self.assertEqual(fresh["pid"], second.pid)
        self.shutdown(fresh)
        second.communicate(timeout=10)

    def test_unauthorized_shutdown_and_wrong_origin_rejected(self):
        process = self.start()
        state = self.ready(process)
        for headers in ({}, {"Authorization": "Bearer " + state["token"], "Origin": "http://example.org"}):
            request = urllib.request.Request(state["url"] + "/api/shutdown", data=b"", headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as error:
                HTTP.open(request, timeout=3)
            self.assertEqual(error.exception.code, 403)
        self.assertIsNone(process.poll())
        self.shutdown(state)
        process.communicate(timeout=10)

    def test_wrong_pid_in_instance_file_is_rejected(self):
        process = self.start()
        state = self.ready(process)
        tampered = dict(state, pid=state["pid"] + 1)
        atomic_json(self.data / "run/instance.json", tampered)
        with self.assertRaises(ValueError):
            read_instance(self.data, instance_id(self.data))
        atomic_json(self.data / "run/instance.json", state)
        self.shutdown(state)
        process.communicate(timeout=10)

    def test_bad_settings_remain_available_for_repair(self):
        path = self.data / "settings.json"
        path.write_text("{broken", encoding="utf-8")
        process = self.start()
        output = process.communicate(timeout=10)[0]
        self.assertEqual(process.returncode, 1)
        self.assertEqual(path.read_text(), "{broken")
        self.assertIn("sakuya.log", output.decode("utf-8", "replace"))
        path.unlink()
        next_process = self.start()
        state = self.ready(next_process)
        self.shutdown(state)
        next_process.communicate(timeout=10)

    def test_pending_release_recovered_before_core_import(self):
        candidate = self.root / "releases/broken"
        candidate.mkdir(parents=True)
        (candidate / "core.py").write_text("raise RuntimeError('BROKEN RELEASE LOADED')")
        activate(self.root, "releases/broken")
        process = self.start()
        state = self.ready(process)
        self.assertEqual(json.loads((self.root / "active-release.json").read_text())["path"], "app")
        self.shutdown(state)
        process.communicate(timeout=10)

    def test_separate_data_directories_are_independent_instances(self):
        first = self.start()
        state = self.ready(first)
        other_data = Path(self.temp.name) / "OtherProject"
        env = dict(self.env, SAKUYA_DATA_DIR=str(other_data))
        second = self.start(env=env)
        deadline = time.monotonic() + 10
        while True:
            try:
                other = read_instance(other_data, instance_id(other_data))
                break
            except (OSError, ValueError, KeyError):
                if time.monotonic() >= deadline:
                    self.fail("Second independent instance not ready")
                time.sleep(.1)
        self.assertNotEqual(other["url"], state["url"])
        self.assertNotEqual(other["instance_id"], state["instance_id"])
        self.shutdown(state)
        self.shutdown(other)
        first.communicate(timeout=10)
        second.communicate(timeout=10)

    @unittest.skipUnless(os.name == "nt", "Windows BAT and PowerShell 5.1")
    def test_bat_from_unrelated_working_directory(self):
        command = f'cmd.exe /d /v:off /s /c ""{self.root / "Sakuya.bat"}" -Mode diagnostic"'
        process = self.start(command)
        state = self.ready(process, timeout=45)
        self.shutdown(state)
        output = process.communicate(timeout=20)[0]
        self.assertEqual(process.returncode, 0, output.decode("utf-8", "replace"))

    @unittest.skipUnless(os.name == "nt", "Windows Forms tray message loop")
    def test_native_tray_readiness_and_exit_fallback(self):
        env = dict(self.env, SAKUYA_DISABLE_TRAY="0")
        process = self.start(env=env)
        state = self.ready(process)
        deadline = time.monotonic() + 20
        while not (self.data / "run/tray-ready.json").exists():
            if time.monotonic() >= deadline:
                self.fail((self.data / "logs/tray.log").read_text(encoding="utf-8-sig"))
            time.sleep(.1)
        tray = json.loads((self.data / "run/tray-ready.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(tray["session"], state["session"])
        subprocess.run(["taskkill", "/PID", str(tray["pid"]), "/F"], capture_output=True, check=True)
        time.sleep(1)
        self.assertIsNone(process.poll())
        self.assertIn("Трей недоступен", (self.data / "logs/sakuya.log").read_text(encoding="utf-8"))
        self.shutdown(state)
        process.communicate(timeout=10)


class PackagingTests(unittest.TestCase):
    def test_encoding_and_single_bat(self):
        self.assertEqual(len(list(ROOT.glob("*.bat"))), 1)
        ROOT.joinpath("Sakuya.bat").read_bytes().decode("ascii")
        for path in ROOT.glob("scripts/*.ps1"):
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"), path)
        manifest = json.loads((ROOT / "config/python-runtime.json").read_text())
        self.assertEqual(set(manifest["architectures"]), {"32", "64", "arm64"})


if __name__ == "__main__":
    unittest.main()
