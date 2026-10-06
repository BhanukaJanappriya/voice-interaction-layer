"""Download the avatar renderer's third party modules into frontend/avatar/vendor/.

Why a script: the vendored files are pinned (commits and versions below), so a
reviewer can re-run this and diff the result instead of trusting a blob. Three.js
modules are fetched by following the relative and "three/addons/" imports from
TalkingHead, so only the files that are really needed are copied.

Run from anywhere:  python frontend/avatar/fetch_vendor.py
Licences (all MIT) are saved next to the code. No GLB is handled here, see
fetch_assets.py for that.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import requests

TALKINGHEAD_COMMIT = "b3e277b3b46f88e557bf28a2c5612a5b04e075c3"
HEADAUDIO_COMMIT = "d3af5f9ff86ab6b2b1913d411a4e1922ec101953"
THREE_VERSION = "0.180.0"

VENDOR = Path(__file__).resolve().parent / "vendor"
TH_RAW = f"https://raw.githubusercontent.com/met4citizen/TalkingHead/{TALKINGHEAD_COMMIT}/"
HA_RAW = f"https://raw.githubusercontent.com/met4citizen/HeadAudio/{HEADAUDIO_COMMIT}/"
THREE_CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/"

IMPORT_RE = re.compile(r"""(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|import\s*['"]([^'"]+)['"]""")


def fetch(url: str, dest: Path) -> bytes:
    """Stream to a temp file in the same folder, then rename atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(65536):
                    handle.write(chunk)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    print(f"ok {dest.relative_to(VENDOR.parent)} ({dest.stat().st_size} bytes)")
    return dest.read_bytes()


def fetch_three_closure(entries: list[str]) -> None:
    """Fetch three modules and every relative import they pull in."""
    seen: set[str] = set()
    queue = list(entries)
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        body = fetch(THREE_CDN + rel, VENDOR / "three" / rel).decode("utf-8")
        base = Path(rel).parent
        for match in IMPORT_RE.finditer(body):
            spec = match.group(1) or match.group(2)
            if spec.startswith("."):
                nxt = os.path.normpath(str(base / spec)).replace("\\", "/")
            elif spec.startswith("three/addons/"):
                nxt = "examples/jsm/" + spec[len("three/addons/"):]
            else:
                continue  # "three" itself, handled by the import map
            queue.append(nxt)


def main() -> int:
    for name in ("talkinghead", "dynamicbones", "retargeter", "lipsync-en"):
        fetch(f"{TH_RAW}modules/{name}.mjs", VENDOR / f"{name}.mjs")
    fetch(TH_RAW + "LICENSE", VENDOR / "LICENSE-TalkingHead.txt")

    for name in ("headaudio.min.mjs", "headworklet.min.mjs", "model-en-mixed.bin"):
        fetch(f"{HA_RAW}dist/{name}", VENDOR / name)
    fetch(HA_RAW + "LICENSE", VENDOR / "LICENSE-HeadAudio.txt")

    fetch(THREE_CDN + "LICENSE", VENDOR / "three" / "LICENSE-three.txt")
    fetch_three_closure(
        [
            "build/three.module.js",
            "build/three.core.js",
            "examples/jsm/controls/OrbitControls.js",
            "examples/jsm/loaders/GLTFLoader.js",
            "examples/jsm/loaders/DRACOLoader.js",
            "examples/jsm/libs/meshopt_decoder.module.js",
            "examples/jsm/loaders/FBXLoader.js",
            "examples/jsm/environments/RoomEnvironment.js",
            "examples/jsm/libs/stats.module.js",
        ]
    )
    (VENDOR / "VERSIONS.txt").write_text(
        f"TalkingHead {TALKINGHEAD_COMMIT} (MIT)\n"
        f"HeadAudio {HEADAUDIO_COMMIT} (MIT)\n"
        f"three {THREE_VERSION} (MIT)\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
