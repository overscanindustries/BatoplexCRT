"""Start on Boot: Batocera's own boot game, the "Launch this game at startup" switch of ES.

ES (main.cpp launchStartupGame) runs global.bootgame.cmd from batocera.conf before its window
opens, when the file at global.bootgame.path exists. emulationstation-standalone passes
--no-startup-game on every later ES start in the same boot, so exit returns to the ES menu.
ES saves only the batocera.conf keys it changed itself, so an edit here is kept.
The command is what ES writes for a Ports game (FileData::getlaunchCommand), without the
-gameinfoxml file that only exists while ES runs."""
import os
import tempfile

CONF = "/userdata/system/batocera.conf"
LAUNCHER = "/userdata/roms/ports/Batoplex.sh"
PATH_KEY = "global.bootgame.path"
CMD_KEY = "global.bootgame.cmd"


def command(launcher=LAUNCHER):
    return "emulatorlauncher %%CONTROLLERSCONFIG%% -system ports -rom %s -systemname Ports" % launcher


def available(conf=CONF, launcher=LAUNCHER):
    """Only on a Batocera box with the Ports launcher installed."""
    return os.path.isfile(conf) and os.path.isfile(launcher)


def _lines(conf):
    with open(conf, encoding="utf-8", errors="surrogateescape") as f:
        return f.read().splitlines()


def _value(lines, key):
    val = None
    for ln in lines:
        if ln.startswith(key + "="):
            val = ln[len(key) + 1:]
    return val


def enabled(conf=CONF, launcher=LAUNCHER):
    try:
        return _value(_lines(conf), PATH_KEY) == launcher
    except OSError:
        return False


def set_enabled(on, conf=CONF, launcher=LAUNCHER):
    """On: Batoplex becomes the boot game (ES has one; this replaces another one).
    Off: removes the boot game only when it is Batoplex. Returns False if the write failed."""
    try:
        lines = _lines(conf)
        if not on and _value(lines, PATH_KEY) != launcher:
            return True
        keep = [ln for ln in lines
                if not ln.startswith(PATH_KEY + "=") and not ln.startswith(CMD_KEY + "=")]
        if on:
            keep += [PATH_KEY + "=" + launcher, CMD_KEY + "=" + command(launcher)]
        real = os.path.realpath(conf)
        mode = os.stat(real).st_mode & 0o7777
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".batocera.conf.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write("\n".join(keep) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, real)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False
