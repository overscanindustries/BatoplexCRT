# Install and remove

Batoplex runs on Batocera 43 (x86_64). It installs as a Port and writes only to `/userdata`.
It does not touch `/boot`, `/usr`, `/etc`, the overlay, or your video mode settings.

You need SSH access to the Batocera PC (user `root`; the password is in
**Main Menu > System Settings > Security**).

## Install

1. Download `batoplex-<version>.tar.gz` from the
   [latest release](https://github.com/overscanindustries/BatoplexCRT/releases/latest).
2. Copy it to the Batocera PC:

   ```sh
   scp batoplex-<version>.tar.gz root@<batocera-ip>:/userdata/system/
   ```

3. On the Batocera PC, unpack it and run the installer:

   ```sh
   cd /userdata/system
   tar xzf batoplex-<version>.tar.gz
   bash batoplex-<version>/install.sh
   rm -rf batoplex-<version> batoplex-<version>.tar.gz
   ```

   The installer checks for Python 3.9+, pygame, and mpv, then refreshes the game list.

4. In EmulationStation, open **Ports > Batoplex**.
5. Go to [plex.tv/link](https://plex.tv/link) on a phone or computer and enter the code shown
   on screen.
6. Press Menu, choose **Settings** > **TV Setup** > **Screen Margins**, and move each edge until it
   just shows on your TV. Video, menus and subtitles then stay inside the edges.

To start Batoplex when the system starts, open the game menu on **Ports > Batoplex** and
choose **Launch this game at startup**. Exit returns you to EmulationStation.

### Optional: check the system first

`probe.sh` is read-only. It reports the Batocera version, Python, pygame, mpv, the video
output and mode, connected controllers, and (if you pass a URL) whether the Plex server answers.

```sh
bash batoplex-<version>/probe.sh http://<plex-server>:32400 > /userdata/system/batoplex-probe.txt 2>&1
```

Attach that file to a bug report if something does not work.

## Update

Install the new version the same way. Your settings and sign-in are kept.

## Remove

```sh
bash /userdata/system/batoplex/uninstall.sh              # removes the app, settings, sign-in, and cache
bash /userdata/system/batoplex/uninstall.sh --keep-data  # keeps settings and sign-in
```

After a full removal, also remove the **Batoplex** device at
[plex.tv/devices](https://plex.tv/devices).

## Files

| Path | Contents |
|---|---|
| `/userdata/system/batoplex/batoplex/` | The app (Python) |
| `/userdata/system/batoplex/data/auth.json` | Plex sign-in token, mode 0600, never logged |
| `/userdata/system/batoplex/data/settings.json` | Screen margins, flicker filter, and other settings ([configuration](configuration.md)) |
| `/userdata/system/batoplex/data/*.log` | App and launcher logs, with tokens redacted |
| `/userdata/roms/ports/Batoplex.sh` | The Ports launcher |
| `/userdata/roms/ports/Batoplex.sh.keys` | Hotkey + Start exits Batoplex |

`/userdata/system` is visible on the Batocera `system` network share. `auth.json` can only be
read by root.

## Visualizer

The visualizer uses [projectM](https://github.com/projectM-visualizer/projectm) (LGPL-2.1)
and presets from its Cream of the Crop pack. Each release attaches the projectM source. See
[native/THIRD-PARTY.md](../native/THIRD-PARTY.md). If the visualizer cannot start, Batoplex
shows a simple spectrum instead. The visualizer runs on x86_64 only. Other CPUs get the
spectrum.
