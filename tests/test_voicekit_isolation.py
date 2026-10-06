"""voicekit must stay a standalone package (ADR 2.2): it imports nothing
from the rest of the project, so it can be lifted out and reused."""

import ast

import pytest

from conftest import BACKEND_DIR

PACKAGE = BACKEND_DIR / "voicekit"
FORBIDDEN = {"config", "agent", "tts", "server", "auth", "db", "knowledge", "stt", "vad", "admin", "mcp_server", "url_safety", "mcp_client", "otp", "livekit_transport"}


@pytest.fixture(scope="module")
def sources():
    if not PACKAGE.is_dir():
        pytest.skip("backend/voicekit not delivered yet")
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, "backend/voicekit has no python files"
    return files


def _imported_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import inside the package
                continue
            yield (node.module or "").split(".")[0], node.lineno


def test_no_project_module_is_imported(sources):
    bad = [
        f"{path.relative_to(BACKEND_DIR)}:{line} imports {root}"
        for path in sources
        for root, line in _imported_roots(path)
        if root in FORBIDDEN
    ]
    assert not bad, bad


def test_no_dynamic_import_of_project_modules(sources):
    bad = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN:
            for pattern in (f'import_module("{name}', f"import_module('{name}", f'__import__("{name}', f"__import__('{name}"):
                if pattern in text:
                    bad.append(f"{path.name} uses {pattern}")
    assert not bad, bad


def test_package_imports_in_a_clean_interpreter_without_config():
    if not PACKAGE.is_dir():
        pytest.skip("backend/voicekit not delivered yet")
    if not all((PACKAGE / name).is_file() for name in ("core.py", "api.py", "__init__.py")):
        pytest.skip("voicekit core, api or __init__ not delivered yet")
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, r'%s'); import voicekit, voicekit.core, voicekit.api; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in %r and not m.startswith('voicekit')); "
        "print('LOADED', bad); sys.exit(1 if bad else 0)" % (BACKEND_DIR, sorted(FORBIDDEN))
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
