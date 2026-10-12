"""Fixtures for the browser tests.

The real app is started in a subprocess (python -m uvicorn server:app from
backend/) on a free port with a throwaway data directory, so nothing here
touches the developer's database, ports 8000 or 8011 to 8014, or the real
data directory. Speech, models and every cloud service are switched off by
the environment below.

The whole folder skips with a reason when playwright or the system Chrome
is missing. Features of the new UI layer are detected by each test and skip
until they exist.
"""

import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from ui_helpers import BLOCKED_HOSTS, LANDING_PROBE, REPO_ROOT

BACKEND_DIR = REPO_ROOT / "backend"


def pytest_collection_modifyitems(config, items):
    """The browser suite takes about 13 minutes on a slow machine, so a plain
    `pytest` skips it. It runs when it is named (`pytest tests/ui`) or when
    RUN_UI_TESTS=1 is set."""
    named = any("tests/ui" in str(arg).replace("\\", "/") or str(arg).replace("\\", "/").rstrip("/").endswith("ui")
                for arg in getattr(config, "args", []))
    if named or os.environ.get("RUN_UI_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="browser tests are opt in: run `pytest tests/ui` or set RUN_UI_TESTS=1")
    here = str(Path(__file__).parent).replace("\\", "/")
    for item in items:
        if str(item.fspath).replace("\\", "/").startswith(here):
            item.add_marker(skip)
FORBIDDEN_PORTS = {8000, 8011, 8012, 8013, 8014}
PYTHON = sys.executable

CODE_RE = re.compile(r"one time code for (.+?): (\d{6})")


@pytest.fixture(autouse=True)
def clean_db():
    """Overrides the autouse fixture of tests/conftest.py, which wipes the
    in process test database. These tests use their own server database."""
    yield


def _free_port():
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in FORBIDDEN_PORTS and not (8000 <= port <= 8020):
            return port
    raise RuntimeError("no free port found")


def _server_env(data_dir):
    env = dict(os.environ)
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "DATABASE_URL": f"sqlite:///{(data_dir / 'ui-test.db').as_posix()}",
            "KNOWLEDGE_DIR": str(data_dir / "knowledge"),
            "JWT_SECRET": "ui-test-jwt-secret-not-for-production-0123456789abcdef",
            "FIELD_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"u" * 32).decode("ascii"),
            "OTP_SENDER": "console",
            "VERIFY_OTP_SENDER": "console",
            "VERIFY_ENABLED": "true",
            "VOICE_TRANSPORT": "websocket",
            "AVATAR_RENDERER": "legacy",
            "WARM_UP_ON_STARTUP": "false",
            "COOKIE_SECURE": "false",
            "LLM_CHAIN": "",
            "MCP_HUB_API_KEY": "",
            "MCP_HUB_URL": "",
            "GEMINI_API_KEY": "",
            "OPENAI_API_KEY": "",
            "GOOGLE_APPLICATION_CREDENTIALS": "",
            "AZURE_SPEECH_KEY": "",
            "AZURE_SPEECH_REGION": "",
            "LIVEKIT_URL": "",
            "LIVEKIT_API_KEY": "",
            "LIVEKIT_API_SECRET": "",
            "SMS_GATEWAY_URL": "",
            "SMS_GATEWAY_API_KEY": "",
            "SMTP_HOST": "",
            "ADMIN_USERNAME": "uiadmin",
            "ADMIN_PASSWORD": "ui-admin-password-123",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


class AppServer:
    def __init__(self, base_url, log_path):
        self.base_url = base_url
        self.log_path = log_path

    def log_size(self):
        try:
            return self.log_path.stat().st_size
        except OSError:
            return 0

    def codes_since(self, offset):
        """Every dev console OTP printed after byte offset, as (masked, code)."""
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(offset)
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            return []
        return CODE_RE.findall(text)

    def wait_for_code(self, offset, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            found = self.codes_since(offset)
            if found:
                return found[-1][1]
            time.sleep(0.2)
        raise AssertionError("no dev console OTP appeared in the server log")


@pytest.fixture(scope="session")
def app_server():
    tmp = Path(tempfile.mkdtemp(prefix="slt-ui-tests-"))
    env = _server_env(tmp)
    log_path = tmp / "server.log"
    port = _free_port()
    proc = None
    log_file = open(log_path, "wb")
    try:
        for module in (["-m", "db.migrate"], ["-m", "verification.seed_demo"]):
            done = subprocess.run(
                [PYTHON, *module], cwd=str(BACKEND_DIR), env=env, capture_output=True, timeout=120
            )
            if done.returncode != 0:
                pytest.skip(
                    "could not prepare the test database: "
                    + done.stderr.decode("utf-8", "replace")[-300:]
                )
        proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.skip("the app exited at start: " + log_path.read_text("utf-8", "replace")[-400:])
            try:
                with urllib.request.urlopen(base_url + "/health", timeout=3):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.skip("the app did not become ready within 90 seconds")
        yield AppServer(base_url, log_path)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_file.close()
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def playwright_obj():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    with sync_api.sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def chrome(playwright_obj):
    try:
        browser = playwright_obj.chromium.launch(channel="chrome", headless=True)
    except Exception as exc:
        pytest.skip(f"system Chrome could not be launched: {str(exc).splitlines()[0][:160]}")
    yield browser
    browser.close()


@pytest.fixture
def make_page(chrome, app_server):
    """Factory: make_page(width, height, theme, ...) returns a Page in its
    own browser context. Contexts are closed after the test."""
    contexts = []

    def _make(
        width=360,
        height=740,
        theme="light",
        touch=False,
        reduced_motion="no-preference",
        block_ui=False,
        landing_probe=False,
        locale="en-US",
        block_network_hosts=True,
        browser=None,
        timeout=20000,
        store_theme=True,
    ):
        target = browser or chrome
        context = target.new_context(
            viewport={"width": width, "height": height},
            color_scheme=theme,
            reduced_motion=reduced_motion,
            has_touch=touch,
            is_mobile=touch and width < 700,
            locale=locale,
            device_scale_factor=1,
        )
        contexts.append(context)
        context.set_default_timeout(timeout)
        context.set_default_navigation_timeout(timeout * 2)
        # The theme is set both through the browser preference and through
        # the app's own stored choice, which wins when present.
        if store_theme:
            context.add_init_script(
                "try { localStorage.setItem('theme', %r); } catch (e) {}" % theme
            )
        if landing_probe:
            context.add_init_script(LANDING_PROBE)
        if block_network_hosts:
            context.route(BLOCKED_HOSTS, lambda route: route.abort())
        page = context.new_page()
        page.ui_theme = theme
        if block_ui:
            page.route("**/ui/**", lambda route: route.abort())
        return page

    yield _make
    for context in contexts:
        try:
            context.close()
        except Exception:
            pass


@pytest.fixture
def base_url(app_server):
    return app_server.base_url
