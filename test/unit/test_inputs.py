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


if __name__ == "__main__":
    unittest.main()
