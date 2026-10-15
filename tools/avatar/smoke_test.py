"""Headless lip sync smoke test and before/after screenshots for the new avatars.

Run with the repo venv (it has Playwright; system Chrome is used like tests/ui does):

    venv/Scripts/python.exe tools/avatar/smoke_test.py

It serves frontend/ with a tiny static server (no app, no backend), opens
frontend/avatar/demo.html and, because avatar.js is shared code that must not change,
swaps the avatar file with a Playwright route: /avatar/assets/mpfb.glb is answered with
female_sl.glb (or the original for the "before" shot), male.glb with male_sl.glb.
Then it plays assets/sample.wav through the demo's "Sample speech" button and records
how far the jaw and viseme morph targets move. Screenshots go to docs/avatar-shots/.
"""
import http.server
import io
import json
import mimetypes
import socketserver
import sys
import threading
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend"
ASSETS = FRONT / "avatar" / "assets"
SHOTS = ROOT / "docs" / "avatar-shots"
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(FRONT), **k)

    def log_message(self, *a):
        pass


def serve():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def shrink(png_bytes, path, limit=400_000):
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    size = img.size
    while True:
        buf = io.BytesIO()
        img.quantize(colors=256, method=Image.Quality.MEDIANCUT if hasattr(Image, "Quality") else 0).save(buf, "PNG", optimize=True)
        if buf.tell() <= limit or size[0] < 300:
            break
        size = (int(size[0] * 0.9), int(size[1] * 0.9))
        img = img.resize(size, Image.LANCZOS)
    path.write_bytes(buf.getvalue())
    return buf.tell(), size


def run_case(pw, base, gender, glb, shot, view, log):
    browser = pw.chromium.launch(channel="chrome", headless=True, args=[
        "--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = browser.new_context(viewport={"width": 900, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    msgs = []
    page.on("console", lambda m: msgs.append(m.text))
    page.on("pageerror", lambda e: msgs.append("PAGEERROR " + str(e)))
    served = ASSETS / glb
    target = "mpfb.glb" if gender == "female" else "male.glb"
    if glb != target:
        page.route("**/avatar/assets/" + target, lambda r: r.fulfill(path=str(served), content_type="model/gltf-binary"))
    page.goto(base + "/avatar/demo.html?noinit")
    page.add_style_tag(content="#stage{width:720px!important;height:720px!important;border-radius:0!important}")
    t0 = time.time()
    ok = page.evaluate(
        """async ([g, view]) => { const api = window.demo.api;
             return await api.init(document.getElementById('stage'), {gender: g, cameraView: view.v, cameraDistance: view.d}); }""",
        [gender, view])
    log("  %s %s init ok=%s in %.1fs" % (gender, glb, ok, time.time() - t0))
    page.wait_for_timeout(2500)
    if not ok:
        log("  init failed, console: %s" % msgs[-5:])
        browser.close()
        return False, {"jawOpen": 0.0, "viseme": 0.0}
    names = page.evaluate("() => Object.keys(window.demo.api._debug.head.mtAvatar).length")
    log("  mtAvatar morph targets known to TalkingHead: %s" % names)
    png = page.locator("#stage").screenshot()
    log("  shot %s %s" % (shot.name, shrink(png, shot)))
    # lip sync: play the sample and sample the applied morph values
    page.evaluate("() => window.demo.api.setState('speaking')")
    page.click("#sample")
    peak = {"jawOpen": 0.0, "viseme": 0.0}
    seen = set()
    for _ in range(60):
        page.wait_for_timeout(100)
        r = page.evaluate("""() => { const m = window.demo.api._debug.head.mtAvatar; let v = 0, k = null;
              for (const n in m) if (n.startsWith('viseme_') && m[n].applied > v) { v = m[n].applied; k = n; }
              return {jaw: (m.jawOpen && m.jawOpen.applied) || 0, v, k}; }""")
        peak["jawOpen"] = max(peak["jawOpen"], r["jaw"])
        peak["viseme"] = max(peak["viseme"], r["v"])
        if r["k"] and r["v"] > 0.05:
            seen.add(r["k"])
    log("  lip sync: peak jawOpen %.3f, peak viseme %.3f, visemes seen %s" % (peak["jawOpen"], peak["viseme"], sorted(seen)))
    errs = [m for m in msgs if "PAGEERROR" in m or "rror" in m][:5]
    log("  console problems: %s" % errs)
    browser.close()
    return ok, peak


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    srv, port = serve()
    base = "http://127.0.0.1:%d" % port
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)
    view = {"v": "upper", "d": -0.3}
    results = {}
    with sync_playwright() as pw:
        for gender, before, after in (("male", "male.glb", "male_sl.glb"), ("female", "mpfb.glb", "female_sl.glb")):
            log("== before %s" % gender)
            run_case(pw, base, gender, before, SHOTS / ("before_%s.png" % gender), view, log)
            log("== after %s" % gender)
            results[gender] = run_case(pw, base, gender, after, SHOTS / ("after_%s.png" % gender), view, log)
    (SHOTS / "smoke_result.json").write_text(json.dumps({k: {"loaded": v[0], "peaks": v[1]} for k, v in results.items()}, indent=1))
    ok = all(v[0] and v[1]["jawOpen"] > 0.05 for v in results.values())
    log("SMOKE %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
