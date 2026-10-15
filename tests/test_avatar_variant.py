"""The avatar variant setting reaches the page, and the page applies the saved gender to the 3D
avatar at load. The browser side is checked by reading the source (the renderer needs WebGL)."""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_the_default_variant_is_the_original_models():
    import config

    assert config.AVATAR_VARIANT in ("original", "south_asian")
    assert "AVATAR_VARIANT=original" in read(".env.example")


def test_the_config_response_names_the_variant():
    from livekit_transport.tokens import TransportConfig

    assert "avatar_variant" in TransportConfig.model_fields


def test_the_loader_passes_the_variant_to_the_renderer():
    loader = read("frontend", "avatar", "loader.js")
    assert "avatar_variant" in loader
    assert re.search(r"r\.init\(stage, \{[^}]*variant: remembered\.variant", loader)


def test_the_renderer_falls_back_to_the_original_files():
    avatar = read("frontend", "avatar", "avatar.js")
    assert 'files: ["female_sl.glb", "mpfb.glb"]' in avatar
    assert 'files: ["male_sl.glb", "male.glb"]' in avatar
    assert 'S.variant === "original" ? [spec.file] : spec.files' in avatar
    # an LFS pointer or other plain text answer is not a model
    assert 'startsWith("text/plain")' in avatar


def test_the_saved_gender_reaches_the_avatar_at_load():
    app = read("frontend", "app.js")
    start = app.index("if (genderToggleButton) {")
    assert "avatarBridge.setGender(avatarGender)" in app[start:start + 600]


def test_the_south_asian_models_are_present_and_a_sensible_size():
    for name, limit in (("male_sl.glb", 8_000_000), ("female_sl.glb", 8_000_000)):
        path = os.path.join(ROOT, "frontend", "avatar", "assets", name)
        assert os.path.exists(path), name
        assert 100_000 < os.path.getsize(path) < limit, name
