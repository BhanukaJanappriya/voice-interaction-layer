"""Embed sprite.svg into icons.js as one string.

sprite.svg is the source of truth. Run this after changing it:

    python frontend/ui/icons/build_sprite.py

It rewrites only the text between the SPRITE-START and SPRITE-END markers in
icons.js, so the sprite is injected from memory with no extra request.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
START, END = "/*SPRITE-START*/", "/*SPRITE-END*/"


def main() -> int:
    svg = (HERE / "sprite.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = re.sub(r">\s+<", "><", svg).strip()
    js_path = HERE / "icons.js"
    js = js_path.read_text(encoding="utf-8")
    if START not in js or END not in js:
        print("markers not found in icons.js", file=sys.stderr)
        return 1
    head, rest = js.split(START, 1)
    _, tail = rest.split(END, 1)
    block = START + "\n  var SPRITE = " + json.dumps(svg) + ";\n  " + END
    js_path.write_text(head + block + tail, encoding="utf-8", newline="\n")
    print("embedded %d bytes of sprite" % len(svg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
