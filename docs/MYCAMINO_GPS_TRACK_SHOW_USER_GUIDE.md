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

New Adventures also start with the **Assistant** enabled. Its yellow speech
bubble points to the next required control in this order: project directory,
Adventure name, GPX file, Track Maps, media, control file, place names, and the
first slide-show start. The bubble moves on automatically as each step becomes
ready. Click its `x`, or clear **Assistant** in the header, to disable it for
the active Adventure. That choice and the completed action steps are saved
automatically with the Adventure.

Adventures created before the Assistant was introduced do not repeat the two
action-only prompts for place names and starting the show. Missing GPX files,
maps, media, or control files are still detected and can reactivate a relevant
data step.

## Adventure Section

Use this section to choose the project directory and describe the adventure.

Controls:

- Project directory field: type or choose the adventure folder.
- Folder icon: opens a directory chooser. A missing selected directory is
  created when selected.
- Adventure name: an editable menu of the Adventures in the selected folder.
  Selecting one loads it immediately. The most recently modified Adventure is
  loaded automatically when a folder is chosen.
- Description: optional two-line description.

Files created:

- `<projectname>.adv`: adventure settings file. This is JSON content with a
  `.adv` extension.

The `.adv` file explicitly stores its project directory, GPX filename, active
`.lst` file, Track Map family, settings, description, import directory, and
last stopped slide-show position. After creation or loading, every relevant
change is saved automatically.

In an empty folder, confirm the suggested Adventure name to create the first
`.adv`. Committing a changed name for an existing Adventure offers **Rename**,
**Copy**, or **Cancel**. The related-files checkbox is enabled by default and
renames/copies GPX, control list, overview, summaries, and all standard and
Time-Lapse Track Maps consistently. Photos and videos remain shared. Turning
the checkbox off intentionally shares GPX, list, and maps; the GUI warns before
an operation modifies data used by another Adventure. A related-data Rename is
refused while another Adventure shares those files; use Copy or turn off the
related-data option instead.

### Adventure Settings

Press the gear immediately left of the myCamino logo to open **Adventure
Settings**. Choose a section in the left sidebar. The right side shows each
setting together with a short explanation.

- Common settings are visible initially. Enable **Show Advanced Settings** for
  technical GPX, PDF, request, cache, and custom map-server controls.
- The reset arrow beside one row restores only that setting. **Reset All**
  restores all parameter defaults without changing project files or names.
- **Apply** validates, applies, and auto-saves the draft to this adventure.
  **Cancel** discards the draft.
- Invalid entries are explained at the bottom and disable Apply.

Settings cover Standard and Time-Lapse playback, Track Map appearance and
ordering, GPX processing, PDF output resolution, place-name lookup, and map
providers. OpenStreetMap and Esri are presets. A custom provider requires an
HTTP(S) tile address containing `{z}`, `{x}`, and `{y}`, plus attribution.
Selecting Custom reveals both required fields immediately. Numerical settings
can be typed or adjusted with the adjacent up/down stepper.

GPX Processing separates horizontal smoothing (default 10 m), retained-point
spacing (10 m), and elevation smoothing (50 m). Horizontal/vertical uncertainty
limits default to 10/20 m, and HDOP/VDOP limits to 20/20. Missing quality data
is accepted. Setting any individual smoothing, spacing, or quality limit to
zero disables it. The same processed geometry drives statistics, Track Maps,
PDFs, timing, and Time-Lapse motion; the original GPX points are not rewritten.

The Time-Lapse **Moving marker** setting chooses between the animated walking
pilgrim and the traditional arrow. The pilgrim is the default. It uses a
standing pose while the GPS marker remains within the screen-scaled motion
tolerance, then resumes its walking cycle from frame 3. At the start of each
stage it adopts the fixed arrow angle and is mirrored when necessary so that it
faces from the stage start toward its end. This setting affects only the stage
map; the overview map continues to show the traditional arrow.

The Locations radius groups nearby GPS positions under an already determined
place name. A larger radius reduces the number of lookups and can speed up
**Add Place Names**, while a smaller radius gives more locally specific names.

Changing a map-rendering or GPX-filtering setting marks affected Track Maps or
the track summary as needing Update. Slide-show and place-name settings do not
force map recreation. Keyboard changes made while a slide show is running are
temporary and do not overwrite the saved project settings.

Main controls:

- Assistant, beside Help: show or hide the guided next-step bubble for the
  active Adventure.
- Help, beside Settings in the header: show the workflow and files used by the
  program.
- Quit: flush any pending auto-save and quit. If writing fails, the program
  offers Retry, Quit Without Saving, or Cancel.

## GPX Files Section

Use this section to select, edit, summarize, and plot the GPX tracks.

Controls:

- GPX file field: editable menu of `.gpx` files in the project directory, or
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

- Import: choose images and videos to import into the project directory.
- View: open the project media browser.
- Folder icon: open project media in Finder.

Files created:

- Imported image/video files are copied directly into the project directory.
- A file already present under the destination name is reported as
  **Skipping existing** and is not copied or counted as newly imported.
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

- Control file: editable menu of `.lst` files in the project folder. Select an
  existing list, or enter the name that **Create** should produce.
- Create: run geolocation/metadata processing and create the sorted control
  file.
- Edit: open the editable control-file table.
- Add Place Names: reverse-geocode GPS coordinates and add missing place names.
- Sync Track Maps: update canonical track map lines and adjacent-day map
  references in an existing control file. It can add missing map lines and
  remove old map lines that no longer match the current GPX summary or no
  longer exist. It does not create map images.
- Merge New Media: choose additional media files and merge them into the
  existing control file.

During Create and Merge New Media, media from a date without its own track can
be associated with a stage on the next or previous day. With GPS data, the
location must be within half the stage length of the appropriate start or end
point. Without GPS data, the date alone is used. The list labels these sections
as **Day before** or **Day after**. Media that does not qualify remains in a
date-sorted section for manual placement.

After this assignment, the program makes a location map for every remaining
date group that contains at least one usable photo or video position. The map
shows the date rather than a track name and is inserted at the date's proper
place in the show. Day-before and Day-after groups always stay attached to
their track. Media without any usable position remains in a mapless date
section. These generated maps are stored in the `trackimages` folder with
names ending in `-media-YYYY-MM-DD.png`.

Like track maps, location maps can have Standard and Time-Lapse versions. The
selected **for Time-Lapse** option controls which version Create, Update,
control-file creation, and Merge New Media produce. Time-Lapse versions shift
the mapped positions at the same scale to maximize useful free space for
framed media. They appear as **Media locations** in the Track Maps Update list
and receive an `*` when the selected version is missing or out of date.

In the editor, a location map has type `LOC`. During Time-Lapse playback it is
a static stage: there is no walking pilgrim, but every displayed image or video
is marked on the location map and, when visible, on the overview. The marker
uses a fixed arrow so it is easy to find. The framed image or video is drawn
above map markers and may cover one when the selected corner overlaps it.

While the control-file table is edited, the program regularly writes recovery
copies in a private backup folder. If the application or computer stops before
Save is pressed, reopening the list offers to restore the latest edits. Save
updates the real control file and clears the active recovery copy.
Closing the editor with unsaved changes offers **Save**, **Don't Save**, and
**Cancel**. This check also runs when quitting the main application.

The search field at the top right finds text or numbers anywhere in the saved
control-file rows, including filenames, times, GPS coordinates, place names,
and map directives. Use its up/down arrows to move through the matches.
`Command-F` focuses the search and advances to the next match;
`Shift-Command-F` moves to the previous match. Search wraps at both ends.

Right-click a table row to Delete, Cut, Copy, Paste, Preview, or Open in
Finder. Finder opens the containing folder with the selected image, video, or
map file already highlighted. The Previews checkbox remains available at the
top left when the editor window is resized.

Music control uses separate `MUS` rows rather than an extra column. Press
**Insert Row** or Command-I, choose `MUS` in the Type field, and enter the
comma-separated commands in **File / Date / Map**. A nonmodal command reference
opens for a `MUS` row. **Hide Media Rows** temporarily removes only image and
video rows and then changes to **Show Media Rows**. The selected stage remains
selected and is scrolled into view when media rows return, making it easy to
continue editing there. Filtering never changes the saved row order, and
search uses only visible rows while the filter is active.

The compact Type column shows only its short code. Its dropdown menu shows both
the short type and its meaning, for example `TRK - Track map` and
`MUS - Music control`. The short code also remains the type used in saved
directives.

While a table cell is being edited, both Command-C/X/V and Control-C/X/V act
on the text inside that cell. With no active cell editor, the same shortcuts
copy, cut, or paste complete selected rows.

When the media viewer moves with the arrow keys, the corresponding control-file
row is selected and scrolled into view. Videos open at their first frame in an
embedded player with native playback controls. Space or Return toggles
Play/Pause.

Files created:

- The selected `.lst` name: final slide-show control file.
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
- `BEF`: map for media assigned to the day before a stage.
- `AFT`: map for media assigned to the day after a stage.
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
- Copy and Cut buttons duplicate or move selected rows through the clipboard.
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
- Preserves `Day before`/`Day after` section types while updating their map
  filenames.
- Avoids adding the same track map twice.
- Uses the selected track ordering.
- Updates a temporary copy first. Cancelling or encountering an error leaves
  the active control file unchanged.

Merge New Media:

- Opens the media browser.
- Marks which files are already included in the control file.
- Lets you select new media files.
- Creates missing sidecars if needed without overwriting the control file.
- Inserts selected media where they would have appeared if included originally.
- Creates Day before/Day after sections when the adjacent-date and distance
  rules qualify.
- Avoids duplicate media entries.

## Start Slide Show

The Start Slide Show section contains:

- Music: choose one supported audio file or a directory containing MP3, M4A,
  AAC, WAV, AIFF, CAF, or FLAC files. One file repeats by itself. A directory
  is scanned recursively and uses case-insensitive relative-path order unless
  `<Adventure name>.playlist` is present. Choose creates and initially selects
  the project's `audio` folder when no source has been selected yet.
- Create Playlist: write an editable playlist into the selected music
  directory. Each album folder and filename receives a unique short `$LABEL`.
  Existing playlists are replaced only after confirmation.
- Update Playlist: keep existing text and append newly discovered files,
  grouped by folder, with new unique album and file labels.
- Edit Playlist: open the active playlist in TextEdit. If no playlist exists
  yet, it is first created from the supported audio files in the directory.
- The Adventure stores the selected music source and explicit playlist path.
  Renaming or copying an Adventure with related files enabled also renames or
  copies its playlist; the audio recordings themselves remain unchanged.

- Show type: choose **Time-Lapse** or **Standard**. Time-Lapse is selected by
  default; this preference is saved with the Adventure and can also be set in
  Settings.
- Start: launches the selected show from the beginning.
- Continue: launches the selected show at its last automatically saved
  position. It is disabled until such a position exists.
- Time-Lapse plays each track map as a moving GPS time-lapse. The track map
  becomes the main background, the overview map remains visible on the other
  display when a separate overview window is active, and photos or videos
  appear over the moving map. The
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
- Quit: auto-save pending Adventure changes and close the main GUI.

Window behavior is **Automatic** by default. A computer with one display starts
with one slide-show window. At the beginning of every new Time-Lapse stage, the
overview is shown by default as a framed medium over the stage map for the
current media duration. The current stage route is projected into that smaller
overview and highlighted there, followed by the animated route. Settings can restore a
full-screen overview instead. In a Standard show, the single-window sequence is
overview, stage map, then media. A computer with two or more displays starts
with a separate overview window. Settings can force either Single window or
Separate overview window regardless of display count.

In automatic Time-Lapse playback, the temporary overview advances to the moving
stage after the normal media duration. In Standard playback,
the track map is shown once at the beginning of each stage. The Settings option
**Track map before each medium** can restore the older behavior of showing the
marked track map before every photo or video; it is off by default.

Press `w` during playback to switch between one window and a separate overview
window. Closing only the overview window has the same effect as switching to
one window and does not stop the slide show.

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
Use **Continue** to return to that position, or **Start** to begin at the
beginning without a confirmation dialog. A show that runs normally to its end
clears the saved resume position.

Common slide-show keys:

- Space: pause or resume automatic playback.
- `m`: switch between automatic and manual stepping.
- `a`: fade background music out and pause it, or resume it at the same
  position with a fade-in. Space-pausing automatic playback also pauses music;
  videos pause it temporarily without changing the `a` setting.
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
- `w`: switch between one slide-show window and a separate overview window.
  Closing the overview window also returns to one-window playback without
  stopping the show.
- `t`: choose the normal slide-show transition.
- `i`: show photo metadata overlay.
- `c`, `p`, `f`, `d`, and `D`: keep their existing clock, place-name,
  fullscreen, display-swap, and memory-debug functions.
- `h`: show help.
- `q` or Escape: quit slide show.

Because the slide show runs separately, quitting it should not close or crash
the main GUI.

### Music playlists and directives

A playlist is a plain text file. Blank lines are ignored, paths are relative to
the selected music directory, and a line such as `$MORNING` labels the next
audio file. Several labels may precede one file. Labels are case-insensitive;
ambiguous extensionless names are skipped with a warning. Subfolders that
directly contain audio are albums and receive an `$ALB_...` label when a
playlist is created.

Add a standalone line such as `#MUSIC: #JUMP $MORNING` to the control file.
Entries are CSV-style comma-separated; quote a pathname containing commas.
Available commands are:

- `$LABEL` or pathname: temporarily queue titles in the listed order, then
  resume the previously interrupted playlist title at its saved position.
- `#JUMP $LABEL`: discard a queue or loop and continue from the label.
- `#ON` / `#OFF`: open or close the control-file audio gate.
- `#CONTINUE`: cancel the active queue or loop without a hard cut.
- `#LOOPLINE`: repeat labels/files later on the same line.
- `#LOOPONE`: repeat the current or most recently played title.
- `#LOOPRANGE $A $B`: repeat the inclusive playlist range.
- `#LOOPALBUM, $LABEL`: repeat the target's album; without a target it uses the
  next album after the current position.
- `#LOOPALL`: repeat the complete playlist.
- `#VOLUME+`, `#VOLUME-`, or `#VOLUME N`: change the internal level from 0–9.

Targets, jumps, continue, and loop commands replace the active transport mode;
gate and volume commands do not. A valid target opens the control gate. A
manual `a` toggle to Audio Off always has priority, so no control-file command
can restart audio until `a` is pressed again. The crossfade duration is set in
**Settings > Audio**. Missing labels or files warn and leave playback running.

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

Adjacent-day sections are static in Time-Lapse mode. The related stage map is
shown first with **Day before** or **Day after**, followed by framed media in
the available map corners. No pilgrim, arrow, or travelled-distance figures
are added to this special section. A media GPS position is still marked when
available, and the overview window continues to highlight the associated
route. These sections do not count as extra travelled stages.

## Standalone GPX Editor App

The DMG also contains `myCamino GPX Editor.app`. This is the same GPX editor
that opens from Add & Edit Tracks, but it can also be launched directly for GPX
work that is not part of a slide-show project. Its gear button edits GPX
processing, PDF export, and map-service settings. Standalone settings are kept
for future sessions; when opened from an Adventure, they are auto-saved in that
Adventure instead.

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
