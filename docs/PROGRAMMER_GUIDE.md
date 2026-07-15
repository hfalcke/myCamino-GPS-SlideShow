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
- `adventure_parameters.py`: typed project parameter registry, defaults,
  normalization, validation, and versioned `.adv` payload support.
- `cocoa_parameter_editor.py`: reusable section-filtered native parameter UI.
- `json_storage.py`: atomic Adventure/preference writing and standalone
  parameter-subset loading.
- `map_provider_utils.py`: shared Contextily provider construction and bounded
  tile-request handling.
- `audio_playlist.py`: recursive audio discovery, `$`-label playlist
  parsing/generation/update, album membership, and pure transport progression.
- `slideshow_control_format.py`: strict CSV-style `#MUSIC:` parsing and command
  normalization.
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
- `WorkflowAssistantBubbleView`: retained in-window speech-bubble overlay for
  first-time workflow guidance.
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
- Show-type selector, Start/PDF/Quit controls, status line, and progress
  bar. Help is in the header beside Settings.

Each section has a non-editable status checkbox. It is used as a visual
completion indicator:

- Adventure: project directory exists.
- GPX Files: at least one GPX file exists.
- Track Maps: overview and all expected track maps exist and are current.
- Photos and Video Clips: at least one supported media file exists.
- Slide Show Control File: control file exists and contains at least one image
  or video.

`_workflow_readiness()` is the shared readiness source for these indicators
and the workflow assistant. Do not add a separate assistant-only file check;
otherwise the bubble and colored marks can disagree. The pure helpers in
`workflow_assistant.py` normalize persisted state, choose the first incomplete
stage, and calculate an in-window bubble placement suitable for unit tests.

New Adventures save `workflow_assistant.enabled`,
`workflow_assistant.place_names_completed`, and
`workflow_assistant.slideshow_started`. Loading an older format-2 Adventure
without this optional object treats the two action markers as complete while
still evaluating file-backed readiness. A successful Add Place Names run sets
a transient pending flag; only saving the changed control table commits the
marker. A successful player process launch commits the slideshow marker.

## Adventure Files

Adventure files use the extension `.adv`, contain JSON, and require
`adventure_format_version: 2`. Older formats are deliberately unsupported.

The GUI saves and loads them with:

- `save_project_configuration()`
- `load_project_configuration(...)`
- `_activate_project_directory(...)`

`adventure_files.py` validates and discovers format-2 files, sorts them by
modification time, and implements transactional rename/copy operations. An
empty directory remains active without an Adventure until its suggested or
typed name is committed. After creation, `mark_dirty()` schedules a 500 ms coalesced auto-save. Discrete
operations use the same path, while callers that need durability immediately
flush it. `atomic_write_json(...)` writes and fsyncs a temporary sibling before
`os.replace(...)`. `current_project_file`, `gpx_file`, `control_file`, and
`track_map_base` are authoritative. Adventure-name edits are never autosaved
as ordinary text changes; committing them invokes Rename/Copy/Cancel.

`slideshow.start_mode` stores the preferred Standard or Time-Lapse selection.
`slideshow.window_mode` stores `auto`, `single`, or `multiple`. Automatic mode
resolves against `NSScreen.screens()` in the player: one screen uses one window
and two or more screens create the separate overview window. Legacy
`slideshow.map_window` booleans are migrated when an Adventure is loaded:
the former enabled default becomes `auto`, while disabled becomes `single`.
`slideshow.track_map_before_media` defaults to false and controls the optional
single-window Standard preview of a marked track map before every medium.

Stored values include:

- project name
- project directory
- format version 2
- project-relative GPX and control-file basenames
- Track Map output base
- last picture import directory
- `time_lapse_media_min_fraction` (currently stored with a default of `0.5`;
  the legacy `time_lapse_media_max_fraction` key is accepted while loading)
- description and other GUI state that belongs to the adventure
- optional `music_source` and `music_playlist` paths; paths inside the project
  are stored relative and external paths are absolute
- `parameters`: a versioned object containing normalized values from the
  central registry

The three development Adventures were migrated directly to format 2; runtime
legacy Adventure migration is intentionally absent. Parameter-registry
normalization remains independent of the Adventure file-format version.

## Adventure Parameter Editor

`adventure_parameters.py` is the only source of parameter defaults. Each
`ParameterSpec` defines its key, section, label, type, range or choices, inline
help, unit, and whether it is advanced. `CocoaParameterEditor` generates the
native controls from this registry and edits a draft until Apply. The main GUI
shows every section; GPX Editor filters it to GPX Processing, PDF Export, and
Map Service.

The registry currently propagates settings to:

- `GPSTrackShow.py` through explicit CLI options for both playback modes.
- `gpx_tracks_table.py` for filtering, sorting, rendering, provider choice,
  timeout, dimensions, and styling.
- embedded `GPXEditor.py` instances for autosave, map behavior, PDF rendering,
  provider choice, cache retention, timestamp fallback, and shared GPX
  processing.
- `GetGeoLocations.py` for known-place radius, timeout, and request pacing.

Track-map sidecars store `adventure_render_parameters`; the track summary stores
`adventure_processing_parameters`. Freshness checks compare these normalized
signatures. Legacy outputs remain current only while the corresponding settings
still equal their old defaults.

## Shared GPX Processing

`gpx_processing.py` is the single source for GPX geometry and quality metrics.
It defines `ProcessingOptions`, `RawTrackPoint`, `ProcessedPoint`,
`ProcessedSegment`, and `ProcessedTrack`. The processor keeps `<trkseg>`
boundaries, filters horizontal and vertical quality independently, smooths XY
and elevation over separate route-distance kernels, applies retained-point
spacing, interpolates missing/rejected elevations only between valid anchors,
and computes length/ascent/descent from the resulting geometry.

Consumers must not recalculate point-to-point geometry independently.
`GPXEditor.py`, `gpx_tracks_table.py`, `gpxlist.py`, `track_timing_utils.py`,
PDF output, Track Maps, and slideshow timing all use the shared output.
`elevation_metrics.py` remains only as a compatibility re-export. Editor caches
are keyed by the semantic track fingerprint and complete processing-parameter
signature; XML edits invalidate only the affected track. Parameter changes are
processed on one background worker using XML snapshots, with AppKit updates
returned to the main thread.

Summary and map sidecars record processing options, raw/retained counts,
rejection counts, segment-preserving geometry, processed elevation, and timed
points. Retained point records also preserve explicit horizontal/vertical
uncertainty, HDOP, VDOP, PDOP, satellite count, and fix type when present; PDOP,
satellites, and fix are metadata only in this version. Every processing option is also part of GUI freshness signatures, so
changing a threshold or kernel marks summaries and Track Maps for Update.

When a project directory is selected, the GUI creates it if needed, discovers
all valid `.adv` files, and loads the newest one. Adventure, GPX, and control
file use editable combo boxes populated from that directory. Explicit stored
references, not the visible title, drive GPX processing and slide-show output.

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

The global `*-summary.json` intentionally contains only compact per-track
statistics, endpoints, processing settings, fingerprints, and plot filenames.
Point-by-point processed geometry and `timed_track_points` belong only to the
matching per-track map sidecars. This avoids duplicating hundreds of megabytes
of data in large Adventures while preserving the timing data needed by the
Time-Lapse player.

After `GPXEditor.py` saves an accepted changed GPX file, the main GUI regenerates
the track summary JSON with `run_gpx_tracks_table_with_options(...)` without
rendering maps. Closing the editor does not repeat that work when the same file
version was already handled or the GPX file was unchanged. This keeps
control-file operations consistent while leaving slow map rendering under
explicit user control.

The GUI deliberately tolerates legacy plot file names when detecting existing
plots. Matching code normalizes track plot names so old zero-padding variants
are not counted as missing duplicates.

Track Maps buttons:

- `Create`: render the shared overview plus every map of the variant selected
  by `for Time-Lapse`. It confirms before overwriting existing images.
- `Update`: open the selection window with missing/stale maps marked by `*`,
  preselected, and copied into the range field.
- `for Time-Lapse`: selects `map_layout="time-lapse"`; unchecked selects
  `map_layout="standard"`. It is persisted as `track_maps_for_time_lapse`.
- `View`: open the preferred variant for every track, with the other variant as
  fallback.
- folder icon: open `trackimages/` in Finder.

Before rendering, obsolete numbered track-map `.png` and `.json` files are
removed if they no longer match any current GPX track. Both valid variants are
retained. The cleanup intentionally does not remove the overview image, summary
JSON, or unrelated files.

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
- `<mediafile>.json`: per-media sidecar metadata, where `<mediafile>` includes
  the original extension (`IMG_4104.mov.json`, not `IMG_4104.json`)

Use `migrate_media_sidecars(project_dir)` or the
`--migrate-media-sidecars` CLI flag to upgrade old stem-only media sidecars.
Migration accepts a legacy sidecar only when its `source_filename` and
`photo_path` identify exactly one current media file; ambiguous metadata is
preserved under a `.legacy-sidecar` filename instead of being overwritten.

Merge actions use:

- `merge_tracks=<summary-json>`
- `merge_media=[media paths]`

Merge logic should avoid duplicate media and duplicate track maps. Track map
duplicate detection must normalize legacy/new track plot numbering.

Initial sorting and Merge New Media share `assign_adjacent_day_track(...)`.
Only media dates without an exact-date track are considered. GPS media must be
within `0.5 * track_length` of the next track's start (`before`) or previous
track's end (`after`); media without GPS is accepted by date. Candidate ties
prefer the next track. `build_control_sections(...)` emits qualifying media as
`#MapBefore:` or `#MapAfter:` sections. A third pass turns remaining GPS-bearing
date groups into `#MediaMap:` sections and inserts each complete section by
date without splitting a before/track/after group. Groups without any usable
GPS coordinate remain mapless. Merge applies the same classification without
reordering existing user-edited rows.

Media-only maps are written to `trackimages` as
`<control-base>-media-YYYY-MM-DD.png` for Standard and
`<control-base>-media-YYYY-MM-DD-timelapse.png` for Time-Lapse, with matching
JSON sidecars. The control file always keeps the canonical Standard filename;
playback resolves the preferred variant with fallback exactly like a track map.
They use the same map provider and rendering parameters as track maps, contain
a date-only header, and store `map_kind: media`, all media coordinates,
normalized clear boxes, and coordinate-to-pixel metadata. Per-track and media-only map extents
have a fixed 10 km minimum short dimension. This value is a conservative lower
bound derived from the smaller dimensions of the existing Santiago standard
track maps; it prevents very short routes or tightly grouped photos from
producing an excessively magnified, low-context map. Overview maps are not
affected.

Time-Lapse media maps call the same `optimized_track_extent(...)` and clear-box
functions as track maps, passing time-ordered media coordinates in place of GPX
points. Standard media maps use the same centered extent as Standard track
maps. `render_media_map_specs(...)` is the only variant-aware media-map render
path and is shared by initial control-file creation, Merge New Media, and Track
Maps Create/Update. Merge classification calls `build_control_sections(...)`,
so exact-date, adjacent-day, and remaining-media passes cannot drift from
initial creation.

The GUI also performs a preflight sync check for the slide-show control file:

- current track maps missing from the control file
- old `#Overviewmap:` or `#Map:` entries that no longer match the current track
  summary
- map entries whose files no longer exist

The Slide Show Control File status line reports these problems. The
`Sync Track Maps` button shows the exact canonical maps to insert and old entries to remove,
then performs removal and insertion in a temporary sibling list. The active
control file is replaced atomically only after the complete merge succeeds;
cancelled or failed merges leave it unchanged. The reusable Cocoa table window
is retained and hidden on close so reopening it never addresses a released
Objective-C window.

`SlideShowControlTableWindow.performKeyEquivalent_(...)` handles `Cmd-F` and
`Shift-Cmd-F` at window scope, including while a cell or the search field has
focus. `control_table_search_indexes(...)` searches serialized control rows so
the UI matches the actual `.lst` representation. Navigation selects and
scrolls the matching row into view and wraps in both directions.

`SlideShowControlTableView.menuForEvent_(...)` selects the right-clicked row
and constructs the row-editing context menu. Preview and Finder actions use
`resolve_control_row_path(...)`, including Standard/Time-Lapse map fallback;
Finder receives file URLs through `activateFileViewerSelectingURLs_(...)`.

Control-file viewer items retain their source row index. Every viewer
navigation calls `sync_control_table_to_media_viewer_item(...)` to select and
scroll that row. Video items use one retained `AVPlayer`/`AVPlayerView` with
inline controls; switching items pauses, detaches, and clears the previous
player before creating another one.

`_resolve_unsaved_control_table_changes()` is shared by the editor Close
action and application termination. It first commits any active cell editor,
then offers Save, Don't Save, or Cancel. Don't Save reloads the on-disk list
and removes the recovery state so reopening cannot expose discarded rows.

Reverse geocoding uses a temporary list so the user-edited sorted list is not
overwritten. After completion, the GUI updates place names in memory from
sidecar files and the user can save the control table.

## Slide-Show Control File Format

The control file is a text file, normally `<projectname>-sorted.lst`.

Supported line types:

- normal media line: image or video filename plus time/GPS/place columns.
- `#Overviewmap: filename`: overview map image.
- `#Map: filename`: per-track map image.
- `#MapBefore: filename`: associated track map for media on the day before.
- `#MapAfter: filename`: associated track map for media on the day after.
- `#MediaMap: filename`: date-only location map for media not assigned to a track.
- `#Date:` or `#Datum:`: date section line.
- `#MUSIC: command, target, ...`: ordered non-display music transport commands.

Media lines have exactly the normal filename/time/GPS/place fields; music is
never attached to another row. `parse_music_directive(...)` uses strict CSV
quoting, normalizes case-insensitive `$LABEL` references, rejects malformed
command syntax, and leaves unresolved labels/files for runtime warnings.
GetGeoLocations merge/sync and Adventure rename/copy keep `#MUSIC:` entries as
ordinary ordered directives and never interpret their payload as a map path.

The control-table window intercepts Command- and Control-C/X/V before row
commands and sends them to the shared field editor whenever a cell is actively
being edited. Media filtering does not call AppKit's unreliable hidden-row API:
the data source publishes an explicit view-to-model index list instead. Every
selection, edit, search, preview, and drag callback translates through that
list, while serialization continues to use the complete unchanged model.

The GUI table maps these to row types:

- `IMG`: image
- `VID`: video
- `MAP`: overview map
- `TRK`: track map
- `BEF`: day-before track map
- `AFT`: day-after track map
- `LOC`: media-only location map
- `DAT`: date row
- `MUS`: music directive

The editable Type combo displays the canonical short code in its closed cell
and `<code> - <description>` in its dropdown menu.
`normalize_control_row_type(...)` keeps only the canonical short code in the
row model, keeping serialization independent of the UI label.

`parse_slideshow_control_line(...)` and `serialize_slideshow_control_row(...)`
are the key conversion functions. Keep them compatible with
`GetGeoLocations.py`.

Opening the control-file editor derives the Track Map summary path directly
from `track_map_base`; it must not prepare or parse the GPX file. The retained
editor model is reused when the control-file path, size, and nanosecond
modification time are unchanged and no newer recovery copy exists. Save,
Discard, and Revert must keep that signature state synchronized.

## GPX Editor Integration

The GUI opens `GPXEditor.py` with:

- `show_gpx_editor_from_cli_args(...)`
- `on_close` callback
- `on_save` callback
- `on_initial_load_complete` callback

While a newly created editor performs its staged initial track load, the main
GUI pauses only the GPX part of its asynchronous project-status refresh. The
shared `threading.Event` is checked through `parse_gpx_file(...)` before each
track, so waiting releases the GIL and obsolete status generations can stop.
The editor's one-shot initial-load callback resumes the status refresh as soon
as its table is ready, including empty-input, failure, cancellation, and close
paths. Merely raising an existing editor never pauses status processing.

The asynchronous status refresh creates one `gpx_tracks_table` preparation
context and derives both the GPX summary label and Track Map freshness from
its tracks. Do not add a separate `_format_gpx_summary(...)` traversal to that
path; synchronous callers may continue to use it directly.

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

`BackgroundMusicController` retains at most two `AVPlayer` instances for
crossfades and uses one 250-ms polling timer for title completion. It parses
`#MUSIC:` rows once and delegates deterministic title progression to
`MusicTransportState`. It must be disposed before presenter and window
teardown. Standard and Time-Lapse activation paths call `synchronize_row()`;
Time-Lapse also executes directives crossed internally before each media event.
Backward navigation and Continue reconstruct gate, volume, queue, and loop
state by replaying preceding directives without rendering slides or repeatedly
crossfading. Videos and Space pause audio transiently, while `a` changes the
independent user-enabled state. This manual state always has priority over the
control-file gate.

The transport supports ordered target queues that return to the interrupted
playlist title and time position, permanent playlist jumps, continue,
single/line/range/album/all loops, a 0–9 gain level, and `#ON/#OFF`. Targets
open the control gate; volume and gate actions do not cancel transport modes.
The player reports unresolved targets but keeps the current title running.

Player entry points accept `music_source`, `music_playlist`, and
`audio_crossfade_seconds`; CLI equivalents are `--music`, `--music-playlist`,
and `--audio-crossfade-seconds`. A directory without an explicit playlist
looks for the control-list stem (without `-sorted`) plus `.playlist`, then
falls back to recursive case-insensitive relative-path order.

`GPSTrackShowWindowDelegate` gives every Cocoa window a stable `photo` or `map`
role. Closing the primary photo window quits playback. Closing the secondary
map window calls `_deactivate_separate_map_window()` and reroutes playback into
the photo window. Window closure and disposal of heavyweight presenter/view
content are deferred until `windowWillClose_` has returned to avoid PyObjC
autorelease-pool crashes. The
`w` key creates or removes this secondary window at runtime. Secondary windows
use `NSWindowCollectionBehaviorFullScreenAuxiliary`, allowing one created after
startup to appear over the primary full-screen Space.

Programmatic removal with `w` does not close the native window. It removes an
optional child-window relationship, calls `orderOut_()`, and stores the intact
window/view/presenter/delegate group in `parked_map_resource`. A later `w`
restores that same group instead of allocating another Cocoa window. This
avoids both AppKit full-screen lifecycle races and zombie PyObjC wrappers.

If the user closes the map through its window control, AppKit has already begun
the native close. In that path, disposal of heavyweight presenter/view content
is deferred until `windowWillClose_` has returned. Timer callbacks are bound
controller methods and never closures that capture Cocoa objects. Lightweight
bridged wrappers remain retained in `retired_map_resources` until the player
process exits. All created windows also set `releasedWhenClosed` to false.
These lifetime rules prevent Python 3.14/PyObjC from calling `objc_release` or
`object_getClass` for an AppKit object that has already been destroyed.

### Stage Time-Lapse Mode

`--time-lapse-stages` starts the player in stage-map mode. A stage consists of
one map directive and its following media rows until the next map directive. The stage
map is rendered by a retained `TimeLapseMapView`; the overview is drawn in the
map role with the complete active route and current position. This avoids
allocating a new full-screen image for every 20 ms animation tick.

`parse_map_directive(...)` distinguishes canonical stages from
`#MapBefore:`/`#MapAfter:` and `#MediaMap:`. Adjacent-day and media-only
directives create static Time-Lapse
stages: the map is shown alone for one media duration, then media is shown
sequentially in route-aware white frames. Their route remains highlighted in
the overview, but they have no moving marker or travelled-distance metrics and
do not contribute to `_time_lapse_distance_before_stage(...)`. Media markers
use the media sidecar coordinates, with control-row coordinates as fallback.
Standard playback draws the fixed English relation title on the associated
map. Resume, history, `T`, and `w` use the common directive parser.

Media-only stages have no route highlight, moving pilgrim/arrow, distance, or
height metrics. Every displayed medium marks its GPS position on the location
map and overview. The location map uses the standard marker plus a fixed
45-degree arrow. In both regular and media-only Time-Lapse stages, markers and
arrows are drawn before the framed medium, so the medium may cover them. The
editor writes a debounced recovery file and minute-spaced
snapshots under `.mycamino-control-backups`; when a newer recovery file is
found, reopening the table offers to restore it. Saving replaces the real list
atomically and removes the active recovery file.

`--time-lapse-duration SECONDS` controls active route motion and defaults to
30 seconds. In time-lapse mode, the existing `--duration` setting is the
minimum display time for media. Timed media is
scheduled from its extension-aware media sidecar; untimed media remains in list
order at the end of its stage. `T` switches between normal and time-lapse mode
without intentionally skipping a media row. Entering time-lapse cancels the
standard continuation timer and presenter transitions, then hides every
standard content layer so both modes cannot animate concurrently.

With no map presenter, a fresh Time-Lapse stage first displays the overview in
the stage view as a framed medium for `--duration`, then starts route motion.
`draw_time_lapse_overview_media(...)` projects the active stage's repaired
track points through the overview sidecar coordinates before the rendered
overview is scaled into its media frame, preserving the route highlight.
`timelapse.overview_as_media=false` or `--time-lapse-overview-fullscreen`
retains the former full-window presentation. A resumed stage starts at its
saved progress without replaying that overview. Standard
single-window playback retains the overview, track-map, media sequence. The
shared transition-completion guard explicitly permits the temporary Time-Lapse
overview callback while rejecting stale Standard callbacks.

`--time-lapse-media-min-fraction FRACTION` sets the preferred minimum size of
the complete white-framed medium and defaults to `0.5`. It is not an upper
limit: each medium grows to the largest route-free rectangle available. Track
safety remains authoritative, so a congested map can still require a smaller
medium. The old `--time-lapse-media-max-fraction` spelling remains a compatible
alias. Per-track plotting supports `map_layout="standard"|"time-lapse"` and
`track_edge_margin_fraction`. Standard maps retain the canonical centered
extent and filename. Time-Lapse maps retain the same scale but evaluate legal
extreme extent shifts and use the `-timelapse` filename suffix. The shared
overview is never variant-specific.

`track_map_layout_utils.py` owns variant naming, obstruction rasterization,
corner frontiers, extent optimization, cache validation, and normalized/view
coordinate conversion. Plot sidecars store `media_clear_boxes` version 1 in
`image_fraction_bottom_left` coordinates. Each corner contains a largest-area
`maximum` and a Pareto `frontier`, together with image size, margin, grid size,
and track fingerprint. The player validates those fields and converts the
normalized rectangles into the current aspect-fit image rectangle. Legacy,
malformed, stale, or wrong-size caches fall back to the same shared runtime
calculation. Every medium is aspect-fitted against the frontier member that
produces the largest display area.

Canonical `#Map:` control rows continue to name the Standard file. Standard
playback resolves Standard then Time-Lapse; Time-Lapse playback resolves the
reverse order and loads the sidecar belonging to the selected image. Thus `T`
switching reloads the appropriate variant without duplicate control rows.

Media display deadlines run alongside the 20 ms motion tick. The arrow keeps
moving while one item is visible. Once its minimum duration has elapsed, the
item remains visible until the next medium replaces it or the stage ends. If
the next media fraction is reached too early, progress is held exactly there
until the minimum duration is complete. The arrow orientation is fixed for the
stage from its start/end line, and the established renderer keeps it
perpendicular to that line. While media is visible, its initial route location
is retained as a red dot with a white outline. Backward navigation stores playlist row
numbers rather than rendered `NSImage` objects. Normal mode reloads the prior
map or medium from its source, and time-lapse mode first walks back through the
media events of the active stage. This permits navigation beyond four items
without restoring the former full-resolution image retention. Arrow-key steps
never modify `manual_mode`; `_continue_time_lapse_after_navigation()` restarts
the 20 ms timer only when playback was already automatic and unpaused.

`TimeLapseMapView` owns a retained clock `NSImageView` above its optional
AVPlayer child. Clock content is rebuilt only when time, date, or view height
changes, not on each 20 ms tick. `create_clock_overlay_image(...)` applies a
50%-opaque black shadow offset down/right by one stroke width before drawing
the white clock and date. The clock time comes from the interpolated current
track-point time. Once progress reaches the track endpoint,
`time_lapse_clock_datetime(...)` advances it to the current medium's later
capture timestamp while leaving the marker at the endpoint. The view draws the
current medium's reverse-geocoded place in
one line across the bottom 5% of the aspect-fit map and draws total distance,
stage distance, and elevation as three right-aligned header lines. These text
layers are drawn directly by the retained view and do not allocate per-frame
overlay images.

### Slide-Show Resume Protocol

The GUI launches the separate player with a project-local `--state-file`.
During orderly quit, the player atomically writes the active zero-based control-
file row, its exact line text, mode, Time-Lapse progress, optional visible-media
row, control-file path, and timestamp. Natural completion writes
`completed: true`. The GUI watcher reads and removes this transient file on the
main thread, stores or clears `slideshow_resume_position` in the `.adv` payload,
and saves the adventure automatically.

Before a later launch, the GUI validates the stored control-file path, row
range, and line text. **Start** deliberately omits resume arguments and begins
at the beginning. **Continue** is enabled when saved state exists and passes
`--resume-index`, `--resume-progress`, and `--resume-media-index` after
validation, without a modal confirmation. The same optional values are available to
`config_from_options(...)` and `run_with_options(...)`. Standard playback
reopens the exact media/map row; Time-Lapse reconstructs the containing stage,
marker progress, and visible medium. Edited or replaced control files invalidate
the stored position safely.

`track_timing_utils.py` is the single timestamp-repair policy shared by the
GPX editor, `gpx_tracks_table.py`, and the player. It preserves usable GPX
times, interpolates missing intervals by cumulative distance, and falls back to
3.5 km/h when a duration cannot be derived. It returns repaired in-memory
values only. GPX files are changed only by an explicit GPX Editor save.

Track-map sidecars include `timed_track_points`, an ordered list of latitude,
longitude, ISO timestamp, estimated-time flag, elevation in metres, and
cumulative stage distance in kilometres. Use
`gpx_tracks_table.upgrade_timed_track_sidecars(...)` to add that payload to
matching current sidecars without rerendering a PNG. It verifies the existing
track fingerprint first and reports unsafe matches instead of guessing. This is
an explicit one-time migration or maintenance API; it is not called when the
slide show starts. Track Maps Create and Update write the complete payload as
part of normal map generation, while the player remains read-only and falls
back to in-memory distance-based timing for legacy or foreign sidecars.

The Adventure settings window now persists the global slide-show parameters in
`.adv` files: media and stage durations, time-lapse media minimum size,
transition, background/marker/arrow styling, font and clock/place overlays,
fullscreen/display swap/map-window/joined-window/repeat options, and collage
size and maximum. Playback key changes remain session-only by design.

`timelapse.marker_style` selects `pilgrim` (default) or `arrow`. The player
loads `pilgrim-frame00-rigged-512.png` through
`pilgrim-frame08-rigged-512.png` once and draws retained images in
`TimeLapseMapView`. Frame 0 is the standing pose; movement after a stationary
interval resumes at frame 3. Motion tolerance scales with the map view
diagonal, and the 0.1-second sprite cadence is independent of the 50 Hz GPS
position updates. The view calculates and caches one orientation per stage
from the same fixed normal used by the arrow. It mirrors the right-facing
source frames when their transformed facing direction opposes the route.
Only the stage-map view uses this setting; the overview view always retains the
arrow. Missing animation assets fall back to the arrow.

## GPXEditor Architecture

`GPXEditor.py` is both standalone and embeddable.

Main entry points:

- `show_gpx_editor(...)`
- `show_gpx_editor_from_cli_args(...)`
- `export_pdf_summary_from_paths(...)`
- `run_gpx_editor_from_cli_args(...)`

`show_gpx_editor(...)` and `show_gpx_editor_from_cli_args(...)` accept an
`on_settings_change` callback. Embedded settings are returned as the
editor-owned subset and merged into the active Adventure, which auto-saves
them. Standalone mode instead reads and atomically writes:

`~/Library/Application Support/myCamino GPX Editor/settings.json`

Core classes:

- `TrackRecord`: in-memory track plus source XML and cached metrics.
- `PointInfo`: one parsed GPX point.
- `GPXEditorController`: main editor controller.
- `EditorTableDataSource` and `EditorTableView`: main track table.
- `PlotView`: overview/track map display with cursor, zoom, selection, delete,
  cut, and anchor handling.
- `ElevationProfileView`: elevation profile window linked to a plot view. It
  shares selection and edit commands with `PlotView`, supports independent
  distance and visible-elevation scaling, and retains its own temporary help
  window and close timer.
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

- `<image-or-video>.json`: media metadata from `GetGeoLocations.py`; retain
  the media extension in this name to avoid JPEG/video stem collisions
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

For a complete tested release, use:

```bash
./release.sh -m "Describe the stable release"
```

`release.sh` shows the current branch and worktree, asks for confirmation, runs
`git diff --check` and the complete unit-test suite, calls `build_dmg.sh`, then
stages all repository changes, commits them, and pushes the current branch to
its configured upstream. Use `--yes` for a non-interactive invocation. The DMG
remains at `dist/myCamino-GPS-Track-Show.dmg`; `dist/` is ignored and therefore
the binary is not pushed to GitHub. A failed test or DMG build occurs before
Git staging and prevents the commit.

Relevant specs:

- `GPSTrackShow.spec`: bundled slide-show player executable.
- `myCamino GPX Editor.spec`: standalone GPX editor app.
- `myCamino GPS Track Show.spec`: main GUI app with bundled player resources.
