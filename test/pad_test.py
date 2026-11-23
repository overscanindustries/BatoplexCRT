"""Virtual Xbox One pad (uinput, VID 045e PID 02ea) so SDL uses its GameController mapping.
Drives: Right, Down, A (open detail), A (play), B (stop), B (back), then Back+Start (quit)."""
import subprocess, sys, time
from evdev import UInput, ecodes as e, AbsInfo
cap = {e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR,
                  e.BTN_SELECT, e.BTN_START, e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
       e.EV_ABS: [(e.ABS_X, AbsInfo(0, -32768, 32767, 16, 128, 0)), (e.ABS_Y, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                  (e.ABS_RX, AbsInfo(0, -32768, 32767, 16, 128, 0)), (e.ABS_RY, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                  (e.ABS_Z, AbsInfo(0, 0, 1023, 0, 0, 0)), (e.ABS_RZ, AbsInfo(0, 0, 1023, 0, 0, 0)),
                  (e.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)), (e.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0))]}
ui = UInput(cap, name="Microsoft X-Box One S pad", vendor=0x045e, product=0x02ea, version=0x0301, bustype=e.BUS_USB)
time.sleep(1)
app = subprocess.Popen([sys.executable, "-m", "batoplex"], cwd="/app")
def btn(code, hold=0.08):
    ui.write(e.EV_KEY, code, 1); ui.syn(); time.sleep(hold); ui.write(e.EV_KEY, code, 0); ui.syn()
def hat(axis, v):
    ui.write(e.EV_ABS, axis, v); ui.syn(); time.sleep(0.08); ui.write(e.EV_ABS, axis, 0); ui.syn()
def shot(name):
    subprocess.run("xwd -root -silent | xwdtopnm 2>/dev/null | pnmtopng > /out/pad-%s.png" % name, shell=True)
time.sleep(5); shot("1-start")
hat(e.ABS_HAT0Y, 1); time.sleep(0.5)          # down -> next row
hat(e.ABS_HAT0X, 1); time.sleep(0.6)          # right
shot("2-moved")
btn(e.BTN_SOUTH); time.sleep(1.5); shot("3-detail")   # A -> detail
btn(e.BTN_SOUTH); time.sleep(4); shot("4-playing")    # A -> play (background pad events)
btn(e.BTN_EAST); time.sleep(3); shot("5-after-stop")  # B -> stop, back in UI
btn(e.BTN_EAST); time.sleep(1.5); shot("6-home")      # B -> home
ui.write(e.EV_KEY, e.BTN_SELECT, 1); ui.write(e.EV_KEY, e.BTN_START, 1); ui.syn(); time.sleep(0.2)
ui.write(e.EV_KEY, e.BTN_SELECT, 0); ui.write(e.EV_KEY, e.BTN_START, 0); ui.syn()
try:
    app.wait(6); print("app exited on View+Menu, code", app.returncode)
except subprocess.TimeoutExpired:
    print("app did not exit"); app.kill()
