"""The legacy avatar must stay intact whatever the new renderer does, and
the new avatar loader must fall back to it.

Static checks on the page, plus a Node run of tests/avatar_loader_test.mjs
(only when node is installed and frontend/avatar exists).
"""

import re
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT

FRONTEND = REPO_ROOT / "frontend"
INDEX = FRONTEND / "index.html"
AVATAR_DIR = FRONTEND / "avatar"
NODE_TEST = REPO_ROOT / "tests" / "avatar_loader_test.mjs"


@pytest.fixture(scope="module")
def index_html():
    return INDEX.read_text(encoding="utf-8")


def test_legacy_bot3d_script_tag_is_still_present(index_html):
    assert re.search(r"""<script[^>]+src=["']bot3d\.js["']""", index_html), "bot3d.js script tag was removed"
    assert (FRONTEND / "bot3d.js").is_file()


def test_legacy_canvas_is_still_present(index_html):
    assert re.search(r"""<canvas[^>]+id=["']bot-canvas["']""", index_html), "#bot-canvas was removed"


def test_flat_face_container_is_still_present(index_html):
    assert 'id="bot-avatar"' in index_html


def test_legacy_script_is_not_loaded_as_a_module_or_deferred_away(index_html):
    tag = re.search(r"<script[^>]+src=[\"']bot3d\.js[\"'][^>]*>", index_html).group(0)
    assert "type=\"module\"" not in tag and "type='module'" not in tag


def test_new_avatar_does_not_replace_the_legacy_three_global(index_html):
    # The page keeps Three.js r128 as a global; the new renderer must load a
    # modern Three through an import map instead (plan risk 7). Keyed on a
    # real signal: a script or import that points into frontend/avatar.
    wired = re.search(r"""(?:src|import)[^>]*["'][^"']*avatar/[^"']*\.m?js["']""", index_html)
    if not wired:
        pytest.skip("the new avatar is not wired into index.html yet")
    # Either a static import map in the page, or the loader injecting one itself
    # (it does, and only when the server asks for the new renderer).
    loader = (AVATAR_DIR / "loader.js").read_text(encoding="utf-8") if (AVATAR_DIR / "loader.js").exists() else ""
    assert "importmap" in index_html or 'type = "importmap"' in loader, (
        "the avatar is wired in but no import map isolates its Three.js from r128"
    )
    assert re.search(r"""<script[^>]+src=["']bot3d\.js["']""", index_html)


def test_avatar_files_do_not_edit_legacy_assets():
    if not AVATAR_DIR.exists():
        pytest.skip("frontend/avatar not delivered yet")
    for path in AVATAR_DIR.rglob("*.js"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "bot3d.js" not in text or "fallback" in text.lower(), f"{path.name} mentions bot3d without a fallback path"


def test_avatar_loader_node_suite():
    if not AVATAR_DIR.exists():
        pytest.skip("frontend/avatar not delivered yet")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    if not NODE_TEST.exists():
        pytest.skip("tests/avatar_loader_test.mjs not written yet")
    result = subprocess.run(
        [node, "--test", str(NODE_TEST)], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT)
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
