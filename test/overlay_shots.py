"""Render each playback overlay state through the real byte path and save PNG shots.

    python3 test/overlay_shots.py OUTDIR

The BGRA bytes that mpv would get are decoded and blended over a test picture, so the
shot shows what the CRT shows. Fails if any text lies outside the safe area or its component.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pygame  # noqa: E402

pygame.init()

from batoplex import draw, overlay  # noqa: E402
from batoplex.tokens import C  # noqa: E402


def picture():
    """A busy test still: colour bands and a bright face-like disc, so edges show."""
    s = pygame.Surface((641, 480))
    for y in range(480):
        s.fill((40 + y // 4, 60 + (y * 3) % 120, 90 + y // 6), (0, y, 641, 1))
    pygame.draw.circle(s, (230, 200, 170), (420, 220), 110)
    pygame.draw.rect(s, (250, 250, 250), (60, 300, 200, 60))
    return s


def blend(base, data, x, y, w, h):
    """Composite premultiplied BGRA bytes the way mpv does."""
    img = pygame.image.frombytes(data, (w, h), "BGRA")
    out = base.copy()
    for yy in range(h):
        for xx in range(w):
            b, g, r, a = img.get_at((xx, yy))   # frombytes reads B,G,R,A into R,G,B,A slots
            dr, dg, db = out.get_at((x + xx, y + yy))[:3]
            k = 255 - a
            out.set_at((x + xx, y + yy), (min(255, b + dr * k // 255), min(255, g + dg * k // 255),
                                          min(255, r + db * k // 255)))
    return out


def check_premul(data):
    for i in range(0, len(data), 4):
        if max(data[i], data[i + 1], data[i + 2]) > data[i + 3]:
            return False
    return True


def run(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    bad = []
    model = {"title": "Cowboy Bebop • S1 E4 • Gateway Shuffle",
             "buttons": [("pause", "Pause"), ("prev", "Previous"), ("next", "Next Episode"),
                         ("subs", "Audio & Subtitles"), ("stop", "Stop")],
             "focus_row": "seek", "focus_btn": 0, "pos_s": 754, "dur_s": 1460,
             "markers": [(90, 180)], "chapters": [300, 610, 980, 1250]}
    for margins in ((40, 36), (8, 16), (72, 72)):
        safe = {"left": margins[0], "right": margins[0], "top": margins[1], "bottom": margins[1]}
        sr = pygame.Rect(safe["left"], safe["top"], 640 - safe["left"] - safe["right"],
                         480 - safe["top"] - safe["bottom"])
        tag = "%dx%d" % margins
        states = [
            ("bar-seek", "bar", dict(model)),
            ("bar-buttons", "bar", dict(model, focus_row="buttons", focus_btn=3)),
            ("bar-scrub", "bar", dict(model, scrub_s=910)),
            ("bar-paused", "bar", dict(model, buttons=[("play", "Play")] + model["buttons"][1:],
                                       focus_row="buttons", focus_btn=0)),
            ("mini", "mini", dict(model)),
            ("toast", "toast", "Subtitles: English • Forced (PGS)"),
            ("panel-subs", "panel", {"tab": "sub", "focus": 1, "more_up": False, "more_down": True,
                                     "rows": [("None", False), ("English (SRT)", True),
                                              ("English • Forced (PGS)", False),
                                              ("English • SDH (SRT)", False), ("Japanese (ASS)", False),
                                              ("Spanish (SRT)", False)]}),
            ("panel-audio", "panel", {"tab": "audio", "focus": 0, "more_up": False,
                                      "more_down": False,
                                      "rows": [("Japanese • 5.1 • AC3", True),
                                               ("English • Stereo • AAC", False)]}),
            ("pmenu", "pmenu", ["Subtitles: English • Forced (PGS)", "Audio: Japanese • 5.1 • AC3",
                                "Play from Start", "Next Episode", "Previous Episode", "Stop"]),
            ("skip", "skip", "Skip Intro"),
            ("skip-bar", "skipbar", "Skip Credits"),
            ("upnext", "upnext", {"show": "Cowboy Bebop", "line": "S1 E5", "count": 12,
                                  "title": "Ballad of Fallen Angels", "focus": "next"}),
            ("upnext-still", "upnext", {"show": "Cowboy Bebop", "line": "S1 E4", "still": True,
                                        "focus": "continue"}),
        ]
        for name, kind, m in states:
            if kind == "bar":
                top = overlay.bar_top(safe)
                w, h, x = 641, 480 - top, 0
                surf = pygame.Surface((w, h))
                surf.fill(C["bg"])
                rec = overlay.draw_bar(surf, m, safe, oy=top)
            elif kind == "mini":
                top = overlay.mini_top(safe)
                w, h, x = 641, 480 - top, 0
                surf = pygame.Surface((w, h))
                surf.fill(C["bg"])
                rec = overlay.draw_mini(surf, m, safe, oy=top)
            elif kind == "panel":
                x, top, w, h = overlay.panel_rect(len(m["rows"]), safe)
                surf = pygame.Surface((w, h))
                rec = overlay.draw_panel(surf, m, ox=x, oy=top)
            elif kind == "pmenu":
                x, top, w, h = overlay.menu_rect(len(m), safe)
                surf = pygame.Surface((w, h))
                rec = overlay.draw_menu(surf, m, 0, ox=x, oy=top)
            elif kind in ("skip", "skipbar"):
                x, top, w, h = overlay.skip_rect(m, safe, kind == "skipbar")
                surf = pygame.Surface((w, h))
                rec = overlay.draw_skip(surf, m, ox=x, oy=top)
            elif kind == "upnext":
                x, top, w, h = overlay.upnext_card_rect(safe, still=m.get("still", False))
                surf = pygame.Surface((w, h))
                rec = overlay.draw_upnext(surf, m, ox=x, oy=top)
            else:
                x, top, w, h = overlay.toast_rect(m, safe)
                surf = pygame.Surface((w, h))
                rec = overlay.draw_toast(surf, m, ox=x, oy=top)
            flt = draw.FlickerFilter((w, h))
            data = overlay.to_bgra(surf, flt, "normal")
            assert len(data) == w * h * 4, name
            if not check_premul(data):
                bad.append("%s %s: not premultiplied" % (tag, name))
            for r, comp in zip(rec.rects, rec.boxes):
                if not sr.contains(r):
                    bad.append("%s %s: text %s outside safe %s" % (tag, name, r, sr))
                if not comp.contains(r):
                    bad.append("%s %s: text %s outside its box %s" % (tag, name, r, comp))
            shot = blend(picture(), data, x, top, w, h)
            pygame.image.save(shot, os.path.join(out_dir, "ov-%s-%s.png" % (tag, name)))
    for b in bad:
        print("FAIL", b)
    print("overlay shots: %d problems" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "test/out/overlay"))
