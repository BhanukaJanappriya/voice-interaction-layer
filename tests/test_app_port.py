"""The app runs on port 8000 and nowhere else."""

import os
import socket
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

import app_port  # noqa: E402


def test_the_port_is_8000():
    assert app_port.APP_PORT == 8000


def test_there_is_no_port_option():
    source = open(os.path.join(ROOT, "backend", "__main__.py"), encoding="utf-8").read()
    assert "--port" not in source.replace("there is no --port option", "")
    assert "port=APP_PORT" in source
    server = open(os.path.join(ROOT, "backend", "server.py"), encoding="utf-8").read()
    assert 'port=APP_PORT' in server and "port=8000" not in server


def test_a_port_option_is_rejected():
    result = subprocess.run([sys.executable, "-m", "backend", "--port", "9000"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "unrecognized arguments" in result.stderr


def test_the_start_script_passes_no_port():
    script = open(os.path.join(ROOT, "scripts", "start.ps1"), encoding="utf-8").read()
    assert "--port" not in script


def test_a_busy_port_stops_the_app_with_a_message(capsys):
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as stop:
            app_port.ensure_free("127.0.0.1", busy)
        assert stop.value.code == 1
        assert f"Port {busy} is already in use" in capsys.readouterr().err
    finally:
        holder.close()


def test_a_free_port_passes():
    free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    app_port.ensure_free("127.0.0.1", port)
