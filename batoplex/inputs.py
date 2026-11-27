"""Controller, keyboard and remote input -> actions, with D-pad repeat.

Pads: SDL GameController API (standard layout), with the pad maps of EmulationStation, so a
pad that wakes after the start is mapped too. Other pads: raw joystick fallback.
Keyboard: arrows, Enter, Escape/Backspace (USB air-mouse remotes send Escape for Back and
Space for Options). The app keeps reading the pad while mpv plays, because
SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1 is set before pygame starts.
"""
import collections
import os
import time
import xml.etree.ElementTree as ET

import pygame

from . import log, tokens

L = log.get("input")

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
OK, BACK, MENU, X, Y, LB, RB, QUIT = "ok", "back", "menu", "x", "y", "lb", "rb", "quit"
BACKSPACE = "backspace"     # Back everywhere, except on a screen with takes_backspace (PIN pad)
# A real OK button gives OK_DOWN and OK_UP; the app turns them into OK (a short
# press, on release) or HOLD (0.5 s), or OK at once on a screen with ok_on_press and in playback.
# A script token "ok" (inject) stays a finished OK.
OK_DOWN, OK_UP, HOLD = "ok_down", "ok_up", "hold"
LT, RT = "lt", "rt"
DIRS = (UP, DOWN, LEFT, RIGHT)

try:
    from pygame._sdl2 import controller as sdlc
except Exception:  # pragma: no cover
    sdlc = None

KEYS = {
    pygame.K_UP: UP, pygame.K_DOWN: DOWN, pygame.K_LEFT: LEFT, pygame.K_RIGHT: RIGHT,
    pygame.K_RETURN: OK, pygame.K_KP_ENTER: OK, pygame.K_ESCAPE: BACK,
    pygame.K_BACKSPACE: BACKSPACE,
    pygame.K_SPACE: MENU, pygame.K_m: MENU, pygame.K_x: X, pygame.K_y: Y,
    pygame.K_PAGEUP: LB, pygame.K_PAGEDOWN: RB, pygame.K_q: LB, pygame.K_e: RB,
    pygame.K_LEFTBRACKET: LT, pygame.K_RIGHTBRACKET: RT,
}
# Number keys (USB remotes, keyboards): the PIN pad types them.
for _n in range(10):
    KEYS[getattr(pygame, "K_%d" % _n)] = "d%d" % _n
    KEYS[getattr(pygame, "K_KP%d" % _n)] = "d%d" % _n
# USB remotes and media keyboards: media keys (names are SDL keycodes; some pygame builds
# lack a few, so they are looked up by name).
for _name, _act in (("K_AUDIOPLAY", "playpause"), ("K_AUDIOSTOP", "stop"),
                    ("K_AUDIONEXT", "next"), ("K_AUDIOPREV", "prev"),
                    ("K_AUDIOREWIND", LEFT), ("K_AUDIOFASTFORWARD", RIGHT),
                    ("K_AC_BACK", BACK), ("K_MENU", MENU), ("K_AC_HOME", BACK)):
    if hasattr(pygame, _name):
        KEYS[getattr(pygame, _name)] = _act

# Text mode (a screen with takes_text, such as Search): these keys type instead of their
# actions. The character comes from the key code, so no IME is needed.
TEXT_KEYS = {getattr(pygame, "K_%s" % c): c for c in "abcdefghijklmnopqrstuvwxyz0123456789"}
TEXT_KEYS.update({getattr(pygame, "K_KP%d" % n): str(n) for n in range(10)})
TEXT_KEYS[pygame.K_SPACE] = " "
CHAR = "char:"              # action prefix: "char:a"
TEXT_KEEP = set(DIRS) | {OK, BACK, BACKSPACE, LB, RB, "playpause", "stop", "next", "prev"}

# EmulationStation pad maps: the user file first, then the system file. Batocera's SDL has no
# built-in pad list, and the launcher passes maps only for the pads that are awake at the start.
ES_INPUT = ("/userdata/system/configs/emulationstation/es_input.cfg",
            "/usr/share/emulationstation/es_input.cfg")
ES_TO_SDL = {"b": "a", "a": "b", "x": "y", "y": "x", "l2": "lefttrigger", "r2": "righttrigger",
             "l3": "leftstick", "r3": "rightstick", "pageup": "leftshoulder",
             "pagedown": "rightshoulder", "start": "start", "select": "back", "up": "dpup",
             "down": "dpdown", "left": "dpleft", "right": "dpright", "joystick1up": "lefty",
             "joystick1left": "leftx", "joystick2up": "righty", "joystick2left": "rightx",
             "hotkey": "guide"}
SDL_MAP_VAR = "SDL_GAMECONTROLLERCONFIG"


def _sdl_part(name, i):
    """One ES input as an SDL mapping part (the same rules as Batocera's configgen)."""
    key, t, n, v = ES_TO_SDL.get(name), i.get("type"), i.get("id"), i.get("value") or "0"
    if key is None or n is None:
        return None
    if t == "button":
        return "%s:b%s" % (key, n)
    if t == "hat":
        return "%s:h%s.%s" % (key, n, v)
    if t == "axis":
        if "joystick" in name:
            return "%s:a%s%s" % (key, n, "~" if int(v) > 0 else "")
        if key.startswith("dp"):
            return "%s:%sa%s" % (key, "-" if int(v) < 0 else "+", n)
        if "trigger" in key:
            return "%s:a%s%s" % (key, n, "~" if int(v) < 0 else "")
        return "%s:a%s" % (key, n)
    return None


def es_mappings(paths=None):
    """SDL mapping lines for every pad in the EmulationStation files, one for each GUID."""
    lines, seen = [], set()
    for p in ES_INPUT if paths is None else paths:
        try:
            root = ET.parse(p).getroot()
        except (OSError, ET.ParseError):
            continue
        for c in root.iter("inputConfig"):
            guid = c.get("deviceGUID")
            if c.get("type") != "joystick" or not guid or guid in seen:
                continue
            try:
                line = _es_line(c, guid)
            except (TypeError, ValueError):          # a bad value: skip this pad, not the app
                continue
            seen.add(guid)
            lines.append(line)
    return lines


def _es_line(c, guid):
    """One pad as an SDL mapping line."""
    parts = [guid, (c.get("deviceName") or "Pad").replace(",", "."), "platform:Linux"]
    buttons, hotkey = set(), None
    for i in c.iter("input"):
        if i.get("name") == "hotkey":
            hotkey = i
            continue
        part = _sdl_part(i.get("name"), i)
        if part:
            parts.append(part)
            if i.get("type") == "button":
                buttons.add(i.get("id"))
    # A hotkey on a button that has a use already confuses SDL apps (configgen).
    if hotkey is not None and hotkey.get("id") not in buttons:
        part = _sdl_part("hotkey", hotkey)
        if part:
            parts.append(part)
    return ",".join(parts) + ","


# Raw joystick fallback (xpad numbering; Batocera's SDL numbers buttons differently, so a pad
# without a map can read A as Back)
JOY_BUTTONS = {0: OK, 1: BACK, 2: X, 3: Y, 4: LB, 5: RB, 6: "select", 7: MENU}
JOY_TRIGGERS = {2: LT, 5: RT}   # xpad: rest -1, full +1

# Trigger thresholds on a 0..1 scale (hysteresis, so a half pull does not chatter).
TRIG_ON, TRIG_OFF = 0.5, 0.3
TRIG_REST = -0.9                 # raw axis value that proves an axis is a trigger at rest


class Input:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.queue = collections.deque()   # (action, is_repeat)
        self.held = {}            # dir -> time of the next repeat (s)
        self.down = set()         # every action whose button is down now
        self.trig = {}            # (instance, axis) -> pressed
        self.trig_seen_rest = set()  # raw axes that have reported a value below 0
        self.controllers = {}
        self.joys = {}
        self.axis_dir = {}        # (instance, axis) -> dir
        self.select_down = False
        self.start_down = False
        self._src = "kbd"         # the device of the event in handle(): OK_UP needs the same one
        self.ok_src = None
        self.flushes = 0          # counts flush(): a pending OK from before one is dropped
        self.text = False         # set by the app each frame: the top screen takes text
        self.activity = False     # any button or key went down (the screensaver reads it)
        # SDL reads the maps when the pads open. The launcher's maps go last, so they win.
        # The variable goes back after that: it is large, and mpv does not need it.
        given = os.environ.get(SDL_MAP_VAR)
        maps = es_mappings()
        if maps:
            os.environ[SDL_MAP_VAR] = "\n".join(maps + ([given] if given else []))
            L.info("pad maps: %d from EmulationStation", len(maps))
        try:
            if sdlc:
                try:
                    sdlc.init()
                except Exception as e:
                    L.info("controller init failed: %s", e)
            pygame.joystick.init()
        finally:
            if given is None:
                os.environ.pop(SDL_MAP_VAR, None)
            else:
                os.environ[SDL_MAP_VAR] = given
        self.scan()

    def scan(self):
        for i in range(pygame.joystick.get_count()):
            try:
                jid = pygame.joystick.Joystick(i).get_instance_id()
                if jid in self.controllers or jid in self.joys:
                    continue
                if sdlc and sdlc.is_controller(i):
                    c = sdlc.Controller(i)
                    self.controllers[jid] = c
                    L.info("controller %d: %s (game controller)", i, c.name)
                else:
                    j = pygame.joystick.Joystick(i)
                    j.init()
                    self.joys[j.get_instance_id()] = j
                    L.info("joystick %d: %s (raw)", i, j.get_name())
            except Exception as e:
                L.info("pad %d failed: %s", i, e)

    def inject(self, action):
        self.activity = True
        self.queue.append((action, False))

    def take_activity(self):
        a, self.activity = self.activity, False
        return a

    def _text_char(self, e):
        """The character a KEYDOWN types in text mode, or None (the key keeps its action)."""
        mod = getattr(e, "mod", 0)
        if mod & (pygame.KMOD_CTRL | pygame.KMOD_LALT):    # AltGr (right Alt) still types
            return None
        u = getattr(e, "unicode", "") or ""
        u = u if len(u) == 1 and u.isprintable() else ""
        if e.key in TEXT_KEYS:
            if u and mod & pygame.KMOD_RALT:
                return u                       # AltGr: "@" on a German keyboard
            return u.lower() if (mod & pygame.KMOD_SHIFT and u) else TEXT_KEYS[e.key]
        if KEYS.get(e.key) in TEXT_KEEP:
            return None
        return u or None

    def _press(self, a):
        if a == OK:
            if OK in self.down:               # a second device, or a key repeat: one press
                return
            self.down.add(OK)
            self.ok_src = self._src
            self.queue.append((OK_DOWN, False))
            self.activity = True
            return
        if a in DIRS:
            if a not in self.held:
                self.held[a] = self.clock() + tokens.REPEAT_DELAY_MS / 1000.0
                self.queue.append((a, False))
        else:
            self.queue.append((a, False))
        self.down.add(a)
        self.activity = True

    def _release(self, a):
        if a == OK:                           # only the device that pressed it
            if OK in self.down and self._src == self.ok_src:
                self.down.discard(OK)
                self.ok_src = None
                self.queue.append((OK_UP, False))
            return
        self.held.pop(a, None)
        self.down.discard(a)

    def is_held(self, a):
        return a in self.down

    def flush(self):
        """Drop queued presses (on a playback state change). Held buttons stay held,
        but do not repeat until released and pressed again."""
        self.queue.clear()
        self.held = {d: float("inf") for d in self.held}
        self.flushes += 1

    def _combo(self, which, down):
        if which == "select":
            self.select_down = down
        else:
            self.start_down = down
        if self.select_down and self.start_down:
            self.queue.append((QUIT, False))
            return True
        return False

    def handle(self, e):
        t = e.type
        self._src = getattr(e, "instance_id", None)
        if self._src is None:
            self._src = "kbd"
        # A hat press is activity through _press; its return to neutral is not
        # (it woke the Aquarium Preview between two rare moments).
        if t in (pygame.KEYDOWN, pygame.JOYBUTTONDOWN) or (
                sdlc and t == pygame.CONTROLLERBUTTONDOWN):
            self.activity = True
        if t == pygame.KEYDOWN:
            c = self._text_char(e) if self.text else None
            if e.key == pygame.K_F4 and e.mod & pygame.KMOD_ALT:
                self.queue.append((QUIT, False))
            elif c is not None:
                self.queue.append((CHAR + c, False))
            elif e.key in KEYS:
                self._press(KEYS[e.key])
        elif t == pygame.KEYUP and e.key in KEYS:
            self._release(KEYS[e.key])
        elif sdlc and t == pygame.CONTROLLERBUTTONDOWN:
            self._cbutton(e.button, True)
        elif sdlc and t == pygame.CONTROLLERBUTTONUP:
            self._cbutton(e.button, False)
        elif sdlc and t == pygame.CONTROLLERAXISMOTION and e.axis in (
                pygame.CONTROLLER_AXIS_TRIGGERLEFT, pygame.CONTROLLER_AXIS_TRIGGERRIGHT):
            a = LT if e.axis == pygame.CONTROLLER_AXIS_TRIGGERLEFT else RT
            self._trigger(e.instance_id, e.axis, a, e.value / 32767.0)
        elif sdlc and t == pygame.CONTROLLERAXISMOTION:
            self._axis(e.instance_id, e.axis, e.value / 32767.0,
                       {pygame.CONTROLLER_AXIS_LEFTX: (LEFT, RIGHT),
                        pygame.CONTROLLER_AXIS_LEFTY: (UP, DOWN)})
        elif t == pygame.JOYBUTTONDOWN and e.instance_id in self.joys:
            a = JOY_BUTTONS.get(e.button)
            if a == "select":
                if not self._combo("select", True):
                    self._press(BACK)
            elif a == MENU:
                if not self._combo("start", True):
                    self._press(MENU)
            elif a:
                self._press(a)
        elif t == pygame.JOYBUTTONUP and e.instance_id in self.joys:
            a = JOY_BUTTONS.get(e.button)
            if a == "select":
                self._combo("select", False)
                self._release(BACK)
            elif a == MENU:
                self._combo("start", False)
                self._release(MENU)
            elif a:
                self._release(a)
        elif t == pygame.JOYHATMOTION and e.instance_id in self.joys:
            hx, hy = e.value
            for d, on in ((LEFT, hx < 0), (RIGHT, hx > 0), (UP, hy > 0), (DOWN, hy < 0)):
                (self._press if on else self._release)(d)
        elif t == pygame.JOYAXISMOTION and e.instance_id in self.joys and e.axis in JOY_TRIGGERS:
            key = (e.instance_id, e.axis)
            if e.value <= TRIG_REST:          # a real trigger rests at -1; a stick rests at 0
                self.trig_seen_rest.add(key)
            if key in self.trig_seen_rest:   # SDL reports 0 before the first motion
                self._trigger(e.instance_id, e.axis, JOY_TRIGGERS[e.axis], (e.value + 1) / 2.0)
        elif t == pygame.JOYAXISMOTION and e.instance_id in self.joys and e.axis in (0, 1):
            self._axis(e.instance_id, e.axis, e.value, {0: (LEFT, RIGHT), 1: (UP, DOWN)})
        elif t in (pygame.JOYDEVICEADDED, getattr(pygame, "CONTROLLERDEVICEADDED", -1)):
            self.scan()
        elif t in (pygame.JOYDEVICEREMOVED, getattr(pygame, "CONTROLLERDEVICEREMOVED", -1)):
            # A wireless pad that drops out must not leave a direction held.
            self.held.clear()
            self.down.clear()
            self.trig.clear()
            self.axis_dir.clear()
            self.select_down = self.start_down = False
            inst = getattr(e, "instance_id", None)
            self.controllers.pop(inst, None)
            self.joys.pop(inst, None)

    def _cbutton(self, b, down):
        m = {pygame.CONTROLLER_BUTTON_A: OK, pygame.CONTROLLER_BUTTON_B: BACK,
             pygame.CONTROLLER_BUTTON_X: X, pygame.CONTROLLER_BUTTON_Y: Y,
             pygame.CONTROLLER_BUTTON_LEFTSHOULDER: LB, pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: RB,
             pygame.CONTROLLER_BUTTON_DPAD_UP: UP, pygame.CONTROLLER_BUTTON_DPAD_DOWN: DOWN,
             pygame.CONTROLLER_BUTTON_DPAD_LEFT: LEFT, pygame.CONTROLLER_BUTTON_DPAD_RIGHT: RIGHT}
        if b == pygame.CONTROLLER_BUTTON_BACK:
            if down and not self._combo("select", True):
                self._press(BACK)
            elif not down:
                self._combo("select", False)
                self._release(BACK)
            return
        if b == pygame.CONTROLLER_BUTTON_START:
            if down and not self._combo("start", True):
                self._press(MENU)
            elif not down:
                self._combo("start", False)
                self._release(MENU)
            return
        a = m.get(b)
        if a:
            (self._press if down else self._release)(a)

    def _trigger(self, inst, axis, a, v):
        key = (inst, axis)
        on = self.trig.get(key, False)
        if not on and v > TRIG_ON:
            self.trig[key] = True
            self._press(a)
        elif on and v < TRIG_OFF:
            self.trig[key] = False
            self._release(a)

    def _axis(self, inst, axis, v, mapping):
        neg, pos = mapping.get(axis, (None, None))
        if neg is None:
            return
        key = (inst, axis)
        cur = self.axis_dir.get(key)
        new = neg if v < -0.6 else pos if v > 0.6 else None
        if abs(v) > 0.35 and cur and new is None:
            return  # hysteresis
        if new != cur:
            if cur:
                self._release(cur)
            if new:
                self._press(new)
            self.axis_dir[key] = new

    def tick(self):
        """Call once per frame. Adds repeats for held directions by wall time, so the
        repeat rate is the same at 30 and 60 frames a second."""
        now = self.clock()
        every = tokens.REPEAT_EVERY_MS / 1000.0
        for d, due in list(self.held.items()):
            n = 0
            while now >= due and n < 3:   # catch up after a slow frame, but never flood
                self.queue.append((d, True))
                due += every
                n += 1
            if now >= due:
                due = now + every
            self.held[d] = due

    def pop_event(self):
        """(action, is_repeat), or None."""
        return self.queue.popleft() if self.queue else None

    def pop(self):
        ev = self.pop_event()
        return ev[0] if ev else None
