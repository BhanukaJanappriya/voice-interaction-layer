"""Build the Sri Lankan SLT avatar from a TalkingHead style GLB. Runs inside Blender (bpy).

    blender --background --python tools/avatar/build_avatar.py -- \
        --src frontend/avatar/assets/male.glb --out frontend/avatar/assets/male_sl.glb \
        [--skin medium_brown|#RRGGBB] [--config config/avatar.yaml] [--meshopt 1]

What it does (see docs/AVATAR.md):
  * armature, vertex groups, shape keys and geometry are never touched; only image pixels
    and two material colour factors change.
  * skin: luminance preserving recolor of the body base colour texture.
  * hair: luminance preserving recolor to near black brown; brows and lashes: colour factor.
  * eyes: the saturated iris pixels are recolored dark brown, sclera and pupil are kept.
  * shirt: the shirt UV islands (trousers keep their texture) become plain black and the
    official logo is baked into the front panel texture at 2048 px, projected from the
    front onto the chest, so it follows the skinned body with no extra geometry.
  * export GLB, Draco off, WebP textures.
"""
import math
import os
import re
import sys
import tempfile

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


# ---------------------------------------------------------------- config

def parse_scalar(v):
    if v.startswith("["):
        return [parse_scalar(x.strip()) for x in v.strip("[]").split(",") if x.strip()]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v.strip("'\"")


def parse_yaml(text):
    """Tiny YAML subset: nested maps by indentation, scalars, inline lists, comments."""
    root = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, val = line.strip().partition(":")
        val = val.strip()
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = parse_scalar(val)
    return root


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = {"src": None, "out": None, "skin": None, "config": os.path.join(ROOT, "config", "avatar.yaml")}
    i = 0
    while i < len(argv):
        a[argv[i].lstrip("-")] = argv[i + 1]
        i += 2
    if not a["src"] or not a["out"]:
        raise SystemExit("need --src and --out")
    return a


# ---------------------------------------------------------------- colour helpers

def s2l(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def l2s(c):
    c = np.clip(np.asarray(c, np.float32), 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def lum(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def rgb255(v):
    return s2l(np.array(v, np.float32) / 255.0)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


# ---------------------------------------------------------------- image io (raw bytes, no colour management)

def upsample(a, n):
    """Bilinear resize of a (h, w, c) array to n x n (used to lift 1024 px textures to 2048)."""
    h, w = a.shape[:2]
    ys = (np.arange(n) + 0.5) * h / n - 0.5
    xs = (np.arange(n) + 0.5) * w / n - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, h - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, w - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    fy = np.clip(ys - y0, 0, 1)[:, None, None]
    fx = np.clip(xs - x0, 0, 1)[None, :, None]
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def read_img(img, min_size=None):
    img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(h, w, 4)
    if min_size and w < min_size:
        a = upsample(a, min_size)
    return a


def write_png(arr, path):
    h, w = arr.shape[:2]
    t = bpy.data.images.new("tmp_write", w, h, alpha=True, float_buffer=False)
    t.colorspace_settings.name = "Non-Color"
    t.alpha_mode = "STRAIGHT"
    t.pixels.foreach_set(np.clip(arr, 0, 1).astype(np.float32).ravel())
    t.filepath_raw = path
    t.file_format = "PNG"
    t.save()
    bpy.data.images.remove(t)


def load_srgb(path, name):
    img = bpy.data.images.load(path)
    img.name = name
    img.colorspace_settings.name = "sRGB"
    img.alpha_mode = "STRAIGHT"
    img.pack()
    return img


# ---------------------------------------------------------------- rasteriser

def tri_uv_px(uvs, W, H):
    return np.array([[u * W - 0.5, v * H - 0.5] for u, v in uvs], np.float64)


def bary(tri, px, py):
    (ax, ay), (bx, by), (cx, cy) = tri
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-12:
        return None
    w0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    w1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    return np.stack([w0, w1, 1 - w0 - w1], 1)


def raster(tri, W, H, tol=0.0):
    """Return (ys, xs, bary(n,3)) for texel centres inside the UV triangle (bottom up rows)."""
    x0, y0 = tri.min(0)
    x1, y1 = tri.max(0)
    xa, xb = max(int(math.floor(x0)), 0), min(int(math.ceil(x1)), W - 1)
    ya, yb = max(int(math.floor(y0)), 0), min(int(math.ceil(y1)), H - 1)
    if xb < xa or yb < ya:
        return None
    ys, xs = np.mgrid[ya:yb + 1, xa:xb + 1]
    ys, xs = ys.ravel(), xs.ravel()
    w = bary(tri, xs.astype(np.float64), ys.astype(np.float64))
    if w is None:
        return None
    ok = (w >= -tol).all(1)
    return ys[ok], xs[ok], w[ok]


def mesh_tris(obj):
    """Triangles as (uv(3,2), vertex index(3), world pos(3,3))."""
    me = obj.data
    me.calc_loop_triangles()
    uv = me.uv_layers.active.data
    mw = obj.matrix_world
    pos = [mw @ v.co for v in me.vertices]
    out = []
    for t in me.loop_triangles:
        out.append(([tuple(uv[l].uv) for l in t.loops], list(t.vertices),
                    [(pos[i].x, pos[i].y, pos[i].z) for i in t.vertices]))
    return out


def uv_mask(tris, W, H, tol=0.02):
    m = np.zeros((H, W), bool)
    for uvs, _, _ in tris:
        r = raster(tri_uv_px(uvs, W, H), W, H, tol)
        if r is not None:
            m[r[0], r[1]] = True
    return m


def dilate(m, n, forbid=None):
    for _ in range(n):
        d = m.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                d |= np.roll(np.roll(m, dy, 0), dx, 1)
        if forbid is not None:
            d &= ~forbid
        m = d
    return m


def components(obj):
    me = obj.data
    parent = list(range(len(me.vertices)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for e in me.edges:
        parent[find(e.vertices[0])] = find(e.vertices[1])
    return [find(i) for i in range(len(me.vertices))]


def under_shirt_tris(body_tris, suit_obj, max_dist=0.04):
    """Body triangles that lie inside the shirt (all three vertices within max_dist behind the shirt surface).
    The shirt is built from separate panels, so gaps at the collar, shoulders and armpits show the body
    through them; painting these texels shirt coloured makes the gaps read as fabric, not skin."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    tris = mesh_tris(suit_obj)
    comp = components(suit_obj)
    cz = {}
    for uvs, vi, pos in tris:
        cz.setdefault(comp[vi[0]], []).append(np.mean(pos, 0)[2])
    shirt_c = {c for c, v in cz.items() if np.mean(v) > 1.10}
    verts, polys = [], []
    for uvs, vi, pos in tris:
        if comp[vi[0]] in shirt_c:
            base = len(verts)
            verts.extend(Vector(p) for p in pos)
            polys.append((base, base + 1, base + 2))
    tree = BVHTree.FromPolygons(verts, polys)
    cache = {}

    def inside(p):
        if p not in cache:
            hit = tree.find_nearest(Vector(p))
            ok = False
            if hit[0] is not None and hit[3] < max_dist:
                # behind the surface, or on the shoulder line outside the collar where the panels leave a gap
                ok = (Vector(p) - hit[0]).dot(hit[1]) < 0 or (abs(p[0]) > 0.09 and p[2] < 1.50)
            cache[p] = ok
        return cache[p]
    return [t for t in body_tris if sum(inside(tuple(pp)) for pp in t[2]) >= 2]


# ---------------------------------------------------------------- recolors

def recolor_skin(arr, body_tris, target_rgb255, chroma_keep, comp=1.0, head_z=1.52, face_scale=1.0, cover_tris=None, cover_rgb255=None):
    """Tone map: keep each texel's luminance relative to the mean of its own region (head or
    body, so the face and the arms end up the same tone), move that mean colour to the preset,
    and keep a share of the texel's own chroma offset (lips, blush, veins)."""
    h, w = arr.shape[:2]
    head_tris = [t for t in body_tris if np.mean([p[2] for p in t[2]]) > head_z]
    rest_tris = [t for t in body_tris if np.mean([p[2] for p in t[2]]) <= head_z]
    mh = uv_mask(head_tris, w, h, 0.0)
    mb = uv_mask(rest_tris, w, h, 0.0) & ~mh
    lin = s2l(arr[..., :3])
    L = lum(lin)
    target = rgb255(target_rgb255) * comp  # comp: albedo needed for the on screen colour under TalkingHead lighting
    out = np.zeros_like(lin)
    notes = []
    # One shared mean for head and body: separate means gave a visible seam at the neck.
    for name, m, sel in (("head and body", mb | mh, np.ones_like(mh)),):
        mean_rgb = lin[m].mean(0)
        mean_L = float(L[m].mean())
        ratio = (L / mean_L)[..., None]
        reg = np.clip(target[None, None, :] * ratio + chroma_keep * (lin - mean_rgb[None, None, :] * ratio), 0, 1)
        out[sel] = reg[sel]
        notes.append("%s mean sRGB %s lum %.3f" % (name, np.round(l2s(mean_rgb) * 255).astype(int).tolist(), mean_L))
    out[mh] *= face_scale
    if cover_tris:
        mc = uv_mask(cover_tris, w, h, 0.0)
        out[mc] = rgb255(cover_rgb255)[None, :]
        notes.append("%d body triangles under the shirt painted shirt colour" % len(cover_tris))  # the face gets more light on screen than the torso, so its albedo is a little lower
    res = arr.copy()
    res[..., :3] = l2s(np.clip(out, 0, 1))
    return res, "skin -> %s albedo x%.2f (face x%.2f); %s" % (list(target_rgb255), comp, face_scale, "; ".join(notes))


def recolor_hair(arr, target_rgb255, comp=1.0):
    lin = s2l(arr[..., :3])
    L = lum(lin)
    sel = arr[..., 3] > 0.5
    mean_L = float(L[sel].mean())
    ratio = (np.clip(L / mean_L, 0, 2.5) ** 0.7)  # compressed so bright tips do not turn grey
    a = arr[..., 3]
    # semi transparent edge texels carry bleed colour from the atlas background (light halo): flatten and darken them
    edge = 1.0 - np.clip((a - 0.3) / 0.6, 0, 1)
    ratio = (ratio * (1 - edge) + 0.6 * edge)[..., None]
    out = rgb255(target_rgb255)[None, None, :] * comp * ratio
    res = arr.copy()
    res[..., :3] = l2s(np.clip(out, 0, 1))
    return res, "hair mean luminance %.3f recolored to %s" % (mean_L, list(target_rgb255))


def recolor_iris(arr, target_rgb255, comp=1.0):
    rgb = arr[..., :3]
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)
    t = np.clip((sat - 0.35) / 0.25, 0, 1)
    m = t * t * (3 - 2 * t)
    lin = s2l(rgb)
    L = lum(lin)
    sel = m > 0.5
    mean_L = float(L[sel].mean()) if sel.any() else 1.0
    ratio = np.clip(L / mean_L, 0, 2.5)[..., None]
    tgt = rgb255(target_rgb255)[None, None, :] * comp * ratio
    out = lin * (1 - m[..., None]) + tgt * m[..., None]
    res = arr.copy()
    res[..., :3] = l2s(np.clip(out, 0, 1))
    return res, "iris pixels (%d) recolored to %s" % (int(sel.sum()), list(target_rgb255))


def load_logo(path):
    img = bpy.data.images.load(path)
    img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(h, w, 4)[::-1]  # top row first
    bpy.data.images.remove(img)
    return s2l(a[..., :3]), a[..., 3].copy(), w, h


def build_shirt(arr, obj, cfg, female, log):
    """Black shirt islands plus the baked, antialiased chest logo. Returns the new array."""
    H, W = arr.shape[:2]
    tris = mesh_tris(obj)
    comp = components(obj)
    cz = {}
    for uvs, vi, pos in tris:
        cz.setdefault(comp[vi[0]], []).append(np.mean(pos, 0))
    info = {c: (np.mean(v, 0), len(v)) for c, v in cz.items()}
    shirt_c = [c for c, (m, n) in info.items() if m[2] > 1.10]
    log("  shirt components: %d of %d (centroid z above 1.10 m)" % (len(shirt_c), len(info)))
    wide = [c for c in shirt_c if info[c][1] > 200]
    front = min(wide, key=lambda c: info[c][0][1])  # the body faces -Y in Blender
    log("  front panel centroid %s" % np.round(info[front][0], 3).tolist())
    shirt_tris = [t for t in tris if comp[t[1][0]] in shirt_c]
    other = [t for t in tris if comp[t[1][0]] not in shirt_c]
    smask = uv_mask(shirt_tris, W, H, 0.02)
    omask = uv_mask(other, W, H, 0.02)
    smask = dilate(smask & ~omask, 4, forbid=omask)
    out = arr.copy()
    out[smask, :3] = l2s(rgb255(cfg["shirt_rgb"]))[None, :]

    lcfg = cfg["logo"]
    logo_lin, logo_a, lw, lh = load_logo(os.path.join(ROOT, lcfg["file"]))
    zc = float(lcfg["centre_z_ratio_female" if female else "centre_z_ratio_male"]) * 1.77
    wm = float(lcfg["width_m"])
    hm = wm * lh / lw  # uniform scale only
    log("  logo %dx%d px placed %.3f x %.3f m, centre z %.3f" % (lw, lh, wm, hm, zc))
    S = 4
    offs = [((i + 0.5) / S - 0.5) for i in range(S)]
    acc = np.zeros((H, W, 3), np.float32)
    acca = np.zeros((H, W), np.float32)
    for uvs, vi, pos in [t for t in tris if comp[t[1][0]] == front]:
        tri = tri_uv_px(uvs, W, H)
        r = raster(tri, W, H, 0.02)
        if r is None:
            continue
        ys, xs, _ = r
        P = np.array(pos)
        for dy in offs:
            for dx in offs:
                w = bary(tri, xs + dx, ys + dy)
                x = w @ P[:, 0]
                z = w @ P[:, 2]
                ix = np.floor((x / wm + 0.5) * lw).astype(int)
                iy = np.floor(((zc - z) / hm + 0.5) * lh).astype(int)
                ok = (ix >= 0) & (ix < lw) & (iy >= 0) & (iy < lh)
                if not ok.any():
                    continue
                a = logo_a[iy[ok], ix[ok]]
                acc[ys[ok], xs[ok]] += logo_lin[iy[ok], ix[ok]] * a[:, None]
                acca[ys[ok], xs[ok]] += a
    acc /= S * S
    acca /= S * S
    cov = acca > 0
    comp_lin = acc + (1 - acca)[..., None] * s2l(out[..., :3])
    out[..., :3] = np.where(cov[..., None], l2s(comp_lin), out[..., :3])
    log("  logo texels touched: %d" % int(cov.sum()))
    return out


# ---------------------------------------------------------------- main

def main():
    args = parse_args()
    cfg = parse_yaml(open(args["config"], encoding="utf-8").read())
    skin = args["skin"] or cfg["default_skin"]
    skin_rgb = hex_to_rgb(skin) if skin.startswith("#") else cfg["skin_presets"][skin]["rgb"]
    src = os.path.abspath(args["src"])
    out_path = os.path.abspath(args["out"])

    def log(s):
        print("[avatar] " + s, flush=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    female = any("female" in o.name for o in objs)
    log("source %s (%s), skin %s -> %s" % (os.path.basename(src), "female" if female else "male", skin, skin_rgb))

    tmp = tempfile.mkdtemp(prefix="avatar_tex_")
    body_tris = mesh_tris(bpy.data.objects["Human"])

    def base_node(mat):
        for n in mat.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                return n
        return None

    def base_image_node(mat):
        n = base_node(mat)
        if n is None or not n.inputs["Base Color"].links:
            return None
        s = n.inputs["Base Color"].links[0].from_node
        return s if s.type == "TEX_IMAGE" else None

    hc = float(cfg.get("light_compensation", 1.0))
    suit = [o for o in objs if "casualsuit" in o.name][0]
    cover = under_shirt_tris(body_tris, suit)
    log("body triangles under the shirt: %d of %d" % (len(cover), len(body_tris)))
    done = {}
    for o in objs:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.name in done:
                continue
            nm = mat.name.lower()
            if "eyebrow" in nm or "eyelash" in nm:
                base_node(mat).inputs["Base Color"].default_value = (rgb255(cfg["brow_lash_rgb"]) * hc).tolist() + [1.0]
                done[mat.name] = "base color factor set to %s" % cfg["brow_lash_rgb"]
                continue
            node = base_image_node(mat)
            if node is None:
                done[mat.name] = "no base texture, untouched"
                continue
            img = node.image
            if mat.name == "Human.body":
                new_arr, note = recolor_skin(read_img(img, 2048), body_tris, skin_rgb, float(cfg["skin_detail_chroma"]), float(cfg.get("skin_light_compensation", 1.0)), 1.52, float(cfg.get("skin_face_scale", 1.0)), cover, cfg["shirt_rgb"])
                bn = base_node(mat)
                for key in ("Specular IOR Level", "Specular"):
                    if key in bn.inputs:
                        bn.inputs[key].default_value = float(cfg.get("skin_specular", 0.5))
                        break
            elif "short02" in nm or "ponytail" in nm:
                new_arr, note = recolor_hair(read_img(img), cfg["hair_rgb"], hc)
                bn = base_node(mat)
                bn.inputs["Roughness"].default_value = 1.0  # no blue sheen from the cool key light
                for key in ("Specular IOR Level", "Specular"):
                    if key in bn.inputs:
                        bn.inputs[key].default_value = 0.0
                        break
            elif "low-poly" in nm or "high-poly" in nm:
                new_arr, note = recolor_iris(read_img(img), cfg["iris_rgb"], hc)
            elif "casualsuit" in nm:
                arr = read_img(img, int(cfg.get("shirt_texture_px", 2048)))
                new_arr = build_shirt(arr, o, cfg, female, log)
                note = "shirt islands black, logo baked at %d px" % arr.shape[1]
                bn = base_node(mat)
                bn.inputs["Roughness"].default_value = float(cfg.get("shirt_roughness", 1.0))
                for key in ("Specular IOR Level", "Specular"):
                    if key in bn.inputs:
                        bn.inputs[key].default_value = float(cfg.get("shirt_specular", 0.0))
                        break
            else:
                done[mat.name] = "untouched"
                continue
            p = os.path.join(tmp, re.sub(r"\W", "_", mat.name) + ".png")
            write_png(new_arr, p)
            node.image = load_srgb(p, img.name + "_sl")
            done[mat.name] = note
    for k, v in done.items():
        log("%-34s %s" % (k, v))

    # sizes: 2048 for skin and shirt base colour, 1024 for everything else
    for img in list(bpy.data.images):
        if img.users == 0:
            bpy.data.images.remove(img)
    for img in list(bpy.data.images):
        n = img.name.lower()
        target = int(cfg.get("shirt_texture_px", 2048)) if ("casualsuit" in n and "normal" not in n) else (2048 if n.startswith("young_") else 1024)
        if max(img.size) > target:
            img.scale(target, target)
        log("image %-40s %s" % (img.name, tuple(img.size)))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    extra = {}
    if str(args.get("meshopt", "0")).lower() in ("1", "true", "yes"):
        extra["export_meshopt_compression_enable"] = True  # EXT_meshopt_compression, TalkingHead registers the decoder
    bpy.ops.export_scene.gltf(
        filepath=out_path, export_format="GLB", export_image_format="WEBP", export_image_quality=88,
        export_materials="EXPORT", export_animations=False, export_apply=False, export_morph=True,
        export_morph_normal=False, export_skins=True, export_yup=True,
        export_draco_mesh_compression_enable=False, export_extras=True, **extra)
    log("wrote %s (%d bytes)" % (out_path, os.path.getsize(out_path)))


main()
