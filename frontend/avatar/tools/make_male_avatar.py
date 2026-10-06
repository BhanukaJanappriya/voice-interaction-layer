"""Build a CC0 male TalkingHead avatar with MPFB in headless Blender.

Run:
  blender --background --python make_male_avatar.py -- --out male.glb [--stage N]

Prerequisites are described in MALE_AVATAR.md (MPFB 2.0.17 extension installed,
CC0 asset packs makehuman_system_assets_cc0, visemes02 and faceunits01 extracted
into the MPFB user data directory, and the TalkingHead rig files copied to
<data>/rigs/talkinghead.json and <data>/rigs/weights.talkinghead.json).
The script follows TalkingHead blender/MPFB/MPFB.md step by step.
"""
import sys
import os
import importlib
import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = "male.glb"
for i, a in enumerate(argv):
    if a == "--out":
        OUT = argv[i + 1]
OUT = os.path.abspath(OUT)

PKG = "bl_ext.user_default.mpfb"
LocationService = importlib.import_module(PKG + ".services").LocationService
S = importlib.import_module(PKG + ".services")
HumanService, TargetService, ObjectService = S.HumanService, S.TargetService, S.ObjectService
ExportService, FaceService, RigService = S.ExportService, S.FaceService, S.RigService
AssetService = S.AssetService

DATA = LocationService.get_user_data()


def asset(*parts):
    p = os.path.join(DATA, *parts)
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    return p


# Clean scene
bpy.ops.wm.read_factory_settings(use_empty=True)
importlib.import_module(PKG)  # make sure registered
try:
    bpy.ops.preferences.addon_enable(module=PKG)
except Exception as exc:  # already enabled
    print("addon_enable:", exc)

# 1. Male adult basemesh
macro = TargetService.get_default_macro_info_dict()
macro.update({"gender": 1.0, "age": 0.5, "muscle": 0.55, "weight": 0.5, "height": 0.55})
macro["race"] = {"asian": 0.0, "caucasian": 1.0, "african": 0.0}
basemesh = HumanService.create_human(macro_detail_dict=macro, scale=0.1)
basemesh.name = "Human"
print("basemesh", basemesh.name, len(basemesh.data.vertices))

# 2. TalkingHead custom rig
AssetService.invalidate_custom_rig_cache()
rig = HumanService.add_custom_rig(basemesh, "custom.talkinghead")
print("rig", rig.name, len(rig.data.bones))

# 3. Skin, eyes, brows, lashes, teeth, tongue, hair (all from the CC0 system pack), GameEngine PBR
HumanService.set_character_skin(asset("skins", "young_caucasian_male", "young_caucasian_male.mhmat"),
                                basemesh, skin_type="GAMEENGINE", material_instances=False)


def add(kind, folder, name, mat=None):
    mhclo = asset(kind, folder, name + ".mhclo")
    alt = None
    obj = HumanService.add_mhclo_asset(mhclo, basemesh, asset_type=("Eyes" if kind == "eyes" else
                                       "Eyebrows" if kind == "eyebrows" else
                                       "Eyelashes" if kind == "eyelashes" else
                                       "Teeth" if kind == "teeth" else
                                       "Tongue" if kind == "tongue" else "Hair"),
                                       material_type="GAMEENGINE", subdiv_levels=0,
                                       alternative_materials=alt)
    print("added", kind, folder, obj)
    return obj


add("eyes", "low-poly", "low-poly")
add("eyebrows", "eyebrow001", "eyebrow001")
add("eyelashes", "eyelashes01", "eyelashes01")
add("teeth", "teeth_base", "teeth_base")
add("tongue", "tongue01", "tongue01")
add("hair", "short02", "short02")

# 4. Export copy with ARKit and Oculus targets, interpolated to child meshes
collection = basemesh.users_collection[0]
copy_ = ExportService.create_character_copy(basemesh, name_suffix="_export_copy", place_in_collection=collection)
new_base = ObjectService.find_object_of_type_amongst_nearest_relatives(copy_)
TargetService.bake_targets(new_base)
FaceService.load_targets(new_base, load_microsoft_visemes=False, load_meta_visemes=True,
                         load_arkit_faceunits=True)
FaceService.interpolate_targets(new_base)
ExportService.bake_modifiers_remove_helpers(new_base, bake_masks=True, bake_subdiv=True,
                                            remove_helpers=True, also_proxy=True)

# 5. Keep only the export copy
arm = copy_
keep = {arm} | set(arm.children_recursive)
for o in list(bpy.data.objects):
    if o not in keep:
        bpy.data.objects.remove(o, do_unlink=True)
arm.name = "Armature"
for o in keep:
    if o is not arm and o.name.endswith("_export_copy"):
        o.name = o.name[:-len("_export_copy")]
        if o.type == "MESH":
            o.data.name = o.name
print("kept", [o.name for o in keep])

# Stage dump for debugging
bpy.ops.wm.save_as_mainfile(filepath=OUT.replace(".glb", ".blend"))

if "--no-export" in argv:
    sys.exit(0)

# 6. TalkingHead add-on helpers: scale, bone axes (A-pose), apply transforms
th_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                       os.environ.get("TH_ADDON", "talkinghead-addon.py"))
th_path = os.environ.get("TH_ADDON", th_path)
if os.path.isfile(th_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("talkinghead_addon", th_path)
    th = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(th)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    th.scale_character([arm])
    th.fix_bone_axes([arm], th.BONE_AXES_DATA_A)
else:
    print("WARNING talkinghead-addon.py not found, skipping scale and axis fix")

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


# 6b. Alpha handling for glTF (TalkingHead MPFB.md): OPAQUE for skin, teeth, tongue and the
# eyebrow and eyelash strips (flat colour like the female model), MASK for eyes and hair.
def fix_materials():
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        alpha = bsdf.inputs["Alpha"]
        low = mat.name.lower()
        flat = any(k in low for k in ("eyebrow", "eyelash"))
        masked = any(k in low for k in ("low-poly", "short02", "eyes"))
        if flat:
            for l in list(bsdf.inputs["Base Color"].links):
                nt.links.remove(l)
            bsdf.inputs["Base Color"].default_value = (0.04, 0.025, 0.015, 1.0)
        if masked and alpha.is_linked:
            src = alpha.links[0].from_socket
            math = nt.nodes.new("ShaderNodeMath")
            math.operation = "GREATER_THAN"
            math.inputs[1].default_value = 0.3
            nt.links.new(src, math.inputs[0])
            nt.links.new(math.outputs[0], alpha)
        else:
            for l in list(alpha.links):
                nt.links.remove(l)
            alpha.default_value = 1.0
        mat.use_backface_culling = False
        print("material", mat.name, "flat" if flat else "", "mask" if masked else "opaque")


fix_materials()

# 7. Export
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", use_selection=True,
                          export_animations=False, export_apply=False, export_morph=True,
                          export_skins=True, export_image_format="WEBP" if False else "AUTO")
print("EXPORTED", OUT, os.path.getsize(OUT))
