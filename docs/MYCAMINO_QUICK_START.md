# myCamino Quick Start

myCamino turns GPS tracks, photographs, videos, maps, music, and narration into
a map-based Adventure and slide show. The Adventure Map is the normal starting
screen. Press **h** there for its complete keyboard help.

## Build an Adventure

1. Drop an existing Adventure file or project folder onto the Adventure Map,
   or drop one or more GPX files to begin a new Adventure. With no Adventure
   open, myCamino explains what will be created and lets you name the Adventure,
   choose or create its folder, or open an existing Adventure instead.
2. Drop photographs and videos onto the map. Choose **Add to Slide Show** to
   copy them into the Adventure and prepare their metadata. Choose **Create or
   Extend Track** when the media locations should instead become GPX points.
3. On the first metadata run, decide whether myCamino may request historical
   weather from Open-Meteo. Place names and weather are optional; embedded
   dates and GPS data are prepared locally.
4. myCamino prepares required maps and then creates an initial control file
   automatically when none exists. Review that file to choose and order the
   media shown in the slide show.
5. Choose **Slide Show > Start**. During
   playback, press **t** or **Shift-T** to cycle transition styles and **h** for
   all playback keys.

A GPX track is optional. myCamino can create one from the GPS positions in
photos, from points entered on the map, or from coordinates pasted from Google
Maps, Google Earth, and other sources. Tracks can be edited directly on the map
by inserting or moving points and by splitting or joining segments. The Track
Table and Waypoint Table provide detailed numerical editing when needed.

## Drag-and-Drop Inputs

- `.adv` files and Adventure folders open an existing project.
- GPX files add tracks. When several are dropped together, they can be ordered
  by time or by distance from an anchor point.
- Photos and videos can become slide-show media or track points.
- Music and narration files are copied into their respective audio folders and
  can be organized into playlists.
- A control file can be opened directly for review and media selection.

## Advanced Options

Use **Adventure > Advanced Interface** for complete project status and detailed
controls. Settings cover GPX processing, map providers, metadata and place-name
handling, weather, audio normalization, slideshow timing, headers, transitions,
dual-screen playback, and end-of-show behavior.

The Control File editor supports music, narration, captions, labels, transitions,
timing, pauses, jumps, and hidden media. The GPX Editor supports map and table
editing, elevation profiles, ordering, renumbering, exporting selected tracks,
and building tracks from photos or pasted coordinates.

Use **Help > Start Guided Demo** at any time for a disposable hands-on example.
The demo asks you to drag a supplied GPX track and a folder of photographs onto
the map, then uses the same metadata and control-file workflow as a real project.

Adventure changes are saved automatically after two seconds without further
editing. **File > Save Workspace** or `Cmd-S` remains available when an
immediate save is wanted. Standalone editors continue to use their explicit
Save and Revert commands.
