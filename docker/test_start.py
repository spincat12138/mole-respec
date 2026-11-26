"""Tests for deployment input boundaries and child-process failure handling."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from start import Desktop, read_password, screen_size


class ConfigurationTests(unittest.TestCase):
    def test_default_screen_fits_application(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(screen_size(), "1440x900x24")

    def test_invalid_or_too_small_screen_is_rejected(self):
        for key, value in (
            ("SCREEN_WIDTH", "1000"),
            ("SCREEN_HEIGHT", "780"),
            ("SCREEN_WIDTH", "4097"),
            ("SCREEN_WIDTH", "1440;command"),
        ):
            with self.subTest(key=key, value=value):
                with mock.patch.dict(os.environ, {key: value}, clear=True):
                    with self.assertRaises(ValueError):
                        screen_size()

    def test_password_accepts_windows_and_linux_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "password"
            for suffix in (b"", b"\n", b"\r\n"):
                path.write_bytes(b"Ab12!@#$" + suffix)
                self.assertEqual(read_password(path), b"Ab12!@#$")

    def test_unsafe_passwords_fail_without_disclosing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "password"
            for password in (b"", b"short", b"long-secret", b"abc defg", b"abc\ndefg", b"abc\x00defg", "中文密码".encode()):
                with self.subTest(length=len(password)):
                    path.write_bytes(password)
                    with self.assertRaises(ValueError) as error:
                        read_password(path)
                    if password:
                        self.assertNotIn(repr(password), str(error.exception))


class LifecycleTests(unittest.TestCase):
    def test_even_successful_child_exit_ends_the_container(self):
        desktop = Desktop()
        desktop.processes = [("Qt", mock.Mock(poll=mock.Mock(return_value=0)))]
        with self.assertRaisesRegex(RuntimeError, "Qt exited with status 0"):
            desktop.check_processes()

    def test_readiness_does_not_hide_process_failure(self):
        desktop = Desktop()
        desktop.processes = [("Xvfb", mock.Mock(poll=mock.Mock(return_value=1)))]
        probe = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "Xvfb exited"):
            desktop.wait_ready(probe)
        probe.assert_not_called()

    def test_readiness_timeout_keeps_underlying_error(self):
        desktop = Desktop()
        error = ConnectionRefusedError("connection refused")
        with mock.patch("start.time.monotonic", side_effect=[0, 21]):
            with self.assertRaisesRegex(RuntimeError, "did not become ready") as caught:
                desktop.wait_ready(mock.Mock(side_effect=error))
        self.assertIs(caught.exception.__cause__, error)

    def test_shutdown_terminates_all_children_and_kills_unresponsive_child(self):
        running = mock.Mock(poll=mock.Mock(return_value=None))
        stuck = mock.Mock(poll=mock.Mock(return_value=None))
        stuck.wait.side_effect = [subprocess.TimeoutExpired("Qt", 10), 0]
        exited = mock.Mock(poll=mock.Mock(return_value=1))
        desktop = Desktop()
        desktop.processes = [("Xvfb", running), ("Openbox", exited), ("Qt", stuck)]
        desktop.stop()
        running.terminate.assert_called_once()
        stuck.terminate.assert_called_once()
        stuck.kill.assert_called_once()
        exited.terminate.assert_not_called()
        running.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
