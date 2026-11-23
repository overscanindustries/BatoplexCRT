import os
import tempfile
import unittest

from batoplex import boot

L = "/userdata/roms/ports/Batoplex.sh"


class TestStartOnBoot(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.conf = os.path.join(d, "batocera.conf")
        self.launcher = os.path.join(d, "Batoplex.sh")
        open(self.launcher, "w").close()

    def write(self, text):
        with open(self.conf, "w") as f:
            f.write(text)
        os.chmod(self.conf, 0o644)

    def read(self):
        with open(self.conf) as f:
            return f.read()

    def test_on_then_off_keeps_other_lines(self):
        self.write("# comment\nglobal.videooutput=DVI-I-1\nsystem.hostname=BATOCERA\n")
        self.assertTrue(boot.available(self.conf, self.launcher))
        self.assertFalse(boot.enabled(self.conf, self.launcher))
        self.assertTrue(boot.set_enabled(True, self.conf, self.launcher))
        self.assertTrue(boot.enabled(self.conf, self.launcher))
        text = self.read()
        self.assertIn("global.bootgame.path=%s\n" % self.launcher, text)
        self.assertIn("global.bootgame.cmd=emulatorlauncher %%CONTROLLERSCONFIG%% -system ports "
                      "-rom %s -systemname Ports\n" % self.launcher, text)
        self.assertTrue(text.startswith("# comment\nglobal.videooutput=DVI-I-1\n"))
        self.assertEqual(os.stat(self.conf).st_mode & 0o777, 0o644)
        self.assertTrue(boot.set_enabled(False, self.conf, self.launcher))
        self.assertEqual(self.read(),
                         "# comment\nglobal.videooutput=DVI-I-1\nsystem.hostname=BATOCERA\n")

    def test_on_replaces_another_boot_game(self):
        self.write("global.bootgame.path=/userdata/roms/snes/a.sfc\nglobal.bootgame.cmd=x\n")
        boot.set_enabled(True, self.conf, self.launcher)
        text = self.read()
        self.assertNotIn("a.sfc", text)
        self.assertEqual(text.count("global.bootgame.path="), 1)
        self.assertEqual(text.count("global.bootgame.cmd="), 1)

    def test_off_leaves_another_boot_game(self):
        before = "global.bootgame.path=/userdata/roms/snes/a.sfc\nglobal.bootgame.cmd=x\n"
        self.write(before)
        self.assertTrue(boot.set_enabled(False, self.conf, self.launcher))
        self.assertEqual(self.read(), before)

    def test_missing_conf(self):
        self.assertFalse(boot.available(self.conf, self.launcher))
        self.assertFalse(boot.enabled(self.conf, self.launcher))
        self.assertFalse(boot.set_enabled(True, self.conf, self.launcher))


class TestSettingsRow(unittest.TestCase):
    def test_row_shows_and_toggles(self):
        from unittest import mock
        from test_bands import SettingsScreen, fake_app
        from batoplex import inputs
        from batoplex.screens.system import SettingsPage
        app = fake_app()
        results = []
        app.saved = lambda ok: results.append(ok) or ok
        writes = []
        with mock.patch.object(boot, "available", lambda: True), \
                mock.patch.object(boot, "enabled", lambda: False), \
                mock.patch.object(boot, "set_enabled", lambda on: writes.append(on) or True):
            pages = []
            app.push = lambda scr, panel=None: pages.append(scr)
            top = SettingsScreen(app)                 # TV Setup holds the row
            top.focus = [r[1] for r in top.items()].index("cat:tvsetup")
            top.handle(inputs.OK)
            scr = pages[-1]
            self.assertIsInstance(scr, SettingsPage)
            items = scr.items()
            scr.focus = [r[1] for r in items].index("boot")
            self.assertEqual(items[scr.focus][2:], ("Start on Boot", "Off"))
            import pygame
            pygame.font.init()
            scr.draw(app.canvas)
            scr.handle(inputs.LEFT)                # already Off: no write
            scr.handle(inputs.OK)
            scr.handle(inputs.RIGHT)               # already On
            self.assertEqual(writes, [True])
            self.assertEqual(scr.items()[scr.focus][3], "On")
        with mock.patch.object(boot, "set_enabled", lambda on: False):
            scr.handle(inputs.OK)                  # a failed write keeps the shown state
        self.assertEqual(scr.items()[scr.focus][3], "On")
        self.assertEqual(results[-1], False)


if __name__ == "__main__":
    unittest.main()
