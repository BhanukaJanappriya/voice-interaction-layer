"""Run inside Blender: prints meshes, materials, textures and shape key counts of a GLB.
blender --background --python tools/avatar/inspect_glb.py -- file.glb
"""
import sys
import bpy

path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=path)
for o in bpy.data.objects:
    extra = ""
    if o.type == "MESH":
        sk = o.data.shape_keys
        extra = "verts=%d keys=%d mats=%s parent=%s mods=%s" % (
            len(o.data.vertices), len(sk.key_blocks) if sk else 0,
            [m.name if m else None for m in o.data.materials], o.parent.name if o.parent else None,
            [m.type for m in o.modifiers])
    print("OBJ", o.type, o.name, extra)
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    for n in m.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            bc = n.inputs["Base Color"]
            src = bc.links[0].from_node if bc.links else None
            img = src.image if src is not None and src.type == "TEX_IMAGE" else None
            print("MAT", m.name, "base", tuple(round(v, 2) for v in bc.default_value),
                  "img", (img.name, tuple(img.size)) if img else None,
                  "alpha", n.inputs["Alpha"].default_value, m.blend_method if hasattr(m, "blend_method") else "")
for i in bpy.data.images:
    print("IMG", i.name, tuple(i.size))
