"""Pure Python GLB inspector: morph targets, joints, materials, sizes.

Usage: python validate_glb.py male.glb [reference.glb]
"""
import json
import struct
import sys

ARKIT = ("eyeBlinkLeft eyeBlinkRight eyeLookDownLeft eyeLookDownRight eyeLookInLeft eyeLookInRight "
         "eyeLookOutLeft eyeLookOutRight eyeLookUpLeft eyeLookUpRight eyeSquintLeft eyeSquintRight "
         "eyeWideLeft eyeWideRight jawForward jawLeft jawRight jawOpen mouthClose mouthFunnel "
         "mouthPucker mouthLeft mouthRight mouthSmileLeft mouthSmileRight mouthFrownLeft "
         "mouthFrownRight mouthDimpleLeft mouthDimpleRight mouthStretchLeft mouthStretchRight "
         "mouthRollLower mouthRollUpper mouthShrugLower mouthShrugUpper mouthPressLeft "
         "mouthPressRight mouthLowerDownLeft mouthLowerDownRight mouthUpperUpLeft mouthUpperUpRight "
         "browDownLeft browDownRight browInnerUp browOuterUpLeft browOuterUpRight cheekPuff "
         "cheekSquintLeft cheekSquintRight noseSneerLeft noseSneerRight tongueOut").split()
VISEMES = ("viseme_sil viseme_PP viseme_FF viseme_TH viseme_DD viseme_kk viseme_CH viseme_SS "
           "viseme_nn viseme_RR viseme_aa viseme_E viseme_I viseme_O viseme_U").split()


def load(path):
    with open(path, "rb") as fh:
        magic, ver, length = struct.unpack("<4sII", fh.read(12))
        assert magic == b"glTF", "not a GLB"
        clen, ctype = struct.unpack("<I4s", fh.read(8))
        return json.loads(fh.read(clen)), length


def info(path):
    g, length = load(path)
    nodes = g.get("nodes", [])
    joints = set()
    for s in g.get("skins", []):
        joints.update(nodes[j]["name"] for j in s["joints"])
    meshes = {}
    for n in nodes:
        if "mesh" in n:
            m = g["meshes"][n["mesh"]]
            names = (m.get("extras") or {}).get("targetNames") or []
            if not names:
                for p in m["primitives"]:
                    names = (p.get("extras") or {}).get("targetNames") or names
            meshes[n["name"]] = names
    root = [n["name"] for n in nodes if n["name"] == "Armature"]
    return {"size": length, "joints": joints, "meshes": meshes, "root": root,
            "ext": g.get("extensionsUsed", []), "materials": len(g.get("materials", [])),
            "images": len(g.get("images", []))}


def report(path):
    i = info(path)
    print("== %s: %d bytes, root Armature: %s, joints %d, materials %d, images %d, extensions %s" % (
        path, i["size"], bool(i["root"]), len(i["joints"]), i["materials"], i["images"], i["ext"]))
    for name, tn in i["meshes"].items():
        print("  mesh %-22s %d targets" % (name, len(tn)))
    allnames = set()
    for tn in i["meshes"].values():
        allnames.update(tn)
    print("  missing ARKit :", [n for n in ARKIT if n not in allnames])
    print("  missing visemes:", [n for n in VISEMES if n not in allnames])
    return i


if __name__ == "__main__":
    a = report(sys.argv[1])
    if len(sys.argv) > 2:
        b = report(sys.argv[2])
        print("joints only in %s: %s" % (sys.argv[1], sorted(a["joints"] - b["joints"])))
        print("joints only in %s: %s" % (sys.argv[2], sorted(b["joints"] - a["joints"])))
