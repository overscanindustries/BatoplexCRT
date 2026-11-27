import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import inputs  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def drain(inp):
    out = []
    while True:
        ev = inp.pop_event()
        if ev is None:
            return out
        out.append(ev)


def hold(fps, seconds):
    clk = Clock()
    inp = inputs.Input(clock=clk)
    inp._press(inputs.RIGHT)
    for _ in range(int(fps * seconds)):
        clk.t += 1.0 / fps
        inp.tick()
    return drain(inp)


class FakeEvent:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class TestRepeat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_same_count_at_30_and_60_fps(self):
        a, b = hold(30, 1.5), hold(60, 1.5)
        self.assertEqual(len(a), len(b))
        self.assertEqual(a[0], (inputs.RIGHT, False))
        self.assertTrue(all(r for _, r in a[1:]))

    def test_no_repeat_before_delay(self):
        self.assertEqual(len(hold(60, 0.3)), 1)

    def test_rate(self):
        # 1 press + repeats at 0.333, 0.466 ... up to 1.5 s -> 1 + 9
        self.assertEqual(len(hold(60, 1.5)), 10)

    def test_slow_frame_does_not_flood(self):
        clk = Clock()
        inp = inputs.Input(clock=clk)
        inp._press(inputs.DOWN)
        clk.t += 5.0
        inp.tick()
        self.assertLessEqual(len(drain(inp)), 4)

    def test_flush_stops_held_repeat(self):
        clk = Clock()
        inp = inputs.Input(clock=clk)
        inp._press(inputs.LEFT)
        inp.flush()
        clk.t += 2.0
        inp.tick()
        self.assertEqual(drain(inp), [])
        self.assertTrue(inp.is_held(inputs.LEFT))
        inp._release(inputs.LEFT)
        self.assertFalse(inp.is_held(inputs.LEFT))

    def test_pop_compat(self):
        inp = inputs.Input()
        inp.inject(inputs.OK)
        self.assertEqual(inp.pop(), inputs.OK)
        self.assertIsNone(inp.pop())


class TestTriggers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_controller_trigger_hysteresis(self):
        inp = inputs.Input()
        inp._trigger(1, 4, inputs.LT, 0.4)
        self.assertEqual(drain(inp), [])
        inp._trigger(1, 4, inputs.LT, 0.6)
        inp._trigger(1, 4, inputs.LT, 0.9)
        inp._trigger(1, 4, inputs.LT, 0.4)   # still above release
        self.assertEqual(drain(inp), [(inputs.LT, False)])
        self.assertTrue(inp.is_held(inputs.LT))
        inp._trigger(1, 4, inputs.LT, 0.1)
        self.assertFalse(inp.is_held(inputs.LT))
        inp._trigger(1, 4, inputs.LT, 0.8)
        self.assertEqual(drain(inp), [(inputs.LT, False)])

    def test_raw_axis_needs_rest_first(self):
        inp = inputs.Input()
        inp.joys[7] = object()
        ev = lambda v: FakeEvent(pygame.JOYAXISMOTION, instance_id=7, axis=5, value=v)
        inp.handle(ev(0.0))     # SDL's first report before any motion: not a half pull
        inp.handle(ev(0.9))
        self.assertEqual(drain(inp), [])
        inp.handle(ev(-1.0))
        inp.handle(ev(0.9))     # (0.9 + 1) / 2 = 0.95
        self.assertEqual(drain(inp), [(inputs.RT, False)])

    def test_raw_stick_axis_is_not_a_trigger(self):
        # A DInput right stick on axis 5 rests at 0 and swings both ways.
        inp = inputs.Input()
        inp.joys[7] = object()
        ev = lambda v: FakeEvent(pygame.JOYAXISMOTION, instance_id=7, axis=5, value=v)
        for v in (0.0, -0.4, 0.0, 0.8, 0.0, -0.6, 0.9):
            inp.handle(ev(v))
        self.assertEqual(drain(inp), [])

    def test_keyboard_brackets(self):
        inp = inputs.Input()
        inp.handle(FakeEvent(pygame.KEYDOWN, key=pygame.K_RIGHTBRACKET, mod=0))
        self.assertEqual(inp.pop(), inputs.RT)


class TestText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def key(self, inp, k, u="", mod=0):
        inp.handle(FakeEvent(pygame.KEYDOWN, key=k, mod=mod, unicode=u))
        return [a for a, _ in drain(inp)]

    def test_text_mode_types(self):
        inp = inputs.Input()
        inp.text = True
        for k, c in ((pygame.K_a, "a"), (pygame.K_x, "x"), (pygame.K_y, "y"), (pygame.K_m, "m"),
                     (pygame.K_q, "q"), (pygame.K_e, "e"), (pygame.K_SPACE, " "),
                     (pygame.K_5, "5"), (pygame.K_KP7, "7")):
            self.assertEqual(self.key(inp, k), ["char:" + c])
        self.assertEqual(self.key(inp, pygame.K_a, "A", pygame.KMOD_LSHIFT), ["char:a"])
        self.assertEqual(self.key(inp, pygame.K_1, "!", pygame.KMOD_LSHIFT), ["char:!"])
        self.assertEqual(self.key(inp, pygame.K_QUOTE, "'"), ["char:'"])
        self.assertEqual(self.key(inp, pygame.K_RIGHTBRACKET, "]"), ["char:]"])
        self.assertEqual(self.key(inp, pygame.K_a, "a", pygame.KMOD_LCTRL), [])
    def test_altgr_types(self):
        inp = inputs.Input()
        inp.text = True
        self.assertEqual(self.key(inp, pygame.K_q, "@", pygame.KMOD_RALT), ["char:@"])
        self.assertEqual(self.key(inp, pygame.K_2, "@", pygame.KMOD_RALT | pygame.KMOD_LSHIFT),
                         ["char:@"])
        self.assertEqual(self.key(inp, pygame.K_q, "q", pygame.KMOD_LALT), [inputs.LB])


    def test_text_mode_keeps_navigation(self):
        inp = inputs.Input()
        inp.text = True
        for k, a in ((pygame.K_LEFT, inputs.LEFT), (pygame.K_RETURN, inputs.OK_DOWN),
                     (pygame.K_ESCAPE, inputs.BACK), (pygame.K_BACKSPACE, inputs.BACKSPACE),
                     (pygame.K_PAGEDOWN, inputs.RB)):
            self.assertEqual(self.key(inp, k), [a])
            inp.handle(FakeEvent(pygame.KEYUP, key=k, mod=0))
            drain(inp)                                   # OK_UP

    def test_text_off_is_unchanged(self):
        inp = inputs.Input()
        self.assertEqual(self.key(inp, pygame.K_x, "x"), [inputs.X])
        self.assertEqual(self.key(inp, pygame.K_SPACE, " "), [inputs.MENU])
        self.assertEqual(self.key(inp, pygame.K_5, "5"), ["d5"])
        self.assertEqual(self.key(inp, pygame.K_QUOTE, "'"), [])

    def test_activity(self):
        inp = inputs.Input()
        self.assertFalse(inp.take_activity())
        inp.handle(FakeEvent(pygame.KEYDOWN, key=pygame.K_F1, mod=0, unicode=""))  # unmapped
        self.assertTrue(inp.take_activity())
        self.assertFalse(inp.take_activity())
        inp.inject("ok")
        self.assertTrue(inp.take_activity())



XBOX_CFG = """<?xml version="1.0"?>
<inputList>
  <inputConfig type="keyboard" deviceName="Keyboard" deviceGUID="-1">
    <input name="a" type="key" id="13" value="1" />
  </inputConfig>
  <inputConfig type="joystick" deviceName="Microsoft X-Box One pad, wired"
               deviceGUID="060000005e040000120b000009050000">
    <input name="a" type="button" id="2" value="1" />
    <input name="b" type="button" id="1" value="1" />
    <input name="down" type="hat" id="0" value="4" />
    <input name="hotkey" type="button" id="9" value="1" />
    <input name="joystick1left" type="axis" id="0" value="-1" />
    <input name="l2" type="axis" id="2" value="1" />
    <input name="select" type="button" id="7" value="1" />
    <input name="start" type="button" id="8" value="1" />
  </inputConfig>
</inputList>
"""


class TestPadMaps(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def cfg(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_es_pad_becomes_an_sdl_map(self):
        lines = inputs.es_mappings([self.cfg("es.cfg", XBOX_CFG)])
        self.assertEqual(len(lines), 1)                       # the keyboard is not a pad
        parts = lines[0].split(",")
        self.assertEqual(parts[:3], ["060000005e040000120b000009050000",
                                     "Microsoft X-Box One pad. wired", "platform:Linux"])
        for p in ("a:b1", "b:b2", "dpdown:h0.4", "leftx:a0", "lefttrigger:a2", "back:b7",
                  "start:b8", "guide:b9"):
            self.assertIn(p, parts)

    def test_user_file_wins_and_missing_files_are_skipped(self):
        user = self.cfg("user.cfg", XBOX_CFG.replace('id="1" value="1" />', 'id="5" value="1" />'))
        lines = inputs.es_mappings([os.path.join(self.dir, "none.cfg"), user,
                                    self.cfg("sys.cfg", XBOX_CFG)])
        self.assertEqual(len(lines), 1)
        self.assertIn("a:b5", lines[0].split(","))

    def test_maps_are_set_while_the_pads_open_and_then_removed(self):
        from unittest import mock
        seen = []
        old = inputs.ES_INPUT
        inputs.ES_INPUT = (self.cfg("es.cfg", XBOX_CFG),)
        try:
            with mock.patch.object(pygame.joystick, "init",
                                   lambda: seen.append(os.environ.get(inputs.SDL_MAP_VAR))):
                with mock.patch.dict(os.environ, {inputs.SDL_MAP_VAR: "LAUNCHER"}):
                    inputs.Input(clock=Clock())
                    self.assertEqual(os.environ[inputs.SDL_MAP_VAR], "LAUNCHER")
                os.environ.pop(inputs.SDL_MAP_VAR, None)
                inputs.Input(clock=Clock())
                self.assertNotIn(inputs.SDL_MAP_VAR, os.environ)
        finally:
            inputs.ES_INPUT = old
        first = seen[0].split("\n")
        self.assertTrue(first[0].startswith("060000005e040000120b000009050000,"))
        self.assertEqual(first[-1], "LAUNCHER")               # the launcher's maps win
        self.assertEqual(len(seen[1].split("\n")), 1)

    def test_a_bad_value_skips_only_that_pad(self):
        bad = XBOX_CFG.replace('id="0" value="-1"', 'id="0" value="x"').replace(
            "060000005e040000120b000009050000", "0300bad")
        lines = inputs.es_mappings([self.cfg("bad.cfg", bad), self.cfg("es.cfg", XBOX_CFG)])
        self.assertEqual([l.split(",")[0] for l in lines], ["060000005e040000120b000009050000"])


if __name__ == "__main__":
    unittest.main()
