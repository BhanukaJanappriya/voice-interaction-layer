"""Self check: python -m i18n (run from the backend folder).

Verifies every language file has the same keys, the same placeholders per
key, and no empty values."""

import json
import sys

from . import _DIR, available_languages, placeholders


def check() -> list[str]:
    problems: list[str] = []
    data = {}
    for lang in available_languages():
        data[lang] = {k: v for k, v in json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8")).items() if k != "_meta"}
    if "en" not in data:
        return ["en.json is missing"]
    base = data["en"]
    for lang, entries in data.items():
        for key in sorted(set(base) - set(entries)):
            problems.append(f"{lang}: missing key {key}")
        for key in sorted(set(entries) - set(base)):
            problems.append(f"{lang}: extra key {key}")
        for key, value in entries.items():
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{lang}: empty value for {key}")
            elif key in base and placeholders(value) != placeholders(base[key]):
                problems.append(f"{lang}: placeholders differ for {key}: {sorted(placeholders(value))} vs {sorted(placeholders(base[key]))}")
    return problems


if __name__ == "__main__":
    issues = check()
    for line in issues:
        print("FAIL", line)
    if issues:
        sys.exit(1)
    print(f"i18n OK: {', '.join(available_languages())} share {len(json.loads((_DIR / 'en.json').read_text(encoding='utf-8'))) - 1} keys")
