# AGENTS.md

BatoplexCRT is a Plex client for CRT TVs on Batocera: Python and pygame draw a 640x480
interface for 480i, and mpv plays the video.

## Design rules

The full text is in [CONTRIBUTING.md](CONTRIBUTING.md#design-rules). In short:

- Horizontal edges and text go on even rows. The `draw.py` helpers snap for you.
- Horizontal lines are 2 to 4 px thick. A 1 px line flickers.
- Everything stays inside the safe area (`tokens.SAFE`).
- Colors, sizes and spacing come from `tokens.py`.

## Tests

- Unit tests: `python3 -m unittest discover -s test/unit` (needs pygame).
- Full suite: `sh test/run.sh` (needs Docker). Run it before you open a pull request.

## Rules

- Dependencies: the Python standard library, pygame and the mpv binary. Nothing else.
- Keep each pull request to one change.
- Comments explain the code. Do not cite private notes or tickets.
