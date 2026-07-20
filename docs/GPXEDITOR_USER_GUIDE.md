# myCamino GPX Editor User Guide

## License and source code

The GPX Editor is part of the GPL-3.0-or-later licensed myCamino project. Open
**Help** and use **License**, **Third-Party Notices**, or **Source Code** to read
the documents included with the installed application.

`myCamino GPX Editor` edits one or more GPX files and saves one combined GPX
file in the current table order. It is a native macOS Cocoa application and can
run standalone or be opened from `myCamino GPS Track Show`.

After installation, start the editor by clicking the **myCamino GPX Editor**
icon in Applications. Because the beta is unsigned, macOS may say that Apple
cannot check it for malicious software. Dismiss that warning without moving the
app to the Bin. Within about one hour, open **Apple menu → System Settings →
Privacy & Security**, scroll to **Security**, click **Open Anyway**,
authenticate, and confirm **Open**. This exception is separate from the one for
the main GPS Track Show app. After the one-time approval, a normal click opens
the editor. Only approve a DMG obtained from the official myCamino website whose
SHA-256 checksum matches the published value. See
[Apple's Gatekeeper instructions](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac).

Experts working directly from the source code can instead use the command-line
interface (CLI):

```bash
./.venv/bin/python GPXEditor.py
./.venv/bin/python GPXEditor.py input.gpx --output-file output.gpx
```

When opened from the main GPS Track Show GUI, it receives input GPX files and a
default output file automatically.

If the editor is already open from the main GUI, pressing Add & Edit Tracks
again brings the existing editor window to the front instead of opening a
second editor.

## Main Window

The main window contains:

- Output GPX file field and folder button.
- Project name field.
- Track table.
- Selection field.
- Action buttons.
- Status line.
- A gear button beside the logo for editor settings.

The output field shows where Save and Save & Exit will write the GPX file.
Edit the field, press Enter, or use the folder button to choose an output file.

The project name is stored in GPX metadata when saved.

## Settings

The gear button opens **GPX Editor Settings** with three sections:

- GPX Processing: separate horizontal and elevation smoothing, retained-point
  spacing, horizontal/vertical error and HDOP/VDOP limits, timestamp fallback,
  recovery interval, and interactive map behavior.
- PDF Export: document/map resolution, zoom, and tile limits.
- Map Service: OpenStreetMap, Esri, or a custom tile service.

Numerical values can be typed or changed with the adjacent up/down stepper.
Each row has a reset arrow, and Reset All resets only these editor sections.
Custom map services require an HTTP(S) URL containing `{z}`, `{x}`, and `{y}`
plus attribution.

When the editor runs standalone, Apply stores these settings in macOS
Application Support for the next session. When opened from myCamino GPS Track
Show, Apply updates and auto-saves the active Adventure instead. Parameter
changes do not mark the GPX track data as edited.

## Loading Tracks

Use Add Tracks to choose one or more `.gpx` files.

Behavior:

- Multiple files can be selected.
- Selection order is preserved.
- The first loaded GPX basename becomes the project name if the table was
  empty.
- Original GPX structure and metadata are preserved as much as possible.
- Every track receives a unique `Nr.` that is not changed by sorting.
- Large files are processed one track at a time. The status line and progress
  bar show the current track while the window remains responsive; Help and
  Quit remain available during loading.

If an autosave recovery file exists from a previous session, the editor offers
to load it.

## Track Table

The table shows one row per track plus a final summary row.

Columns include:

- Row
- Nr.
- Show
- Name
- Date & Time
- Length
- Duration
- Sum
- Distance
- Avg Speed
- Ascent
- Descent
- NPoints

Important behavior:

- Name and Date & Time are editable.
- Show controls whether a track is included in statistics, overview plots, and
  PDF output.
- Numeric values, plots, Track Maps, PDFs, and Time-Lapse timing all use the
  same segment-aware processed geometry. Raw GPX points remain unchanged.
- Horizontal coordinates are smoothed over 10 m by default, and retained
  output points are spaced by at least 10 m. Elevation uses its own 50 m
  smoothing distance because GPS height is less precise.
- Explicit horizontal/vertical uncertainty and HDOP/VDOP can reject unreliable
  coordinates or elevations independently. Missing quality values are allowed.
  A value of zero disables the corresponding filter or smoothing operation.
- Ascent and descent use the processed elevation profile so normal altitude
  jitter is not counted as repeated climbing.
- Distance is measured from the current anchor point.
- The final row summarizes visible tracks.
- Click a column header to sort.
- If multiple rows are selected, sorting reorders only the selected rows.
- Drag selected rows to reorder tracks manually.
- Backspace/Delete deletes selected tracks after confirmation.
- Double-click a track row to open the waypoint inspector and raise the track
  plot plus the associated elevation profile.

The processing summary above the table always shows the active XY smoothing,
spacing, elevation smoothing, error, and DOP limits. At the right end of the
waypoint inspector, the compact **XY use** and **Elevation use** columns explain
how each raw point was processed. `Used` means retained in the processed
geometry, while `Smooth` is a valid point used only during smoothing. `Interp`
includes the reason for elevation interpolation; other short values identify
rejection reasons. The inspector header shows retained/raw point counts.

Date sorting uses a special rule for tracks with missing, zero, or invalid
duration: those tracks are placed by distance from the anchor among the regular
date-sorted tracks.

## Track Selection

Use normal table selection with mouse clicks, Shift-click, or drag.

The selection field accepts track numbers and ranges:

```text
1,3-5,8
```

Buttons:

- Select All: select all tracks.
- Unselect All: clear the selection.

Selected tracks affect Join Tracks, Plot Track(s), Plot Overview highlighting,
and some exports.

## Saving

Buttons:

- Save: save to the current output file.
- Save & Exit: save and close the editor.
- Quit: ask whether to save unsaved changes.

If the output file already exists, the editor creates a `.bak` backup before
overwriting it.

The editor periodically writes a recovery autosave to:

```text
/tmp/myCamino-GPXEditor-recovery.gpx
```

Clean saves remove the recovery file.

## Editing Track Metadata

Editable table fields:

- Name
- Date & Time

Changing a track date/time updates track metadata and shifts track point times
as needed. If timing information is incomplete, the editor estimates timestamps
from distance and speed.

Undo and Redo support recent main-table actions.

## Joining Tracks

To join tracks:

1. Select at least two tracks.
2. Press Join Tracks.
3. Choose which selected track provides the metadata.

The editor merges the selected tracks into the first selected track and removes
the other selected tracks from the table. Metrics and plots are refreshed.

## Anchor Point

The anchor point is used for distance calculations.

Ways to set it:

- Press Set Anchorpoint to use the first point of the first selected track.
- In a plot window, move the cursor to a point and press `a`.

The anchor is stored in GPX metadata/extensions when saved.

## Plot Overview Window

Press Plot Overview to open an OpenStreetMap overview.

Behavior:

- Shows all visible tracks.
- If tracks are selected, the overview zooms to them and highlights them.
- Selecting tracks in the table updates the highlight.
- Click a track on the map to select it in the table.
- Double-click a point to open that track and waypoint in the inspector.

Common keys:

- `i`: toggle point information overlay.
- `h`: show help.
- `a`: set anchor at current cursor point.
- `+` / `-`: zoom in/out.
- Cmd-+ / Cmd--: zoom two steps in or out.
- `c`: center on cursor.
- `z`: zoom to current selection.
- Shift-`Z`: reset to full map extent.
- `p`: save current plot as PNG.
- `u`: clear plot selection.
- `e`: open or focus elevation profile.
- Cmd-Z / Shift-Cmd-Z: undo or redo the last track edit.
- Cmd-X: cut a track at the current cursor point in a track plot.
- `q`: close plot window.

Mouse:

- Click or drag to move the white cursor dot to the nearest waypoint.
- Scroll/pan behavior depends on the plot mode and current map view.

## Plot Track(s) Window

Press Plot Track(s) to open a detailed map for selected tracks.

Behavior:

- Shows selected track maps.
- Arrow keys switch between selected tracks.
- Click or drag to move the cursor to the nearest waypoint.
- Double-click a point to open the waypoint inspector.

Track editing keys:

- `m` or Shift-click: set a marker.
- `m` does not open the point information overlay.
- Delete/Backspace: delete the point range from marker to cursor after
  confirmation.
- `x`: cut the track at the cursor after confirmation.
- `a`, `i`, `h`, `+`, `-`, `c`, `z`, Shift-`Z`, `p`, `e`, `q`: same general behavior
  as the overview plot.

When points are deleted or a track is cut, the table and open plots are updated.
If an inspector table edit changes or deletes points, the open track plot and
elevation profile for that track are refreshed.

## Elevation Profile Window

Plot windows can open an elevation profile.

The profile shows elevation versus distance and follows the current plot cursor
and selection. It can also open the waypoint inspector by double-clicking when
appropriate.

Press `h` to open the separate elevation-profile key reference. It closes a
few seconds after the key is released. The profile supports the same cursor,
selection, editing, track-navigation, PNG export, undo, and redo controls as
the track plot. In addition, `y` fits the height axis to the elevations in the
currently visible distance range with five percent headroom above and below.
Press `0` to restore the zero-based default height axis; this does not change
the current distance zoom.

When the editor closes, plot and elevation windows close with it. Closing a
track plot also closes its associated elevation profile, and closing an
inspector closes the plot/elevation windows associated with that track.

## Waypoint Inspector

Open the inspector by:

- Double-clicking a track row.
- Pressing Inspect Track after selecting one track.
- Double-clicking a point in a plot window.

The inspector shows all waypoints of one track.

Inspector actions:

- Edit waypoint cells and press Enter.
- Delete selected waypoints with Backspace/Delete after confirmation.
- Undo inspector edits.
- Plot the inspected track.
- Save inspector edits to memory.
- Save & Exit to save edits to memory and close the inspector.
- Readjust Time to recalculate selected waypoint timestamps.
- Split Track to split at the selected waypoint.

The main editor Save writes all accepted inspector edits to disk.

## File Exports

PNG:

- Saves the current plot.
- If a multi-track plot is active, the editor can save multiple track PNGs.

PDF:

- Exports the track table.
- Lets you choose included columns.
- Can include overview and track map pages.
- Can include elevation profiles.
- Supports orientation and map rotation options.
- The main GPS Track Show GUI exposes the same export panel as `PDF Summary`
  next to the Start button.

View File:

- Opens the source GPX file of the selected track in TextEdit.
- If unsaved edits exist, the editor asks whether to save first.

## Files Created by GPXEditor

Normal output:

- `<output>.gpx`: combined edited GPX.
- `<output>.gpx.bak`: backup of overwritten output.

Temporary recovery:

- `/tmp/myCamino-GPXEditor-recovery.gpx`

Optional exports:

- PNG plot images.
- PDF reports.

Embedded use from GPS Track Show:

- The editor returns the most recently saved GPX file to the main GUI.
- The main GUI uses that returned path as the active GPX track file.
- The main GUI then regenerates the track summary file, checks which track maps
  are stale, and checks whether the slide-show control file needs track-map
  updates.

## Troubleshooting

- If Save does nothing, check that an output file is set.
- If plotting is unavailable, required plotting dependencies may be missing.
- If map tiles are slow, the editor may be waiting for OpenStreetMap tile
  downloads.
- If a track has no time information, date edits may estimate point timestamps.
- If the main GUI opened the editor, use Save or Save & Exit so the saved GPX
  path is returned to the main GUI.
- If settings cannot be stored, check write access to the Adventure directory
  or to `~/Library/Application Support/myCamino GPX Editor` in standalone mode.
