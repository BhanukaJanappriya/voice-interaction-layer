"""QA: the spoken voice and the avatar can never disagree about gender.

For every tier in config/voices.yaml and every language, female_natural resolves
only to female voices and male only to male voices. The gender of each name comes
from a table written here by hand, not from the yaml; a name that is not in the
table fails the test, so adding a voice means extending the table on purpose.

The avatar gender and the voice style are one value in the page (app.js
voiceStyle() is derived from avatarGender) and the server accepts exactly the two
styles the page sends.
"""

import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT

APP_JS = (REPO_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

LANGS = ("en", "si", "ta")
STYLES = ("female_natural", "male")
TIERS = ("gemini_live", "livekit_inference", "local")

# Hand written. Sources: Google speech generation docs (Gemini prebuilt voices), the Inworld voice
# names in the LiveKit Inference docs, the Microsoft neural voice names (the name carries the person).
FEMALE = {
    # Gemini prebuilt voices
    "Kore", "Aoede",
    # Inworld
    "Ashley",
    # edge-tts
    "en-US-AvaMultilingualNeural", "en-US-AriaNeural",
    "si-LK-ThiliniNeural",
    "ta-LK-SaranyaNeural", "ta-IN-PallaviNeural",
    # Google Cloud Chirp 3 HD (voicekit/voices.py, tts.py)
    "en-US-Chirp3-HD-Kore", "ta-IN-Chirp3-HD-Kore",
}
MALE = {
    "Puck", "Charon",
    "Edward",
    "en-US-AndrewMultilingualNeural", "en-US-GuyNeural",
    "si-LK-SameeraNeural",
    "ta-LK-KumarNeural", "ta-IN-ValluvarNeural",
    "en-US-Chirp3-HD-Puck", "ta-IN-Chirp3-HD-Puck",
}


def gender_of_name(name):
    """The bare voice name, whatever the tier wrote around it ("model:voice")."""
    bare = str(name).split(":")[-1]
    if bare in FEMALE:
        return "female"
    if bare in MALE:
        return "male"
    return None


def want(style):
    return "male" if style == "male" else "female"


@pytest.fixture(scope="module")
def voice_config():
    from voicekit import voice_config as module

    return module


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("language", LANGS)
@pytest.mark.parametrize("style", STYLES)
def test_every_configured_voice_has_the_gender_of_its_style(voice_config, tier, language, style):
    names = voice_config.lookup(tier, language, style)
    assert names, f"no {tier} voice for {language} {style}"
    for name in names:
        found = gender_of_name(name)
        assert found is not None, f"unknown voice {name!r} in {tier}/{language}/{style}: add it to the gender table"
        assert found == want(style), f"{tier}/{language}/{style} lists {name!r} which is {found}"


def test_the_two_styles_never_share_a_voice(voice_config):
    for tier in TIERS:
        for language in LANGS:
            female = set(voice_config.lookup(tier, language, "female_natural"))
            male = set(voice_config.lookup(tier, language, "male"))
            assert not (female & male), (tier, language, female & male)


def test_every_name_in_the_yaml_file_is_in_the_table():
    """Reads the raw file too, so a voice hidden behind a null or an unknown tier is still checked."""
    import yaml

    data = yaml.safe_load((REPO_ROOT / "config" / "voices.yaml").read_text(encoding="utf-8"))
    unknown = []
    wrong = []
    for section in ("tiers", "offline"):
        for tier, langs in (data.get(section) or {}).items():
            for language, genders in langs.items():
                if not isinstance(genders, dict):
                    continue
                for gender, names in genders.items():
                    for name in names or []:
                        if name in (None, "default") or str(name).startswith("si_LK-dilu"):
                            continue  # sapi default and the Piper model id that does not exist yet
                        found = gender_of_name(name)
                        if found is None:
                            unknown.append((tier, language, gender, name))
                        elif found != gender:
                            wrong.append((tier, language, gender, name))
    assert not unknown, f"extend the gender table: {unknown}"
    assert not wrong, f"voices under the wrong gender key: {wrong}"


def test_gender_of_agrees_with_the_hand_table(voice_config):
    for tier in TIERS:
        for language in LANGS:
            for style in STYLES:
                for name in voice_config.lookup(tier, language, style):
                    assert voice_config.gender_of(tier, language, name) == want(style)


def test_the_gemini_live_voice_the_server_picks_matches_the_style(server_module):
    for language in LANGS:
        for style in STYLES:
            picked = server_module._voice_name(language, style)
            assert gender_of_name(picked) == want(style), (language, style, picked)


def test_an_unknown_style_gets_a_female_voice_everywhere(server_module, voice_config):
    assert gender_of_name(server_module._voice_name("en", "robot")) == "female"
    for tier in TIERS:
        for name in voice_config.lookup(tier, "en", "robot"):
            assert gender_of_name(name) == "female"


def test_the_local_voice_maps_follow_the_style():
    from voicekit import voices

    for (language, style), name in voices.EDGE_VOICES.items():
        assert gender_of_name(name) == want(style), (language, style, name)
    for (language, style), (_, name) in voices.GOOGLE_VOICES.items():
        assert gender_of_name(name) == want(style), (language, style, name)


def test_the_tts_module_maps_follow_the_style():
    tts = pytest.importorskip("tts", reason="tts could not be imported here")
    for (language, style), name in tts._NEURAL_VOICE_MAP.items():
        assert gender_of_name(name) == want(style), (language, style, name)
    for (language, style), entry in tts._GOOGLE_CHIRP_VOICE_MAP.items():
        assert gender_of_name(entry[1]) == want(style), (language, style, entry)


# The page and the server share one value


def _function_body(source, name):
    start = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert start, f"{name} not found"
    depth, i = 1, start.end()
    while depth and i < len(source):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
    return source[start.end(): i - 1]


def test_the_page_derives_the_voice_style_from_the_avatar_gender():
    body = _function_body(APP_JS, "voiceStyle")
    assert re.fullmatch(r'\s*return avatarGender === "male" \? "male" : "female_natural";\s*', body), body


def test_the_page_has_one_gender_variable_and_no_second_voice_variable():
    assert len(re.findall(r"^let avatarGender\b", APP_JS, re.M)) == 1
    assert not re.search(r"^\s*(let|var|const)\s+(selectedVoice|voiceGender|selectedVoiceStyle|currentVoiceStyle)\b", APP_JS, re.M)


def test_every_voice_style_the_page_sends_comes_from_voiceStyle():
    sends = re.findall(r"voice_?[sS]tyle\s*:\s*([^,}\n]+)", APP_JS)
    assert sends, "no voice style is sent at all"
    for value in sends:
        assert value.strip() == "voiceStyle()", value


def test_the_gender_toggle_sets_the_avatar_and_the_voice_from_one_assignment():
    body = _function_body(APP_JS, "setAvatarGender")
    assert body.count("avatarGender =") == 1
    assert "avatarBridge.setGender(avatarGender)" in body
    assert "voiceStyle()" in body


def test_toggling_gender_during_a_gemini_live_call_reconnects_with_the_new_voice():
    body = _function_body(APP_JS, "setAvatarGender")
    live = body[body.index("if (liveSession)"):]
    live = live[: live.index("else if")]
    assert "stopConversation()" in live and "startConversation(" in live


def test_the_server_accepts_exactly_the_two_styles_the_page_sends(server_module):
    assert tuple(server_module._VOICE_STYLES) == STYLES
    assert server_module.DEFAULT_VOICE_STYLE == "female_natural"
    for style in STYLES:
        assert server_module._valid_voice_style(style) == style
    for bad in (None, "", "Male", "female", "robot", 1, ["male"], {"a": 1}):
        assert server_module._valid_voice_style(bad) == "female_natural"


def test_the_livekit_token_uses_the_same_two_styles():
    tokens = pytest.importorskip("livekit_transport.tokens", reason="livekit_transport not importable")
    assert tuple(tokens.VOICE_STYLES) == STYLES
    assert tokens.normalise_voice_style("male") == "male"
    assert tokens.normalise_voice_style("anything") == "female_natural"


def test_the_avatar_gender_choice_is_only_male_or_female_in_storage():
    body = _function_body(APP_JS, "loadAvatarGender")
    assert '"male" ? "male" : "female"' in body
