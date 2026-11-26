"""Run the Qt desktop and its browser bridge as one container lifecycle."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.request import urlopen


PASSWORD_FILE = Path("/run/secrets/vnc_password")
VNC_ADDRESS = ("127.0.0.1", 5900)
WEB_URL = "http://127.0.0.1:6080/"


def screen_size() -> str:
    dimensions = []
    for key, default, minimum in (
        ("SCREEN_WIDTH", "1440", 1080),
        ("SCREEN_HEIGHT", "900", 824),
    ):
        value = int(os.environ.get(key, default))
        if not minimum <= value <= 4096:
            raise ValueError(f"{key} must be between {minimum} and 4096")
        dimensions.append(str(value))
    return "x".join((*dimensions, "24"))


def read_password(path: Path) -> bytes:
    # Classic VNC authentication uses only eight bytes; reject silent truncation.
    password = path.read_bytes().rstrip(b"\r\n")
    if len(password) != 8 or any(byte < 33 or byte > 126 for byte in password):
        raise ValueError("vnc_password.txt must contain exactly 8 printable ASCII characters (no spaces)")
    return password


def check_vnc() -> None:
    with socket.create_connection(VNC_ADDRESS, timeout=2) as connection:
        with connection.makefile("rb") as stream:
            banner = stream.read(12)
        if not banner.startswith(b"RFB "):
            raise RuntimeError("VNC did not return an RFB greeting")


def check_web() -> None:
    with urlopen(WEB_URL, timeout=2) as response:
        if response.status != 200:
            raise RuntimeError("noVNC did not return HTTP 200")


def check_display() -> None:
    subprocess.run(
        ["xdpyinfo", "-display", os.environ["DISPLAY"]],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
    )


class Desktop:
    def __init__(self) -> None:
        self.processes: list[tuple[str, subprocess.Popen]] = []

    def start(self, name: str, command: list[str]) -> None:
        print(f"Starting {name}", flush=True)
        self.processes.append((name, subprocess.Popen(command)))

    def check_processes(self) -> None:
        for name, process in self.processes:
            code = process.poll()
            if code is not None:
                raise RuntimeError(f"{name} exited with status {code}")

    def wait_ready(self, check: Callable[[], None], timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while True:
            self.check_processes()
            try:
                check()
                return
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Desktop service did not become ready") from exc
                time.sleep(0.2)

    def stop(self) -> None:
        processes = [process for _, process in reversed(self.processes)]
        for process in processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        for process in processes:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> int:
    if sys.argv[1:] == ["--healthcheck"]:
        check_display()
        check_vnc()
        check_web()
        return 0

    geometry = screen_size()
    password = read_password(PASSWORD_FILE)
    Path(os.environ["XDG_CONFIG_HOME"]).mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(os.environ["XDG_RUNTIME_DIR"])
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_dir.chmod(0o700)
    # Normalize Windows CRLF before x11vnc reads the first line as a password.
    password_file = runtime_dir / "vnc-password"
    password_file.write_bytes(password + b"\n")
    password_file.chmod(0o600)

    def request_stop(_signum, _frame) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    desktop = Desktop()
    try:
        desktop.start("Xvfb", [
            "Xvfb", os.environ["DISPLAY"], "-screen", "0", geometry,
            "-nolisten", "tcp", "-ac",
        ])
        desktop.wait_ready(check_display)
        desktop.start("Openbox", ["openbox"])
        desktop.start("x11vnc", [
            "x11vnc", "-display", os.environ["DISPLAY"],
            "-listen", "127.0.0.1", "-rfbport", "5900",
            "-passwdfile", str(password_file), "-forever", "-shared", "-noxdamage",
        ])
        desktop.wait_ready(check_vnc)
        desktop.start("noVNC", [
            "/usr/bin/websockify", "--web=/usr/share/novnc/",
            "6080", "127.0.0.1:5900",
        ])
        desktop.wait_ready(check_web)
        desktop.start("Mole Respec", [sys.executable, "/app/app.py"])
        print("Desktop ready: open http://<host>:6080/", flush=True)
        while True:
            desktop.check_processes()
            time.sleep(0.5)
    finally:
        desktop.stop()


if __name__ == "__main__":
    sys.exit(main())
