# Configuration

## Settings

Press Menu for the menu of the focused item; Settings is its last row. Holding A opens the
same menu. Settings is also the last row of the side panel. Each page has a group of
settings, and the focused row shows a line of help at the bottom. B goes back.

- **Experience:** Theme Music and its volume, Automatically Sign In.
- **Appearance:** Theme, High Contrast, Menu Sounds, Screen Saver.
- **Player Experience:** Skip Intro and Credits, Auto Play, Up Next and its countdown time,
  Passout Protection, Rewind on Resume.
- **Video & Audio:** quality for home and remote servers, the default Display Mode,
  Night Audio (quiet sounds up, loud sounds down, for video).
- **TV Setup:** Screen Margins, Test Patterns, Flicker Filter, Start on Boot.
- **Account:** your server, other servers, Switch User, Sign Out.

Each profile keeps its own Player Experience settings and theme. Menu Sounds is for the
Hearth theme. In Test Patterns, Left/Right change the pattern and Up/Down change the variant.
Menu has Flicker Filter and Show Name there, and Reset in Screen Margins.

## Side panel and More

Press B on Home for the side panel. Up on the top row goes to Settings, and Down on Settings
goes back to the top. **More** lists the libraries of each server, then Watchlist, Channels
and Add Radio Stations. Press Menu on a library, in the panel or in More, to pin or unpin it
or to move it up or down.

## Screen saver

Settings > Appearance > Screen Saver. Pick when it starts, the type and its options. The box
beside the list shows each change. Preview starts it now, and any button stops it.

- **Bounce:** posters from your pinned libraries, or the DVD Video logo in one of three
  forms that DVD players showed. Count (1 to 5), Speed and Size. The logo can change color
  at each wall.
- **Entertainment:** art from your pinned libraries, with the clock and the title.
- **Digital Rain:** Glyph Size, Color, Speed, Density and Glyph Set.
- **Polygons, Starfield, Pong, Fireplace, Tunnel:** the classics, redrawn.
- **Aquarium:** a reef tank, a freshwater tank, or both mixed, with Few, Normal or Many
  fish. Frogs and turtles come up for air, crabs walk the sand, and now and then it is
  feeding time or a shark passes in the distance. You can pick the species in each tank, how
  lively they are, how often each event happens, Moonlight and Pan Speed. In Preview, Left
  and Right show the rare moments one at a time.
- **Flying Objects, Night City:** pixel scenes. Flying Objects can show toasters, CRT TVs or
  both.
- **3D Maze:** the classic maze, redrawn.
- **Shuffle:** a different screen saver every 5, 10 or 30 minutes, from the ones you pick.
- **Weather:** your local forecast in the style of 1990s cable weather. Location is found
  from your internet connection, or set a ZIP code or city. Music can play a Plex playlist
  of yours while it shows; any button stops it.

During Music sets what shows while music plays: the visualizer (the default), the album art,
or the screen saver itself.

Weather asks plex.tv for your approximate location (Automatic) and gets the forecast from
[Open-Meteo](https://open-meteo.com). Nothing else leaves the box.

## Playback menu

Press Menu during playback:

- **Display Mode:** Letterbox, 14:9 or Zoom for wide video, kept for each show or movie.
  14:9 and Zoom also cut black bars that are part of the file. In Letterbox, 4:3 video always shows whole.
- **Quality:** for this video only. With Automatic, Batoplex lowers the quality if a remote
  video keeps buffering.
- **Sleep Timer:** 15 to 90 minutes, or the end of the episode, video or track. The Now
  Playing menu has it too.

## Queue and playlists

Press Menu on a movie, episode, show, album, track or playlist for **Play Next**, **Add to
Queue** and **Add to Playlist**. While the video queue has items, **Play Queue** in the menu
and the side panel opens it. Press Menu on a playlist entry to move or remove it, and on a
playlist to rename or delete it. Smart playlists can only be played, and a shared server can
refuse changes.

Item menus use the same order on every screen: the play actions, Mark as Played, Watchlist,
Play Next, Add to Queue, Add to Playlist, Go to Show (or Album, Artist), Details, then the
rows of that screen.

## settings.json

A few options are only in the settings file:

- Batocera: `/userdata/system/batoplex/data/settings.json`
- Desktop: `~/.batoplex/settings.json`

Quit Batoplex before you edit it.

| Key | Default | What it does |
|---|---|---|
| `server_url` | `""` | Server address to use instead of the one found through plex.tv, for example `"http://192.168.1.20:32400"` |
| `hidden_libraries` | `[]` | Library ids to hide (on your own server) |
| `sub_scale` | `1.0` | Subtitle size |
| `mpv_extra` | `[]` | Extra mpv options, for example `["--hwdec=no"]` |

A library id is the number after `source=` in the address bar when you open the library in
Plex Web.

```json
{
  "hidden_libraries": [8]
}
```

## Channels

Open **More** > **Set Up Channels** > **Make Channels for Me**. Batoplex makes about 10
themed channels, such as Comedy, Cartoons and Sci-Fi, and 2 marathons of your most-watched
shows. You can turn on more channels under **Suggested**. A **Channels** row then shows in
the side panel.

To add one channel, open **More** > **Manage Channels** > **Add a Channel**, or press Menu in
the guide. Pick a channel under **Suggested**, or use **Choose What Plays** to pick a show,
genre, network, collection, playlist or library. In **Manage Channels**, press A on a channel
to rename, move, turn off or delete it. **Start Over** makes a new set.

To play different things at different times, choose **Schedule** on a channel and add a time
block. Pick what plays, and Batoplex suggests a start time and days. The schedule starts each
day at 4:00, so a block cannot start then.

Channels play like live TV, so you cannot seek.

| Button | Channels |
|---|---|
| Up / Down | Change channel |
| LB | Last channel |
| A | Info banner; again to pause |
| RB | Guide over the picture (A tunes, B closes, LT/RT change the page) |
| B | Close the banner, then go to the guide |

## Radio

To add internet radio, put your stations in `radio.m3u` in the same folder as
`settings.json`:

```
#EXTM3U
#EXTINF:-1,Station name
https://example.com/stream.aac
```

A **Radio** row then shows in the side panel. Only `http` and `https` links work, up to 200
stations. A link with no `#EXTINF` name shows its host name.

To find stations on the TV, open **More** > **Add Radio Stations**, or the last row in
**Radio**. The list shows popular stations. Type two or more characters to search by name.
Press A to add or remove a station, and X to listen first. To remove a station from Radio,
press Menu on it and choose **Remove from Radio**.

The list comes from [radio-browser.info](https://www.radio-browser.info), and your search
text goes to that service. Batoplex adds each station to the end of `radio.m3u` and does not
change your other lines.

## Visualizer

Open it from Now Playing with Y or the last button in the row. Left/Right change the
preset, LT/RT change the track, A pauses, and B goes back while the music keeps playing. When the
screen saver starts during music, the visualizer shows instead (During Music).

To add your own presets, put `.milk` files and their textures in a `visualizer` folder next
to `settings.json`. Subfolders are fine. Batoplex mixes them with its built-in presets.
