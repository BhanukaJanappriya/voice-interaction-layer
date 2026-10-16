"""Translation file parity (requirement R4).

Checks, with readable failure lists:

* every key of en.json exists in si.json and ta.json, with a non empty value
  and the same {placeholders}, for the frontend and the backend files
* no key exists only in si or ta
* every literal key used in code (SltI18n.t, data-i18n attributes, the backend
  t() helper) exists in en.json
* the proposed new keys in docs/patches/D_i18n_keys.json are complete in all
  three languages, do not clash with existing keys, and the MERGED result keeps
  every rule above

tests/test_i18n.py has the older contract tests; this file adds the readable
reports, the code usage check and the merge proof. It does not repeat them.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["en", "si", "ta"]
SIDES = {"frontend": ROOT / "frontend" / "i18n", "backend": ROOT / "backend" / "i18n"}
PATCH = ROOT / "docs" / "patches" / "D_i18n_keys.json"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
NON_LATIN = re.compile(r"[඀-෿஀-௿]")
# New texts that are intentionally identical or Latin only in si and ta.
LATIN_OK = {"voice.mode.gemini_live", "voice.mode.livekit"}


def load(side, lang):
    data = json.loads((SIDES[side] / f"{lang}.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if k != "_meta"}


def problems(tables):
    """tables: {lang: {key: text}} -> list of readable problems."""
    out = []
    base = tables["en"]
    for lang in LANGS:
        table = tables[lang]
        for key in sorted(set(base) - set(table)):
            out.append(f"{lang}: missing key {key}")
        if lang != "en":
            for key in sorted(set(table) - set(base)):
                out.append(f"{lang}: key {key} has no English source")
        for key, text in sorted(table.items()):
            if not isinstance(text, str) or not text.strip():
                out.append(f"{lang}: empty value for {key}")
                continue
            if key in base and set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(base[key])):
                out.append(
                    f"{lang}: placeholders of {key} are {sorted(set(PLACEHOLDER.findall(text)))} "
                    f"but English has {sorted(set(PLACEHOLDER.findall(base[key])))}"
                )
            if "--" in text or "—" in text or "–" in text:
                out.append(f"{lang}: {key} contains a double hyphen or dash")
    return out


def assert_clean(found, what):
    assert not found, f"{what}: {len(found)} problem(s)\n  " + "\n  ".join(found[:80])


@pytest.mark.parametrize("side", list(SIDES))
def test_repo_files_are_in_parity(side):
    assert_clean(problems({lang: load(side, lang) for lang in LANGS}), f"{side} translation files")


# Literal keys used in code ------------------------------------------------

KEY = r"([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)"
FRONTEND_USES = [
    re.compile(r"""\bt\(\s*["']""" + KEY + r"""["']"""),
    re.compile(r"""data-i18n(?:-aria|-placeholder)?=["']""" + KEY + r"""["']"""),
]
BACKEND_USES = [re.compile(r"""\b(?:t|dial_text)\(\s*["']""" + KEY + r"""["']""")]


def used_keys(globs, patterns):
    found = {}
    for pattern in globs:
        for path in sorted(ROOT.glob(pattern)):
            if "vendor" in path.parts or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for regex in patterns:
                for m in regex.finditer(text):
                    found.setdefault(m.group(1), path.relative_to(ROOT).as_posix())
    return found


def test_frontend_keys_used_in_code_exist():
    en = load("frontend", "en")
    used = used_keys(["frontend/*.js", "frontend/*.html", "frontend/ui/**/*.js", "frontend/verify/*.js"], FRONTEND_USES)
    missing = [f"{key} (used in {where})" for key, where in sorted(used.items()) if key not in en]
    assert_clean(missing, "frontend keys used in code but missing from en.json")


def test_backend_keys_used_in_code_exist():
    en = load("backend", "en")
    used = used_keys(["backend/**/*.py"], BACKEND_USES)
    # Modules that carry their own BUILTIN table for these keys are fine; the
    # key must then exist in that table or in en.json.
    missing = []
    for key, where in sorted(used.items()):
        if key in en:
            continue
        table_text = (ROOT / where).read_text(encoding="utf-8", errors="replace")
        if f'"{key}"' in table_text and "BUILTIN" in table_text:
            continue
        missing.append(f"{key} (used in {where})")
    assert_clean(missing, "backend keys used in code but missing from backend/i18n/en.json")


# The proposed keys (docs/patches/D_i18n_keys.json) ------------------------

def load_patch():
    return json.loads(PATCH.read_text(encoding="utf-8"))


def test_patch_file_is_complete_in_all_languages():
    patch = load_patch()
    for side in SIDES:
        tables = patch[side]
        assert set(tables) == set(LANGS), f"{side}: languages {sorted(tables)}"
        assert_clean(problems(tables), f"patch {side} keys")


def test_patch_texts_are_translated_not_copied():
    patch = load_patch()
    found = []
    for side in SIDES:
        for key, en in patch[side]["en"].items():
            if key in LATIN_OK or not re.search(r"[A-Za-z]{3}", en):
                continue
            for lang in ("si", "ta"):
                text = patch[side][lang][key]
                if text == en or not NON_LATIN.search(text):
                    found.append(f"{side} {lang}: {key} is not translated")
    assert_clean(found, "patch translations")


@pytest.mark.parametrize("side", list(SIDES))
def test_patch_does_not_clash_with_existing_keys(side):
    patch = load_patch()
    clash = []
    for lang in LANGS:
        existing = load(side, lang)
        for key, text in patch[side][lang].items():
            if key in existing and existing[key] != text:
                clash.append(f"{lang}: {key} already exists with different text")
    # Keys the lead has already merged are allowed when the text is identical.
    assert_clean(clash, f"patch {side} versus existing files")


@pytest.mark.parametrize("side", list(SIDES))
def test_merged_result_is_in_parity(side):
    patch = load_patch()
    merged = {}
    for lang in LANGS:
        table = dict(load(side, lang))
        table.update(patch[side][lang])
        merged[lang] = table
    assert_clean(problems(merged), f"merged {side} files")


def test_every_key_the_code_changes_document_exists_in_the_patch_or_files():
    doc = (ROOT / "docs" / "patches" / "D_code_changes.md")
    if not doc.exists():
        pytest.skip("D_code_changes.md not written")
    text = doc.read_text(encoding="utf-8")
    patch = load_patch()
    known = set(load("frontend", "en")) | set(load("backend", "en")) | set(patch["frontend"]["en"]) | set(patch["backend"]["en"])
    wanted = set(re.findall(r"""\bt\(\s*["']""" + KEY + r"""["']""", text)) | set(re.findall(r"""data-i18n(?:-aria|-placeholder)?=["']""" + KEY + r"""["']""", text))
    assert_clean([f"{k} is used in D_code_changes.md but defined nowhere" for k in sorted(wanted - known)], "code change keys")
