# myCamino GPS Track Show Programmer Guide

This project is a native macOS/PyObjC workflow for building a GPS-aware photo
and video slide show from one adventure: a project directory, one combined GPX
track file, imported media, generated map images, geolocation metadata, and a
slide-show control list.

The active source files are:

- `GPSTrackShowGUI.py`: main Cocoa GUI for creating and managing an adventure.
- `GPSTrackShow.py`: standalone Cocoa slide-show player.
- `GPXEditor.py`: native GPX track editor, usable standalone or embedded from
  the GUI.
- `GetGeoLocations.py`: media metadata, sidecar, sorted-list, reverse
  geolocation, and merge logic.
- `gpx_tracks_table.py`: GPX parsing, table summaries, track sorting, map plot
  generation, and track summary JSON generation.
- `plot_metadata_utils.py`: shared JSON and coordinate/plot metadata helpers.
- `gpxjoin.py`, `gpxlist.py`, `editGPXTrack.py`: older or supporting GPX tools.

## Runtime Model

All GUI code uses PyObjC/Cocoa/AppKit. Tkinter is intentionally not used.

`GPSTrackShowGUI.py` is the main process. It imports and calls:

- `GetGeoLocations.run_with_options(...)`
- `gpx_tracks_table.prepare_with_options(...)`
- `gpx_tracks_table.run_with_options(...)`
- `GPXEditor.show_gpx_editor_from_cli_args(...)`
- `GPXEditor.export_pdf_summary_from_paths(...)`

`GPSTrackShow.py` is not imported by the GUI. It is launched as a separate
Python process with `subprocess.Popen(...)`. This is deliberate. Running the
slide show in-process previously caused macOS/PyObjC AppKit or AVKit lifetime
crashes on quit. Keep the process boundary.

`GPXEditor.py` is also built as a standalone app for the DMG, because it is
useful outside the full slide-show workflow.

Validation commands:

```bash
./.venv/bin/python -m py_compile GPSTrackShow.py GPSTrackShowGUI.py GPXEditor.py GetGeoLocations.py gpx_tracks_table.py plot_metadata_utils.py
./.venv/bin/python GPSTrackShowGUI.py --help
python3 GPSTrackShow.py --help
rg -n "TrackDiaShow|GPXTrackerShow|GPX Tracker Show" --glob '!__pycache__/**' .
```

## Main GUI Architecture

`GPSTrackShowGUI.py` contains one controller class:

- `GPXTrackerController`: main application controller. The name is historical,
  but the file and user-visible program name are now GPS Track Show.

Important GUI support classes:

- `GPSTrackShowGUITableDataSource`: generic table data source.
- `GPSTrackShowGUIMediaBrowserDataSource`: sortable media browser data source.
- `SlideShowControlTableDataSource`: editable slide-show control-file table.
- `SlideShowControlTableView`: table subclass for delete, drag, copy, paste,
  undo, and redo keyboard handling.
- `GPSTrackShowGUIPlotViewerView`: key-aware plot image viewer.
- `SlideShowMediaViewerView`: key-aware media viewer.
- Window delegate classes retain lifecycle hooks and should be kept alive while
  their windows are open.

The main window is built manually in `_build_window()` and positioned in
`layout_window()`. Help text is mostly in `_configure_tooltips()`.

The sections are:

- Adventure
- GPX Files
- Track Maps
- Photos and Video Clips
- Slide Show Control File
- Start Slide Show
- Save/Load/Quit controls, status line, and progress bar

Each section has a non-editable status checkbox. It is used as a visual
completion indicator:

- Adventure: project directory exists.
- GPX Files: at least one GPX file exists.
- Track Maps: overview and all expected track maps exist and are current.
- Photos and Video Clips: at least one supported media file exists.
- Slide Show Control File: control file exists and contains at least one image.

## Adventure Files

Adventure files use the extension `.adv` but contain JSON.

The GUI saves and loads them with:

- `save_project_configuration()`
- `load_project_configuration(...)`
- `_auto_load_adventure_from_directory(...)`

Stored values include:

- project name
- project directory
- GPX file basename/path as shown by the GUI
- last picture import directory
- description and other GUI state that belongs to the adventure

When a project directory is selected, the GUI creates it if needed and tries to
load an `.adv` file from that directory. The default GPX file is derived from
the project name unless the user manually changed the GPX field.

## GPX Plot Pipeline

The GPX section uses `gpx_tracks_table.py` directly as a Python module. Do not
shell out for plot creation.

Main functions:

- `parse_gpx_file(...)`: parse GPX tracks and metrics.
- `prepare_with_options(...)`: build a run context without executing all output.
- `run_with_options(...)`: execute table/plot/JSON output from Python.
- `selected_track_numbers(...)`: parse selected track numbers.

The GUI creates `trackimages/` under the project directory. It generates:

- overview image: `<projectname>.png`
- overview sidecar metadata: `<projectname>.json`
- summary JSON: `<projectname>-summary.json`
- per-track images: four-digit numbered PNG files, for example
  `0001_track-name.png`
- per-track sidecar JSON files

`gpx_tracks_table.py` writes stable per-track fingerprints into:

- `<projectname>-summary.json`
- each per-track plot sidecar
- the overview plot sidecar as `source_track_fingerprints`

The GUI uses these fingerprints to detect stale track maps per track. It falls
back to modification times only for older metadata that does not contain
fingerprints.

After `GPXEditor.py` saves or closes with an accepted GPX file, the main GUI
regenerates the track summary JSON with `run_gpx_tracks_table_with_options(...)`
without rendering maps. This keeps control-file operations consistent while
leaving slow map rendering under explicit user control.

The GUI deliberately tolerates legacy plot file names when detecting existing
plots. Matching code normalizes track plot names so old zero-padding variants
are not counted as missing duplicates.

Track Maps buttons:

- `Create`: render overview plus every track map directly. It confirms before
  overwriting existing current map images.
- `Update`: open the selection window with missing/stale maps marked by `*`,
  preselected, and copied into the range field.
- `View`: open existing map images.
- folder icon: open `trackimages/` in Finder.

Before rendering, obsolete numbered track-map `.png` and `.json` files are
removed if they no longer match any current GPX track. The cleanup intentionally
does not remove the overview image, summary JSON, or unrelated files.

Plot creation runs on a background thread. The GUI shows a Cancel button that
sets an event. Cancellation is cooperative and stops after the current image.
Each created image is pushed into the plot viewer immediately.

## Geolocation and Control-File Pipeline

`GetGeoLocations.py` owns media metadata extraction, sidecar JSON writing,
sorted list creation, reverse geocoding, and merge operations.

Important callable entry point:

- `run_with_options(...)`

Important constants used by the GUI:

- `GPS_NOT_AVAILABLE`
- `PLACE_NOT_AVAILABLE`
- `PLACE_FAILED`
- `PLACE_NOT_REQUESTED`

Use these constants instead of hard-coded text when deciding whether GPS or
place data is missing.

The normal Create action calls `GetGeoLocations.py` with equivalent options:

- project directory as positional photo directory
- `photolist=<projectname>-sorted.lst`
- `tracks=<projectdir>/trackimages/<projectname>-summary.json`
- track-order sorting based on the GUI track ordering selection

Created files in the project directory:

- `<projectname>.lst`: unsorted/intermediate list
- `<projectname>-sorted.lst`: slide-show control file used by the slide show
- `<mediafile>.json`: per-media sidecar metadata

Merge actions use:

- `merge_tracks=<summary-json>`
- `merge_media=[media paths]`

Merge logic should avoid duplicate media and duplicate track maps. Track map
duplicate detection must normalize legacy/new track plot numbering.

The GUI also performs a preflight sync check for the slide-show control file:

- current track maps missing from the control file
- old `#Overviewmap:` or `#Map:` entries that no longer match the current track
  summary
- map entries whose files no longer exist

The Slide Show Control File status line reports these problems. The
`Update Tracks` button shows the exact maps to insert and old entries to remove,
and can remove obsolete entries before calling the GetGeoLocations merge path.

Reverse geocoding uses a temporary list so the user-edited sorted list is not
overwritten. After completion, the GUI updates place names in memory from
sidecar files and the user can save the control table.

## Slide-Show Control File Format

The control file is a text file, normally `<projectname>-sorted.lst`.

Supported line types:

- normal media line: image or video filename plus time/GPS/place columns.
- `#Overviewmap: filename`: overview map image.
- `#Map: filename`: per-track map image.
- `#Date:` or `#Datum:`: date section line.

The GUI table maps these to row types:

- `IMG`: image
- `VID`: video
- `MAP`: overview map
- `TRK`: track map
- `DAT`: date row

`parse_slideshow_control_line(...)` and `serialize_slideshow_control_row(...)`
are the key conversion functions. Keep them compatible with
`GetGeoLocations.py`.

## GPX Editor Integration

The GUI opens `GPXEditor.py` with:

- `show_gpx_editor_from_cli_args(...)`
- `on_close` callback
- `on_save` callback

The editor must return the most recently saved GPX path. The main GUI then
updates the GPX field, refreshes GPX statistics, regenerates the track summary
JSON, refreshes Track Maps status, and refreshes the control-file track-map
sync status.

The main GUI's `PDF Summary` button calls
`export_pdf_summary_from_paths(...)`. That function creates a temporary editor
controller, loads the current GPX file, and opens the same PDF save panel and
accessory options used by the GPX Editor's own PDF button.

If multiple GPX files are selected, the GUI passes all existing files as input
and passes a single output file. The editor combines/edit tracks and saves one
active GPX.

## GPSTrackShow Slide Show

`GPSTrackShow.py` is a standalone AppKit slide-show program.

The GUI launches it with:

```bash
python GPSTrackShow.py <project-dir> --inputlist <projectname>-sorted.lst --trackdir <project-dir>/trackimages
```

It reads:

- media files from the project directory
- the sorted list passed by `--inputlist`
- track and overview map images plus sidecars from `--trackdir`

It supports keyboard navigation, media overlays, clock/place overlays, and map
windows. Since it is a separate process, quitting it must not affect the main
GUI.

## GPXEditor Architecture

`GPXEditor.py` is both standalone and embeddable.

Main entry points:

- `show_gpx_editor(...)`
- `show_gpx_editor_from_cli_args(...)`
- `export_pdf_summary_from_paths(...)`
- `run_gpx_editor_from_cli_args(...)`

Core classes:

- `TrackRecord`: in-memory track plus source XML and cached metrics.
- `PointInfo`: one parsed GPX point.
- `GPXEditorController`: main editor controller.
- `EditorTableDataSource` and `EditorTableView`: main track table.
- `PlotView`: overview/track map display with cursor, zoom, selection, delete,
  cut, and anchor handling.
- `ElevationProfileView`: elevation profile window linked to a plot view.
- `TrackInspectorController`: waypoint-level editor for one track.

The editor writes:

- one combined `.gpx` file in current table order
- `.bak` backup when overwriting an existing GPX file
- temporary recovery autosave at
  `/tmp/myCamino-GPXEditor-recovery.gpx`
- optional PNG map exports
- optional PDF report exports

Auxiliary windows must be tracked explicitly and closed during editor shutdown.
This includes plot windows, elevation profile windows, and inspector windows.
Do not rely on AppKit cleanup alone.

## Metadata and Sidecars

`plot_metadata_utils.py` provides JSON helpers and coordinate transforms shared
by plots, media, and the slide show.

Common sidecar patterns:

- `<image-or-video>.json`: media metadata from `GetGeoLocations.py`
- `<plot>.json`: plot metadata from `gpx_tracks_table.py`
- `<projectname>-summary.json`: track summary used by control-file creation

Plot sidecars include enough information to map latitude/longitude to image
pixels. Media sidecars include filename, datetime, GPS, place, and extraction
debug/status fields.

## Developer Notes

- Prefer internal Python calls for `GetGeoLocations.py` and
  `gpx_tracks_table.py`.
- Keep `GPSTrackShow.py` isolated as a subprocess.
- Use AppKit on the main thread for UI changes.
- Long operations should run in worker threads and update the GUI through
  `performSelectorOnMainThread...`.
- Use progress bars only while long operations are active.
- Keep table preview thumbnails optional and cached.
- Maintain compatibility between sorted-list parsing in `GPSTrackShowGUI.py`
  and sorted-list writing/merging in `GetGeoLocations.py`.
- Avoid changing the default GPX path after the user manually edits the GPX
  field.

## Packaging

Use `./build_dmg.sh` to build the distribution from the CLI. It performs syntax
checks, builds the slide-show player, builds the standalone GPX editor app,
builds the main GUI app, creates the DMG root, writes the DMG, and verifies it.

Relevant specs:

- `GPSTrackShow.spec`: bundled slide-show player executable.
- `myCamino GPX Editor.spec`: standalone GPX editor app.
- `myCamino GPS Track Show.spec`: main GUI app with bundled player resources.
