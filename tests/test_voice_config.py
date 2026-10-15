import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from voicekit import voice_config as vc  # noqa: E402

# Hard coded from edge_tts.list_voices() on 2026-10-15 (Gender field).
EDGE_GENDER = {
    "en-US-AriaNeural": "female",
    "en-US-AvaMultilingualNeural": "female",
    "en-US-GuyNeural": "male",
    "en-US-AndrewMultilingualNeural": "male",
    "si-LK-ThiliniNeural": "female",
    "si-LK-SameeraNeural": "male",
    "ta-LK-SaranyaNeural": "female",
    "ta-IN-PallaviNeural": "female",
    "ta-LK-KumarNeural": "male",
    "ta-IN-ValluvarNeural": "male",
}
GEMINI_GENDER = {"Kore": "female", "Aoede": "female", "Puck": "male", "Charon": "male"}


@pytest.mark.parametrize("tier", vc.TIERS)
@pytest.mark.parametrize("lang", vc.LANGUAGES)
@pytest.mark.parametrize("gender", vc.GENDERS)
def test_every_tier_language_gender_resolves(tier, lang, gender):
    names = vc.lookup(tier, lang, gender)
    assert names and all(isinstance(n, str) and n for n in names)


@pytest.mark.parametrize("style,gender", [("female_natural", "female"), ("male", "male"), ("female", "female")])
def test_voice_style_maps_to_gender(style, gender):
    assert vc.lookup("local", "en", style) == vc.lookup("local", "en", gender)


def test_edge_voice_genders_match_the_service_table():
    for lang in vc.LANGUAGES:
        for gender in vc.GENDERS:
            for name in vc.lookup("local", lang, gender):
                assert EDGE_GENDER[name] == gender, name
                assert vc.gender_of("local", lang, name) == gender
                assert name.startswith({"en": "en-", "si": "si-", "ta": "ta-"}[lang])


def test_gemini_voice_genders():
    for lang in vc.LANGUAGES:
        for gender in vc.GENDERS:
            for name in vc.lookup("gemini_live", lang, gender):
                assert GEMINI_GENDER[name] == gender


def test_english_default_is_the_fast_voices():
    assert vc.lookup("local", "en", "female_natural")[0] == "en-US-AvaMultilingualNeural"
    assert vc.lookup("local", "en", "male")[0] == "en-US-AndrewMultilingualNeural"


def test_edge_map_shape():
    m = vc.edge_map()
    assert set(m) == {(l, s) for l in vc.LANGUAGES for s in ("female_natural", "male")}


def test_unknown_inputs_do_not_raise():
    assert vc.lookup("nope", "en", "male") == []
    assert vc.lookup("local", "xx", "male") == vc.lookup("local", "en", "male")
    assert vc.gender_of("local", "en", "not-a-voice") is None


def test_missing_file_uses_defaults(tmp_path):
    assert vc.lookup("local", "si", "male", path=str(tmp_path / "missing.yaml")) == ["si-LK-SameeraNeural"]


def test_garbage_file_uses_defaults(tmp_path):
    p = tmp_path / "v.yaml"
    p.write_text(":::: not [yaml", encoding="utf-8")
    assert vc.lookup("local", "ta", "female", path=str(p))[0] == "ta-LK-SaranyaNeural"


def test_partial_file_fills_missing_fields_and_drops_nulls(tmp_path):
    p = tmp_path / "v.yaml"
    p.write_text(
        "tiers:\n  local:\n    en:\n      female: [null, X-Voice, '']\n    si: {}\n  gemini_live: 5\n",
        encoding="utf-8",
    )
    assert vc.lookup("local", "en", "female", path=str(p)) == ["X-Voice"]
    assert vc.lookup("local", "en", "male", path=str(p)) == ["en-US-GuyNeural", "en-US-AndrewMultilingualNeural"]
    assert vc.lookup("local", "si", "female", path=str(p)) == ["si-LK-ThiliniNeural"]
    assert vc.lookup("gemini_live", "ta", "male", path=str(p)) == ["Puck", "Charon"]


def test_real_file_parses_and_has_expected_tiers():
    import yaml

    with open(vc.DEFAULT_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert set(data["tiers"]) == set(vc.TIERS)
    assert data["offline"]["piper_si"]["si"]["male"] == []


def test_livekit_entries_are_model_colon_voice():
    for lang in vc.LANGUAGES:
        for gender in vc.GENDERS:
            for entry in vc.lookup("livekit_inference", lang, gender):
                model, _, voice = entry.partition(":")
                assert model.split("/")[0] in ("google", "inworld") and voice
