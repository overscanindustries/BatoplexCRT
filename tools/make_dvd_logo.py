"""Build art/dvd-logo*.png, the Bounce saver's DVD Video logos: Classic,
Official and Oval.

Source: Wikimedia Commons "File:DVD-Video Logo.svg" (public domain: simple text and shapes;
a trademark of DVD Format/Logo Licensing Corporation). The saver shows the form that idle
players used: the DVD letters and the disc in one colour, with VIDEO cut out of the disc (no
centre hole, no TM). This script takes the letters, the disc and VIDEO from the Commons file,
puts VIDEO into the disc, and renders white on black with rsvg-convert. The box tints and
scales the PNG; it runs no tool.

    python3 tools/make_dvd_logo.py        (build machine: curl and rsvg-convert)
"""
import hashlib
import os
import re
import subprocess
import tempfile
import urllib.request

URL = "https://upload.wikimedia.org/wikipedia/commons/e/e7/DVD-Video_Logo.svg"
SHA256 = "a29feb3af0cb81741b4c57f1c2b2c8c06a05e02f38fc185c4c51e01aa0b7ee40"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "art",
                   "dvd-logo.png")
WIDTH = 352
VIDEO_SCALE = 0.75


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "batoplex-build/1.0"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise SystemExit("DVD-Video_Logo.svg changed on Commons: check it, then update SHA256")
    d = re.search(r' d="([^"]+)"', raw.decode()).group(1)
    subs = [s for s in re.split(r"(?=M)", d) if s.strip()]
    # The 9 parts, measured: 0-1 TM, 2 3 7 4 6 = V I D E O, 5 the disc (outer, then hole),
    # 8 the DVD letters.
    letters, disc = subs[8], subs[5].split("zm")[0] + "z"
    video = "".join(subs[i] for i in (2, 3, 7, 4, 6))
    s = VIDEO_SCALE
    tx, ty = 236 * (1 - s), 188.1 - s * 274.55          # VIDEO centred on the disc
    vid = '<g transform="translate(%.2f %.2f) scale(%s)"><path fill="#%s" ' \
          'fill-rule="evenodd" d="%s"/></g>'
    # Classic: VIDEO cut out of the disc (the idle-screen form most players showed).
    classic = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -2 502 226" width="502" '
               'height="226"><path fill="#fff" fill-rule="evenodd" d="%s"/><path fill="#fff" '
               'd="%s"/>%s</svg>' % (letters, disc, vid % (tx, ty, s, "000", video)))
    # Official: the licensed mark (disc with its hole, VIDEO below; no TM).
    mark = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -2 502 304" width="502" '
            'height="304"><path fill="#fff" fill-rule="evenodd" d="%s"/><path fill="#fff" '
            'fill-rule="evenodd" d="%s"/><path fill="#fff" fill-rule="evenodd" d="%s"/></svg>'
            % (letters, subs[5], video))
    # Oval: a filled badge with the letters, a disc ring and VIDEO cut out (as a real player
    # shows it in the Commons photo "DVD screensaver on real hardware").
    s2 = 0.62
    t2x, t2y = 236 * (1 - s2), 188.1 - s2 * 274.55
    oval = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-70 -40 640 330" width="640" '
            'height="330"><ellipse cx="250" cy="125" rx="315" ry="160" fill="#fff"/>'
            '<g transform="translate(250 125) scale(0.78) translate(-250 -125)">'
            '<path fill="#000" fill-rule="evenodd" d="%s"/><path fill="none" stroke="#000" '
            'stroke-width="10" d="%s"/>%s</g></svg>'
            % (letters, disc, vid % (t2x, t2y, s2, "000", video)))
    for svg, name in ((classic, "dvd-logo.png"), (mark, "dvd-logo-mark.png"),
                      (oval, "dvd-logo-oval.png")):
        with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as f:
            f.write(svg)
        try:
            subprocess.run(["rsvg-convert", "-w", str(WIDTH), "-b", "black", f.name, "-o",
                            os.path.join(os.path.dirname(OUT), name)], check=True)
        finally:
            os.unlink(f.name)
    print(OUT)


if __name__ == "__main__":
    main()
