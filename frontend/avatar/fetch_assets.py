"""Download the CC0 female avatar (mpfb.glb) into frontend/avatar/assets/.

Why a script and not a commit: the file is 36.8 MB. It is fetched from the
pinned TalkingHead commit and verified against a SHA-256 recorded in
docs/THIRD_PARTY.md, so a changed or truncated file is rejected rather than
shown. The download streams into a temp file and is renamed only after the
hash matches, so a half download never looks like a valid asset.

Run:  python frontend/avatar/fetch_assets.py   (add --force to re-download)
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import requests

ASSET_DIR = Path(__file__).resolve().parent / "assets"
ASSETS = {
    "mpfb.glb": {
        "url": "https://raw.githubusercontent.com/met4citizen/TalkingHead/"
        "6318ff8c798e4a661fcd70c9373c4ab789c822bb/avatars/mpfb.glb",
        "sha256": "63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c",
    },
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str, force: bool = False) -> bool:
    spec = ASSETS[name]
    dest = ASSET_DIR / name
    if dest.exists() and not force and sha256_of(dest) == spec["sha256"]:
        print(f"ok {name} already present and verified")
        return True
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=ASSET_DIR, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with requests.get(spec["url"], stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(1 << 20):
                    handle.write(chunk)
        got = sha256_of(tmp)
        if got != spec["sha256"]:
            print(f"FAIL {name}: sha256 {got} does not match the pinned {spec['sha256']}")
            return False
        os.replace(tmp, dest)
        print(f"ok {name} downloaded and verified ({dest.stat().st_size} bytes)")
        return True
    except requests.RequestException as err:
        print(f"FAIL {name}: download error: {err}")
        return False
    finally:
        if tmp.exists():
            tmp.unlink()


def main() -> int:
    force = "--force" in sys.argv
    return 0 if all(fetch(name, force) for name in ASSETS) else 1


if __name__ == "__main__":
    sys.exit(main())
