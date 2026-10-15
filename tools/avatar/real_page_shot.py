"""Screenshots of the real voice screen (app served on a test port) for both genders and two viewports.

    PYTHONIOENCODING=utf-8 venv/Scripts/python.exe tools/avatar/real_page_shot.py [outdir] [base_url] [variant]

Needs a running app that serves the repo files (default http://127.0.0.1:8051). variant is south_asian or original.
Also measures lip sync in the real page: plays assets/sample.wav through window.avatarRenderer.attachAudioStream and
records the peak applied jawOpen and viseme values.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests", "ui"))
from playwright.sync_api import sync_playwright  # noqa: E402
from ui_helpers import open_screen  # noqa: E402

out = (sys.argv[1] if len(sys.argv) > 1 else os.environ["TEMP"] + "/shots") + "/"
base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8051/"
variant = sys.argv[3] if len(sys.argv) > 3 else "south_asian"
os.makedirs(out, exist_ok=True)
VIEWS = {"mobile": (390, 844), "desktop": (1280, 800)}
SPEAK = """async () => {
  const api = window.avatarRenderer; const ctx = new AudioContext(); await ctx.resume();
  const buf = await ctx.decodeAudioData(await (await fetch('/avatar/assets/sample.wav')).arrayBuffer());
  const src = ctx.createBufferSource(); src.buffer = buf; const dest = ctx.createMediaStreamDestination();
  src.connect(dest); await api.attachAudioStream(dest.stream); src.start();
  let jaw = 0, vis = 0; const t0 = performance.now();
  while (performance.now() - t0 < 5000) {
    await new Promise(r => setTimeout(r, 80));
    const m = api._debug.head.mtAvatar; jaw = Math.max(jaw, (m.jawOpen && m.jawOpen.applied) || 0);
    for (const k in m) if (k.startsWith('viseme_')) vis = Math.max(vis, m[k].applied || 0);
  }
  return {jaw, vis};
}"""
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                                "--autoplay-policy=no-user-gesture-required"])
    for gender in ("female", "male"):
        for vname, (w, h) in VIEWS.items():
            ctx = b.new_context(viewport={"width": w, "height": h})
            ctx.add_init_script("localStorage.setItem('avatarGender','%s')" % gender)
            page = ctx.new_page()
            msgs = []
            page.on("console", lambda m: msgs.append(m.text[:140]) if m.type in ("error", "warning") else None)
            open_screen(page, base, "voice", "en")
            page.wait_for_function("() => document.querySelector('#th-avatar canvas') || document.querySelector('.has-th')", timeout=60000)
            page.wait_for_timeout(int(os.environ.get("SHOT_WAIT", "20000")))
            page.screenshot(path=out + "voice_%s_%s.png" % (gender, vname))
            lip = ""
            if vname == "mobile":
                try:
                    lip = page.evaluate(SPEAK)
                except Exception as exc:
                    lip = "lip sync probe failed: %s" % str(exc)[:120]
            print(gender, vname, "has-th:", page.evaluate("() => !!document.querySelector('.has-th')"), lip, msgs[:3], flush=True)
            ctx.close()
    b.close()
