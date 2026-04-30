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
last picture import directory, and other saved GUI settings. When you load an
adventure, the GUI restores these fields.

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

Use this section to create and maintain the overview map and one map per GPX
track.

Controls:

- Create: recreate the overview map and all track maps. If current maps already
  exist, the GUI asks before overwriting them.
- Update: open a selection window for missing or outdated maps.
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
- `0001_...png`, `0002_...png`, etc.: per-track map images.
- `0001_...json`, `0002_...json`, etc.: per-track metadata sidecars.

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
- Update Tracks: update track map lines in an existing control file. It can add
  missing map lines and remove old map lines that no longer match the current
  GPX summary or no longer exist.
- Merge New Media: choose additional media files and merge them into the
  existing control file.

Files created:

- `<projectname>.lst`: intermediate list.
- `<projectname>-sorted.lst`: final slide-show control file.
- `<mediafile>.json`: metadata sidecar for each photo or video.

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

Update Tracks:

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
- PDF Summary: opens the same PDF export panel used by `myCamino GPX Editor`.
  It exports the current GPX track table and can optionally include overview,
  track maps, elevation profiles, and page orientation choices.

The GUI passes these values to the slide-show player:

- the project directory
- the sorted control file as `--inputlist`
- the full `trackimages` directory as `--trackdir`

The slide show reads the control file, media files, map images, and sidecar
metadata.

Common slide-show keys:

- Space, Right, Down: next item.
- Left, Up: previous item.
- `i`: show photo metadata overlay.
- `h`: show help.
- `q` or Escape: quit slide show.

Because the slide show runs separately, quitting it should not close or crash
the main GUI.

## Standalone GPX Editor App

The DMG also contains `myCamino GPX Editor.app`. This is the same GPX editor
that opens from Add & Edit Tracks, but it can also be launched directly for GPX
work that is not part of a slide-show project.

## What Files Are in a Finished Project Directory

A typical finished adventure directory contains:

- `<projectname>.adv`: saved adventure settings.
- `<projectname>.gpx`: active combined GPX track file.
- photos and videos copied into the project directory.
- `<mediafile>.json`: media sidecar metadata.
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
  old, use Update Tracks.
- If the control file cannot be created with tracks, check that
  `trackimages/<projectname>-summary.json` exists.
- If media have no place names, use Add Place Names.
- If the slide show does not start, check that the control file and
  `trackimages/` directory both exist.
