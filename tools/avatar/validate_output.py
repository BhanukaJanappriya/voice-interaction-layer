"""Compare a built avatar GLB with its source: morph targets, joints, textures, sizes.

    venv/Scripts/python.exe tools/avatar/validate_output.py SRC.glb OUT.glb [--max-mb 8]

Checks (exit code 1 if any fails):
  * every mesh keeps the same morph target names in the same order
  * the ARKit and Oculus viseme names TalkingHead needs are present
  * the joint (bone) name set and the skin count are unchanged
  * node names, material names and alphaMode / doubleSided are unchanged
  * every image resolves to a bufferView (no missing texture)
  * the file is under the size limit
Also prints the mean sRGB colour of the body base colour texture of OUT (needs Pillow with WebP).
"""
import io
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "frontend" / "avatar" / "tools"))
from validate_glb import ARKIT, VISEMES  # noqa: E402


def load(path):
    d = Path(path).read_bytes()
    clen, _ = struct.unpack("<I4s", d[12:20])
    g = json.loads(d[20:20 + clen])
    off = 20 + clen
    blen, _ = struct.unpack("<I4s", d[off:off + 8])
    return g, d[off + 8:off + 8 + blen], len(d)


def meshes(g):
    out = {}
    for n in g["nodes"]:
        if "mesh" in n:
            m = g["meshes"][n["mesh"]]
            names = (m.get("extras") or {}).get("targetNames") or []
            out[n["name"]] = (names, len(m["primitives"][0].get("targets", [])))
    return out


def joints(g):
    s = set()
    for sk in g.get("skins", []):
        s.update(g["nodes"][j]["name"] for j in sk["joints"])
    return s


def main():
    src, out = sys.argv[1], sys.argv[2]
    limit = float(sys.argv[sys.argv.index("--max-mb") + 1]) if "--max-mb" in sys.argv else 8.0
    gs, _, ss = load(src)
    go, bo, so = load(out)
    fails = []
    print("source %s: %d bytes | output %s: %d bytes (%.2f MB)" % (Path(src).name, ss, Path(out).name, so, so / 1e6))
    ms, mo = meshes(gs), meshes(go)
    total_s = total_o = 0
    for name, (names, cnt) in ms.items():
        if name not in mo:
            fails.append("mesh node %s missing" % name)
            continue
        on, oc = mo[name]
        total_s += len(names)
        total_o += len(on)
        ok = names == on and cnt == oc
        print("  %-30s targets %3d -> %3d names identical: %s" % (name, len(names), len(on), ok))
        if not ok:
            fails.append("morph targets differ on %s" % name)
    allnames = {n for names, _ in mo.values() for n in names}
    srcnames = {n for names, _ in ms.values() for n in names}
    miss = [n for n in ARKIT + VISEMES if n in srcnames and n not in allnames]
    absent_in_src = [n for n in ARKIT + VISEMES if n not in srcnames]
    print("  total named targets %d -> %d, lost from source: %s, absent in the source as well: %s" % (
        total_s, total_o, miss, absent_in_src))
    if miss:
        fails.append("missing %s" % miss)
    js, jo = joints(gs), joints(go)
    print("  joints %d -> %d, only in source %s, only in output %s" % (len(js), len(jo), sorted(js - jo), sorted(jo - js)))
    if js != jo or len(gs.get("skins", [])) != len(go.get("skins", [])):
        fails.append("joints or skins differ")
    if sorted(n["name"] for n in gs["nodes"]) != sorted(n["name"] for n in go["nodes"]):
        fails.append("node names differ")
    for a, b in zip(gs["materials"], go["materials"]):
        if (a["name"], a.get("alphaMode"), a.get("doubleSided")) != (b["name"], b.get("alphaMode"), b.get("doubleSided")):
            fails.append("material differs: %s" % a["name"])
    print("  materials %d -> %d" % (len(gs["materials"]), len(go["materials"])))
    bad = [i for i in go["images"] if "bufferView" not in i]
    print("  images %d, without data: %d, extensions %s" % (len(go["images"]), len(bad), go.get("extensionsUsed")))
    if bad:
        fails.append("image without data")
    for t in go.get("textures", []):
        ext = (t.get("extensions") or {}).get("EXT_texture_webp", {})
        if t.get("source", ext.get("source")) is None:
            fails.append("texture without source")
    try:
        from PIL import Image
        import numpy as np
        for m in go["materials"]:
            if m["name"] == "Human.body":
                tex = go["textures"][m["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
                src_i = tex.get("source", tex.get("extensions", {}).get("EXT_texture_webp", {}).get("source"))
                bv = go["bufferViews"][go["images"][src_i]["bufferView"]]
                img = Image.open(io.BytesIO(bo[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]])).convert("RGB")
                a = np.asarray(img).reshape(-1, 3).astype(float)
                print("  body texture %s mean sRGB %s (whole image, incl. unused area)" % (img.size, a.mean(0).round(1).tolist()))
    except Exception as exc:  # optional
        print("  texture colour check skipped:", exc)
    for img in go["images"]:
        bv = go["bufferViews"][img["bufferView"]]
        print("  image %-34s %6.1f KB %s" % (img.get("name"), bv["byteLength"] / 1024, img.get("mimeType")))
    if so > limit * 1e6:
        fails.append("size %.2f MB over %.1f MB" % (so / 1e6, limit))
    print("RESULT %s %s" % ("FAIL" if fails else "PASS", fails or ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
