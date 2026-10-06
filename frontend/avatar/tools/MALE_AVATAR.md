# Male avatar build (headless Blender, MPFB)

Output: `frontend/avatar/assets/male.glb` (18.4 MB, git ignored by `*.glb`). Rebuild with the steps below. Total time about 3 minutes.

## One time setup

1. Blender 5.2.2 LTS at `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`.
2. Install MPFB 2.0.17 (GPL-3.0-or-later add-on, official extension, SHA-256 `4f0a879d64a39bf646fbf5f53601ac678855da329d650617dca5737548239a87`):
   `blender --background --command extension install-file -r user_default add-on-mpfb-v2.0.17.zip --enable`
   from https://extensions.blender.org/download/sha256:4f0a879d64a39bf646fbf5f53601ac678855da329d650617dca5737548239a87/add-on-mpfb-v2.0.17.zip
3. Extract these CC0 asset packs (zip root files) into the MPFB user data dir
   `%APPDATA%\Blender Foundation\Blender\5.2\extensions\.user\user_default\mpfb\data`:
   - https://files.makehumancommunity.org/asset_packs/makehuman_system_assets/makehuman_system_assets_cc0.zip (about 280 MB, use this CC0 variant)
   - https://files.makehumancommunity.org/functional/visemes02.zip
   - https://files.makehumancommunity.org/functional/faceunits01.zip
4. Download from https://github.com/met4citizen/TalkingHead/tree/main/blender/MPFB (MIT): `talkinghead.mpfbskel`, `talkinghead.mhw`, `talkinghead-addon.py`.
   Copy `talkinghead.mpfbskel` to `<data>\rigs\talkinghead.json` and add the key `"identifying_bones": ["LeftToe_End"]` to that JSON; copy `talkinghead.mhw` to `<data>\rigs\weights.talkinghead.json`. This is what the UI step "Save rig to library" does, without needing a dummy human.

## Build

```
set TH_ADDON=C:\path\to\talkinghead-addon.py
blender --background --python frontend\avatar\tools\make_male_avatar.py -- --out frontend\avatar\assets\male.glb
python frontend\avatar\tools\validate_glb.py frontend\avatar\assets\male.glb frontend\avatar\assets\mpfb.glb
```

The script follows TalkingHead `blender/MPFB/MPFB.md`: male basemesh (gender 1.0, caucasian), TalkingHead rig and weights, CC0 young caucasian male skin, low-poly eyes, eyebrow001, eyelashes01, teeth_base, tongue01, short02 hair (all GameEngine PBR), export copy with Meta visemes, ARKit face units and interpolation to child meshes, bake masks and helper removal, TalkingHead `scale_character` and `fix_bone_axes` (A pose) helpers, alpha fix (opaque for skin, teeth, tongue, brow and lash strips; alpha clip 0.3 for eyes and hair), glTF binary export without animation.

## Manual fallback in the Blender UI

Follow MPFB.md in the TalkingHead repo (about 30 minutes): install the packs via MPFB `System and resources`, install the add-on, load rig and weights and save to library as `talkinghead`, create a male human, add the custom rig, apply assets, `Operations` then `Export copy` with the viseme and face unit options, TalkingHead scale and axis fix, then export GLB.

## Known gaps

- No clothes: the model is bare chested. Add a CC0 clothing asset from the system pack (for example under `clothes/`) with one more `add()` call in the script if wanted. Clothing was not checked for CC0 status individually.
- No Meshopt or Draco compression applied (TalkingHead main supports Meshopt; `gltf-transform optimize --compress meshopt --texture-compress webp` would shrink it further).
- Hair is a static mesh (no hair bones), so there is no dynamic hair motion.
