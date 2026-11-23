# Contributing

Bug reports, hardware reports, and pull requests are welcome.

## Run it on a desktop

You need Python 3.9+, pygame 2, and mpv.

```sh
python3 -m batoplex --window   # sign in to your own server
```

Keyboard: arrow keys, Enter (select), Escape or Backspace (back), Space (menu).
Desktop settings and the sign-in token go to `~/.batoplex/` (or `$BATOPLEX_HOME`).

To run it without a Plex account, use the mock server from the tests:

```sh
sh tools/make-test-media.sh   # once, for playable clips (needs ffmpeg)
python3 test/mock_plex.py 32499 &
export BATOPLEX_HOME=~/.batoplex-mock
mkdir -p $BATOPLEX_HOME
echo '{"server_token":"mock-token-123456","server_url":"http://127.0.0.1:32499","server_name":"Mock"}' > $BATOPLEX_HOME/auth.json
python3 -m batoplex --window
```

Movies and episodes play. Music needs the tone files that `test/run.sh` makes. Port 32499
keeps clear of a Plex server on the same machine, and `BATOPLEX_HOME` keeps your own sign-in.

## Tests

```sh
sh test/run.sh
```

This builds the install bundle, runs the unit tests, and runs two Docker tests at 641x480
under Xvfb:

- `test/e2e.sh` runs the app against a mock Plex server (`test/mock_plex.py`) with real mpv
  playback and progress reporting.
- `test/install_test.sh` installs the bundle into a fake Batocera layout, launches it from the
  Ports script, exits it the ways Batocera does, and uninstalls it.

It needs Docker, and ffmpeg the first time (to make the test clips with
`tools/make-test-media.sh`). Screenshots and logs go to `test/out/`.

To run only the unit tests, without Docker (needs pygame):

```sh
python3 -m unittest discover -s test/unit
```

## Design rules

The interface is drawn for a 640x480 canvas shown as 480i on a CRT. Keep to these rules or
it will flicker or shimmer on a real set:

- Put horizontal edges and text on even rows. Use `draw.py` helpers, which snap for you.
- Make horizontal lines 2 to 4 px thick. A 1 px line flickers at 30 Hz.
- Keep everything inside the safe area (`tokens.SAFE`, set by the user in Screen Margins).
- Use the colors, sizes, and spacing in `tokens.py`.

Please check UI changes on a real CRT if you can, and include a photo in the pull request.

## Pull requests

- Keep each pull request to one change.
- Run `sh test/run.sh` before you open it.
- New dependencies must already ship with Batocera. The app uses only the Python standard
  library, pygame, and the mpv binary.

## AI use

AI tools are fine; much of this project is written with Claude Code. You must understand, run and
test what you submit, and say in the pull request what the AI did. Write the description and
your replies in your own words.
