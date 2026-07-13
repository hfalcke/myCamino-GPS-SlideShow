# myCamino GPS Track Show User Guide

`myCamino GPS Track Show` helps you assemble one adventure from GPX tracks,
photos, videos, map images, geolocation metadata, and a final slide-show
control file.

An adventure is one project directory. The project directory contains all files
created or imported by the workflow.

Start the GUI with:

```bash
./.venv/bin/python GPSTrackShowGUI.py
```

You can also preload a project:

```bash
./.venv/bin/python GPSTrackShowGUI.py --project-directory /path/to/project
./.venv/bin/python GPSTrackShowGUI.py /path/to/project/adventure.adv
```

## Basic Workflow

The normal order is:

1. Choose or create the adventure directory.
2. Enter the adventure title and optional description.
3. Select or edit the GPX track file.
4. Create or update track maps.
5. Import photos and videos.
6. Create the slide-show control file.
7. Review and edit the control file.
8. Add place names if desired.
9. Export a PDF track summary if desired.
10. Start the slide show.

Green check marks beside sections show that the minimum required step for that
section is complete. Red marks show what is still missing.

## Adventure Section

Use this section to choose the project directory and describe the adventure.

Controls:

- Project directory field: type or choose the adventure folder.
- Folder icon: opens a directory chooser. A missing selected directory is
  created.
- Title: adventure name used for default file names.
- Description: optional two-line description.

Files created:

- `<projectname>.adv`: adventure settings file. This is JSON content with a
  `.adv` extension.

The `.adv` file stores the project name, project directory, selected GPX file,
last picture import directory, the project settings described below, and the
last stopped slide-show position. Loading an adventure restores them together.

### Adventure Settings

Press the gear immediately left of the myCamino logo to open **Adventure
Settings**. Choose a section in the left sidebar. The right side shows each
setting together with a short explanation.

- Common settings are visible initially. Enable **Show Advanced Settings** for
  technical GPX, PDF, request, cache, and custom map-server controls.
- The reset arrow beside one row restores only that setting. **Reset All**
  restores all parameter defaults without changing project files or names.
- **Apply** validates and applies the draft to this adventure. Save the
  adventure afterward to retain it. **Cancel** discards the draft.
- Invalid entries are explained at the bottom and disable Apply.

Settings cover Standard and Time-Lapse playback, Track Map appearance and
ordering, GPX processing, PDF output resolution, place-name lookup, and map
providers. OpenStreetMap and Esri are presets. A custom provider requires an
HTTP(S) tile address containing `{z}`, `{x}`, and `{y}`, plus attribution.
Selecting Custom reveals both required fields immediately. Numerical settings
can be typed or adjusted with the adjacent up/down stepper.

The Locations radius groups nearby GPS positions under an already determined
place name. A larger radius reduces the number of lookups and can speed up
**Add Place Names**, while a smaller radius gives more locally specific names.

Changing a map-rendering or GPX-filtering setting marks affected Track Maps or
the track summary as needing Update. Slide-show and place-name settings do not
force map recreation. Keyboard changes made while a slide show is running are
temporary and do not overwrite the saved project settings.

Bottom buttons:

- Save: save the `.adv` file.
- Load: load an existing `.adv` file.
- Save & Exit: save and quit.
- Quit: quit, asking whether to save if needed.

## GPX Files Section

Use this section to select, edit, summarize, and plot the GPX tracks.

Controls:

- GPX file field: active GPX file basename in the project directory, or
  selected input files before editing.
- Choose: select one or more `.gpx` files.
- Folder icon: open Finder for GPX files in the project directory.
- Add & Edit Tracks: opens `myCamino GPX Editor`.

Behavior:

- If one existing GPX file in the project directory is selected, the GUI uses
  it directly.
- If a GPX file outside the project directory is selected, the GUI asks whether
  to copy it into the project directory.
- If multiple GPX files are selected, the GUI opens the GPX Editor so you can
  produce one combined GPX file.
- If the selected GPX file does not exist, the GPX Editor opens with the default
  output file.
- If the GPX file directory does not exist, the GUI shows an error.
- When the GPX Editor saves or closes, the GUI updates the GPX field to the
  most recently saved GPX file, refreshes the statistics, and regenerates the
  track summary file without recreating map images.

The summary line shows the number of tracks, track/date range information, how
many track plot images exist, and whether an overview plot exists.

## Track Maps Section

Use this section to create and maintain one shared overview plus Standard and
Time-Lapse map variants for each GPX track.

Controls:

- Create: recreate the overview and every track map of the selected variant. If
  current maps already exist, the GUI asks before overwriting them.
- Update: open a selection window for missing or outdated maps.
- for Time-Lapse: checked means Create, Update, and View prefer Time-Lapse maps;
  unchecked means Standard maps.
- View: view existing map images.
- Folder icon: open the `trackimages` folder in Finder.
- Track ordering by: choose `date` or `track number` for generated map order and
  control-file insertion.
- Cancel: shown only while map creation is running.

Files created:

- `trackimages/`: subdirectory in the project directory.
- `<projectname>.png`: overview map image.
- `<projectname>.json`: overview map metadata sidecar.
- `<projectname>-summary.json`: track summary used later by the control-file
  section.
- `0001_...png`, `0002_...png`, etc.: centered Standard per-track maps.
- `0001_...-timelapse.png`, etc.: Time-Lapse maps shifted to leave the largest
  practical route-free corner for photos and videos.
- Matching `.json` files: timing, map coordinates, track identity, and cached
  route-free corner rectangles. The player uses these rectangles directly and
  recalculates them only when cached data is missing or invalid.

The summary and map sidecar files store per-track fingerprints. This lets the
GUI decide which maps really need updating after a GPX edit instead of assuming
all maps are stale whenever the GPX file changes.

When maps are created or updated, obsolete numbered track-map files in
`trackimages/` are removed if they no longer match any current GPX track. The
overview, summary, and unrelated files are not removed.

Update Track Images window:

- Opens when you press Track Maps Update.
- Shows overview as number `0` and all tracks as numbered rows.
- Rows marked with `*` need update.
- Missing or outdated maps are preselected and listed in the Range field.
- You can update all images, select rows manually, or type a range such as
  `1,2,3-6,8`.
- Manual row selection automatically disables the Create all images checkbox.

Plot viewer window:

- Opens while plots are created and updates after each new image.
- Also opens from Track images View.
- Arrow keys move through images.
- `h` toggles help.
- `q` hides/closes the viewer.
- The window keeps the image aspect ratio while resizing.

## Photos and Video Clips Section

Use this section to import and inspect the media files for the adventure.

Controls:

- Import: choose images and videos to copy into the project directory.
- View: open the project media browser.
- Folder icon: open project media in Finder.

Files created:

- Imported image/video files are copied directly into the project directory.
- Existing files are skipped so duplicates are not imported.
- Sidecar `.json` files are created later by the Slide Show Control File
  section or by media browser preparation when needed.

Media browser window:

- Shows media files in the project directory.
- Columns include included status, type, name, time, GPS, and place.
- Sort rows by clicking a column header.
- Double-click a row or press View to open the media viewer.
- In merge mode, select rows and press Merge Selected.

Media viewer window:

- Displays one image or video.
- Shows filename, creation/taken date, GPS coordinates, and place name when
  available.
- Arrow keys move backward and forward.
- `i` toggles the information overlay.
- `h` toggles help.
- `s` toggles sorting between filename and creation date.
- `q` closes the viewer.

The status line below the buttons shows how many photos and videos are present.

## Slide Show Control File Section

This section creates and edits the file that tells the slide show what to show
and in what order.

Controls:

- Create: run geolocation/metadata processing and create the sorted control
  file.
- Edit: open the editable control-file table.
- Add Place Names: reverse-geocode GPS coordinates and add missing place names.
- Sync Track Maps: update canonical track map lines in an existing control file. It can add
  missing map lines and remove old map lines that no longer match the current
  GPX summary or no longer exist. It does not create map images.
- Merge New Media: choose additional media files and merge them into the
  existing control file.

Files created:

- `<projectname>.lst`: intermediate list.
- `<projectname>-sorted.lst`: final slide-show control file.
- `<mediafile>.json`: metadata sidecar for each photo or video, including the
  original extension, for example `IMG_4104.mov.json` or
  `IMG_4104.jpeg.json`.

Older projects created before this naming convention can be migrated without
losing their saved place names by running:

```bash
./.venv/bin/python GetGeoLocations.py /path/to/project --migrate-media-sidecars
```

The migration never changes image or video files. Unclear old metadata files
are preserved with a `.legacy-sidecar` name for review.

The status line below the buttons says either `No control file available yet`
or shows statistics such as number of images, videos, track maps, date rows,
whether an overview map is present, how many images have place names, and the
last modification date.

Create output window:

- Opens during Create, Add Place Names, and merge operations.
- Shows live output from `GetGeoLocations.py`.
- Always scrolls to the latest line.
- Cancel stops processing when possible.
- Close becomes available after completion.
- The progress bar in the main window is shown only while useful.

If the track summary JSON does not exist, the GUI asks whether to run plot
creation first or continue without tracks.

## Control-File Editor Window

The Edit button opens the slide-show control-file table.

Rows can be:

- `IMG`: image.
- `VID`: video clip.
- `MAP`: overview map.
- `TRK`: track map.
- `DAT`: date section row.

Table features:

- Scrollable vertically and horizontally.
- Optional thumbnail previews.
- Cached previews for smoother scrolling.
- File date column from media or track sidecars.
- DAT rows are bold.
- MAP and TRK rows are italic.
- Rows are selectable.
- Drag rows to reorder them.
- Edit cells directly.
- Backspace/Delete removes selected rows.
- Cmd-C, Cmd-X, Cmd-V copy, cut, and paste rows.
- Undo and redo buttons use standard arrow icons.
- Save writes the edited table back to `<projectname>-sorted.lst`.
- Revert reloads the file from disk and discards unsaved table edits.
- Close closes the editor window.
- Double-click media rows to open the media viewer.
- Sort by clicking table headers.

Editing this table is how you fine-tune the final slide-show order.

## Update and Merge Operations

Sync Track Maps:

- Reads the current track summary JSON in `trackimages/`.
- States which overview/track map lines need to be inserted.
- States which old map lines no longer match the current tracks or map files.
- Offers to remove old map lines before merging.
- Inserts missing overview/track map lines into the existing sorted list.
- Avoids adding the same track map twice.
- Uses the selected track ordering.

Merge New Media:

- Opens the media browser.
- Marks which files are already included in the control file.
- Lets you select new media files.
- Creates missing sidecars if needed without overwriting the control file.
- Inserts selected media where they would have appeared if included originally.
- Avoids duplicate media entries.

## Start Slide Show

The Start Slide Show section contains:

- Start: launches `GPSTrackShow.py` as a separate process.
- Start Time-Lapse: plays each track map as a moving GPS time-lapse. The track
  map becomes the main background, the overview map remains visible on the
  other display, and photos or videos appear over the moving map. The
  standard display-swap key still swaps these two display roles. For every
  stage, the player calculates track-free width and height choices in all four
  corners. Each photo or video uses the corner where its own shape can be shown
  largest. The saved 50% value is a preferred minimum, not a maximum; media can
  grow substantially larger wherever the route-free area permits it. Where map
  metadata is available, placement
  remains below the map header and keeps a 5% margin inside the actual map
  area. The moving arrow keeps one fixed orientation, perpendicular to the line
  from the stage start to its end. While media is visible, a red dot with a
  white edge marks the route position where it first appeared. Its reverse-
  geocoded place appears as one shadowed line in the free 5% strip at the
  bottom of the map. When the clock option is enabled, the analog clock in the
  upper-left follows the moving GPX marker time throughout the stage rather
  than showing a fixed media time. The upper-right map header shows total
  travelled distance, distance within the current stage, and current height.
  Half-transparent black shadows keep all overlay text readable; `c` and `p`
  toggle the clock and place name respectively.
- PDF Summary: opens the same PDF export panel used by `myCamino GPX Editor`.
  It exports the current GPX track table and can optionally include overview,
  track maps, elevation profiles, and page orientation choices.

The GUI passes these values to the slide-show player:

- the project directory
- the sorted control file as `--inputlist`
- the full `trackimages` directory as `--trackdir`
- the saved time-lapse media minimum size

The slide show reads the control file, media files, map images, and sidecar
metadata.

When a slide show is stopped before reaching its end, the player returns its
current control-file row and, for Time-Lapse, its stage progress and visible
medium. The main GUI stores this position automatically in the adventure file.
At the next Start or Start Time-Lapse, a **Resume Slide Show?** dialog appears.
Press `y` to continue there or `n` to start at the beginning. A show that runs
normally to its end clears the saved resume position.

Common slide-show keys:

- Space: pause or resume automatic playback.
- `m`: switch between automatic and manual stepping.
- Right or Down: advance to the next item; in manual time-lapse mode, advance
  to the next scheduled media pause or the end of the stage. Arrow-key
  navigation retains the current automatic or manual playback mode.
- Left or Up: previous item. In Time-Lapse this first walks backward through
  media in the current stage, then to the previous stage. Previous items are
  reloaded from the control list instead of being retained as large images.
- Command-Left or Command-Right: jump to the previous or next date section.
- `+` or `-`: change media duration. In Time-Lapse this is a minimum; the
  current image remains visible longer whenever no newer image is due.
- Command-`+` or Command-`-`: change the active stage time-lapse duration by
  five seconds.
- `T`: switch exclusively between Standard Slide Show and Time-Lapse at the
  current stage; the inactive presentation is stopped.
- `t`: choose the normal slide-show transition.
- `i`: show photo metadata overlay.
- `c`, `p`, `f`, `d`, and `D`: keep their existing clock, place-name,
  fullscreen, display-swap, and memory-debug functions.
- `h`: show help.
- `q` or Escape: quit slide show.

Because the slide show runs separately, quitting it should not close or crash
the main GUI.

The time-lapse uses the recorded GPX point times where available. Missing times
are estimated along the travelled distance; when a track has no usable duration,
it uses a walking speed of 3.5 km/h. Creating or playing a time-lapse does not
change the GPX file or its map information. Track maps created or updated with
the current version include cumulative distance, elevation, and timing data.
Older projects can be migrated once without recreating their map PNG files. If
that information is unavailable, the player uses its safe distance-based
fallback instead of changing project files while it starts.

The position marker normally continues moving while a photo or video is shown.
If it reaches the scheduled position of another medium before the current one
has received its full display time, the marker waits at that position. The next
medium then starts there after the previous one has finished. If the marker has
already reached the final track time but later-dated media remain in the stage,
the marker stays at the endpoint while the analog clock advances to the capture
time and date of each displayed medium.

## Standalone GPX Editor App

The DMG also contains `myCamino GPX Editor.app`. This is the same GPX editor
that opens from Add & Edit Tracks, but it can also be launched directly for GPX
work that is not part of a slide-show project.

## What Files Are in a Finished Project Directory

A typical finished adventure directory contains:

- `<projectname>.adv`: saved adventure settings.
- `<projectname>.gpx`: active combined GPX track file.
- photos and videos copied into the project directory.
- `<mediafile>.json`: media sidecar metadata; the full media filename remains
  part of the sidecar name, for example `IMG_4104.mov.json`.
- `<projectname>.lst`: intermediate list.
- `<projectname>-sorted.lst`: final slide-show control file.
- `trackimages/`: map images and their metadata.

Inside `trackimages/`:

- `<projectname>.png`: overview map.
- `<projectname>.json`: overview sidecar.
- `<projectname>-summary.json`: track summary.
- `0001_...png`, `0002_...png`, etc.: track maps.
- matching `.json` sidecars for track maps.

## Troubleshooting

- If the GPX section says no GPX file is available, choose or create a GPX file
  first.
- If track maps are missing, use Track Maps Create.
- If only some track maps are missing or outdated, use Track Maps Update.
- If the Slide Show Control File status says track map entries are missing or
  old, use Sync Track Maps.
- If the control file cannot be created with tracks, check that
  `trackimages/<projectname>-summary.json` exists.
- If media have no place names, use Add Place Names.
- If the slide show does not start, check that the control file and
  `trackimages/` directory both exist.
