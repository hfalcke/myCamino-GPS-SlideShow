# myCamino GPS Track Show Programmer Guide

## Media-Derived Tracks and Planning

`media_track_builder.py` is the shared non-GUI pipeline for creating estimated
GPX tracks from media. It validates extension-aware sidecars, selectively calls
the existing metadata extractor, groups media by control-file stage or local
date, applies endpoint-preserving point spacing, and writes canonical GPX 1.1.
Generated tracks carry a `mycamino:trackOrigin` extension with
`kind="media-derived"` and `estimated="true"`. `gpx_tracks_table.py` reports
their `source_structure` as `media`.

`gpx_point_editing.py` contains segment-aware XML operations used by the point
table and Track Map: Web Mercator interpolation/extrapolation, insertion,
clipboard serialization, deletion, and same-segment movement. UI code should
use these helpers rather than duplicating GPX child-order or boundary logic.

`gpx_routing.py` defines the deliberately small future routing-provider
boundary. A future Valhalla, OSRM, or openrouteservice implementation returns
candidate points; it must not mutate editor XML directly. No external routing
server or API dependency is introduced in the current release.

## Licensing and release artifacts

Original project content is GPL-3.0-or-later, copyright 2026 Heino Falcke.
`LICENSE`, `COPYRIGHT`, `SOURCE_CODE.md`, and `THIRD_PARTY_NOTICES.md` are the
repository-level declarations. New first-party source files should include
`SPDX-License-Identifier: GPL-3.0-or-later`.

`scripts/prepare_license_bundle.py` collects the exact Python runtime package
versions and their supplied license files, locates the Python license, verifies
the pinned FFmpeg source checksum, and creates an archive from the actual Git
worktree. `build_dmg.sh` runs this before PyInstaller, embeds the resulting
documents in every application, copies the source archives into the DMG, and
mounts the completed DMG to verify required resources. Keep the explicit
runtime distribution list synchronized with dependencies added to the
PyInstaller specifications; a release intentionally fails if a required
license cannot be found.

This project is a native macOS/PyObjC workflow for building a GPS-aware photo
and video slide show from one adventure: a project directory, one combined GPX
track file, imported media, generated map images, geolocation metadata, and a
slide-show control list.

Deferred provider and workflow ideas are recorded in
[`FUTURE_ENHANCEMENTS.md`](FUTURE_ENHANCEMENTS.md) so they are not confused
with currently implemented behavior.

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
- `slideshow_control_format.py`: strict CSV-style `#MUSIC:`, `#CONTROL:`,
  `#CAPTION:`, and `#FONT:` parsing, disabled-line handling, and normalization.
- `video_audio_normalization.py`: generated-directory exclusion, FFmpeg
  discovery, two-pass loudness normalization, atomic manifests, freshness
  checks, and runtime normalized-video resolution.
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

Adventure discovery keeps strict format-2 loading separate from copied-folder
recovery. `discover_adventure_candidates()` returns valid records, relocatable
templates whose only structural problem is their recorded project root, and
genuinely invalid files. `create_adventure_from_template()` creates a new file
without replacing an existing destination, rebases only absolute paths below
the old project root, retains shared relative project assets, and clears resume
history. Normal `load_adventure()` validation remains strict.

The main window is built manually in `_build_window()` and positioned in
`layout_window()`. Help text is mostly in `_configure_tooltips()`.

The sections are:

- Adventure
- GPX Files
- Photos and Video Clips
- Map Generation
- Slide Show Control File
- Start Slide Show
- Show-type selector, Start/PDF/Quit controls, status line, and progress
  bar. Help is in the header beside Settings.

Each section has a non-editable status checkbox. It is used as a visual
completion indicator:

- Adventure: project directory exists.
- GPX Files: the user explicitly accepted a usable GPX source or selected
  media-only mode.
- Map Generation: overview plus all Standard and Time-Lapse stage maps exist and
  are current.
- Photos and Video Clips: supported media exists and the user explicitly
  accepted the project folder as the collection.
- Slide Show Control File: control file exists and contains at least one image
  or video.

`_workflow_readiness()` is the shared readiness source for these indicators
and the workflow assistant. Do not add a separate assistant-only file check;
otherwise the bubble and colored marks can disagree. The pure helpers in
`workflow_assistant.py` normalize persisted state, choose the first incomplete
stage, and calculate an in-window bubble placement suitable for unit tests.

New Adventures save `workflow_assistant.enabled`,
`journey_source_confirmed`, `media_confirmed`, `metadata_prepared`,
`place_names_requested`, `place_names_completed`, and `slideshow_started`.
Missing newer fields in an existing Adventure normalize as confirmed to avoid
restarting onboarding. `detected_gpx_choices(...)` supplies the two explicit
journey-source decisions, independent of how many GPX files are already in the
folder. The retained bubble renders choices as mutually exclusive radio rows plus action controls;
the controller dispatches them without opening a modal onboarding wizard.
The media stage uses three radio rows and one Continue action. Place-name
selection is not a separate assistant stage: `locations.add_place_names` is
shown in the Photos and Video Clips section and defaults to true.
The explicit **No GPX file - use only photos** checkbox lives in the main GPX section and
persists through `trackmaps.route_source = "media"`; it is intentionally not an
assistant-only choice.

Audio has an independent compact section. `audio.enabled` defaults to false
for new Adventures and controls whether both music and narration arguments are
passed to the player. The GUI always creates `<project>/audio` and
`<project>/narration`; each selected `.playlist` must be a direct child of its
corresponding directory. The small Help link beside No Audio calls the retained
`showMusicDirectiveHelp_()` controller used when a `MUS` row is edited, so the
main window and control-file editor cannot develop different command
references. Loading an older Adventure without `audio.enabled` enables music
only when that Adventure already references a music source.

Video normalization remains available when music is disabled. It runs only
after the explicit GUI action, writes into `normalized-videos`, and commits
each manifest update atomically. The manifest binds outputs to source size and
nanosecond modification time plus the LUFS/boost/true-peak settings. Playback
never invokes FFmpeg: it validates the manifest and falls back to the original.
`scripts/build_ffmpeg_lgpl.sh` builds the pinned non-GPL FFmpeg executable that
`GPSTrackShow.spec` includes with its LGPL notice.

Guided metadata preparation wraps `discover_media_update_candidates(...)`,
`analyze_control_file_updates(...)`, and `commit_control_file_update_plan(...)`
with no active control file. It therefore reuses valid sidecars and writes only
missing, invalid, legacy-unsigned, or signature-changed metadata. The guided
control/assets worker stages the list, generates the compact GPX summary when
needed, requests both map layouts, and atomically installs the list. Map errors
are accumulated in `media_map_options["map_failures"]`; a valid list remains
installed while map readiness stays incomplete.

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

`slideshow.transition` stores the initial playback style and now includes
`time_lapse`. Schema-5 Adventures containing `slideshow.start_mode` are
normalized once: Time-Lapse becomes `slideshow.transition=time_lapse`, while
Standard retains its stored standard transition.
`slideshow.window_mode` stores `auto`, `single`, or `multiple`. Automatic mode
resolves against `NSScreen.screens()` in the player: one screen uses one window
and two or more screens create the separate overview window. Legacy
`slideshow.map_window` booleans are migrated when an Adventure is loaded:
the former enabled default becomes `auto`, while disabled becomes `single`.
`slideshow.track_map_before_media` defaults to false and controls the optional
single-window Standard preview of a marked track map before every medium.

The main GUI reuses one retained Adventure Processing window across
`assistant_metadata`, `automatic_maps`, manual map generation, control
creation, and control update.
`_continue_adventure_processing(...)` ends a phase without releasing the
window or clearing its text; the next `_prepare_geolocations_window(...)`
appends a heading and updates the title. Completed sidecars and maps remain
installed if a later phase is cancelled or fails. The output `NSTextView` has
all automatic spelling and replacement services disabled. Worker output is
queued asynchronously through `GeoLocationsOutputWriter`; do not make those
line callbacks synchronous because AppKit text checking can otherwise create
a main-thread/background-operation mutex cycle during long place-name runs.
Track start/end place enrichment atomically checkpoints both map variants after
each completed track, so cancelling or terminating a long tour resumes from the
next unfinished endpoint rather than discarding the complete pass.
Project status compares both current map variants against the active track
fingerprint and `locations.reuse_radius_m`. Missing start/end entries are
reported in both metadata and map status, mark **Update Metadata Extraction**
with an asterisk, and make the Assistant metadata stage incomplete without
marking otherwise valid map pixels stale.

`GPSTrackShow.py` preparses map blocks with `parse_stage_descriptors(...)`.
`PlaybackPhase` records Intro information, clean Intro overview, Stage Map,
marked Stage Overview, Media, and Time-Lapse states. Resume files version 2
store the stage, phase, medium position, style, and Time-Lapse progress rather
than depending on retained rendered-image history.

The optional Adventure `title_image` is resolved project-relatively by the GUI
and passed as `--adventure-title-image`. An absent value deliberately falls
back to the first still image in the control file. The Intro information layout
measures its wrapped description and optional image before choosing its height,
but draws no background panel. It uses black text with a light shadow and an
AppKit drop shadow for the title image. Images are aspect-fitted to at most 35%
of both screen dimensions. Intro
information and clean-overview phases use `SWITCH`, keeping the identical
basemap visible instead of fading through the slideshow background. In
dual-window mode the map presenter receives the clean Tour Overview throughout
both Intro phases.

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
- fixed project-relative `music_source` (`audio`) and an optional
  `music_playlist` path directly inside that folder; both are stored relative
  to the project
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

Map access is centralized in `map_provider_utils.py`. Settings distinguish
`maps.interactive_provider` from `maps.output_provider`; credentials are read
from macOS Keychain using only `maps.credential_id` from the Adventure. The
provider catalog and non-secret machine preference live in
`map_provider_setup.py`; the latter stores only provider, credential identifier,
and validation status under Application Support. `cocoa_map_provider_setup.py`
implements the shared first-use flow. Hosted keys are validated with one minimal
tile request before Keychain storage. Offline storage is marked unverified and
must pass validation before automatic rendering. Esri and Custom XYZ return to
Map Service Settings; Custom XYZ requires HTTPS, XYZ placeholders, attribution,
and a successful test tile. Provider dashboard pages are opened in the browser
but never scraped or automated. The shared Contextily cache, stable identifying
User-Agent, serial request pacing,
OSM seven-day minimum retention, terminal 403 handling, and 429 reporting apply
to GPX Editor, generated maps, and PDF maps. Automatic whole-project jobs never
fetch missing public OSM tiles; that endpoint is limited to interactive use and
one explicitly selected map plan.

## Shared GPX Processing

`gpx_import.py` is the common document-level importer. It accepts GPX 1.0 and
1.1 core namespaces plus namespace-free documents that declare one of those
versions. It normalizes core elements to GPX 1.1, converts routes into tracks,
and converts waypoint-only documents into one ordered track. Extension
namespaces remain unchanged. Invalid coordinates are removed, but absent,
malformed, or backward timestamps never remove valid geometry.

`gpx_processing.py` is the single source for GPX geometry and quality metrics.
It defines `ProcessingOptions`, `RawTrackPoint`, `ProcessedPoint`,
`ProcessedSegment`, and `ProcessedTrack`. The processor keeps `<trkseg>`
boundaries, filters horizontal and vertical quality independently, smooths XY
and elevation over separate route-distance kernels, applies retained-point
spacing, interpolates missing/rejected elevations only between valid anchors,
and computes length/ascent/descent from the resulting geometry.
It also derives a distance-centered running speed from intervals bounded by
recorded timestamps. Speeds below the stationary threshold are excluded from
the whole-track moving average; no speed is extrapolated beyond recorded time
anchors.

Consumers must not recalculate point-to-point geometry independently.
`GPXEditor.py`, `gpx_tracks_table.py`, `gpxlist.py`, `track_timing_utils.py`,
PDF output, generated maps, and slideshow timing all use the shared output.
`elevation_metrics.py` remains only as a compatibility re-export. Editor caches
are keyed by the semantic track fingerprint and complete processing-parameter
signature; XML edits invalidate only the affected track. Parameter changes are
processed on one background worker using XML snapshots, with AppKit updates
returned to the main thread.

The compact summary points to one map-independent derived-data sidecar per
track. Those sidecars record processing options, raw/retained counts, rejection
counts, segment-preserving geometry, processed elevation, timed points, and
running speed. Retained point records also preserve explicit horizontal/vertical
uncertainty, HDOP, VDOP, PDOP, satellite count, and fix type when present; PDOP,
satellites, and fix are metadata only in this version. Geometry parameters are
part of map freshness signatures. Running-speed window and stationary-threshold
changes update these small sidecars without touching PNG maps.

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
statistics, endpoints, processing settings, fingerprints, plot filenames, and
`track_data_sidecar` references. Point-by-point processed geometry and
`timed_track_points` live in `<base>-trackdata/NNNN.json`; map sidecars retain
image projection/layout metadata and remain a transition fallback. This avoids
duplicating data and allows timing/GPS inference to remain current while an
expensive PNG is visibly stale.

After `GPXEditor.py` saves an accepted changed GPX file, the main GUI returns
immediately and regenerates the compact summary and all derived sidecars in one
background traversal without rendering maps. Progress is reported per track.
Closing the editor does not repeat work for the same file version.

The GUI deliberately tolerates legacy plot file names when detecting existing
plots. Matching code normalizes track plot names so old zero-padding variants
are not counted as missing duplicates.

Map Generation controls:

- The default requested basemap zoom is 16. Compact stage maps retain a
  2.25-km minimum short dimension so zoom-16 tiles are not enlarged at the
  default 1920x1080 output size.

- `Generate and Update Maps`: opens one logical-stage selection with
  missing/stale rows marked by `*` and preselected. Each selected stage renders
  Standard followed immediately by Time-Lapse from one prepared GPX context.
- `View`: opens the overview followed by Standard and Time-Lapse maps paired
  per stage.
- folder icon: open `trackimages/` in Finder.

Automatic generation uses the same `ProjectMapPlan` after media metadata
preparation and again as a final control-file creation preflight. It renders
only missing/stale assets. Manual selection can force regeneration of current
stages.

Before rendering, obsolete numbered map `.png` and `.json` files are removed
if they no longer match any current GPX track. Obsolete media-stage maps are
removed only within the active Adventure's sanitized `<base>-media-...` family.
Both valid variants are retained. Cleanup intentionally does not remove the
overview image, summary JSON, other Adventure families, or unrelated files.

Map generation runs on a background thread. The GUI shows a Cancel button that
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

Create and Update Control File enable lazy media GPS inference. The compact track
summary supplies only each stage's start/end time, name, fingerprint, map names,
and derived-sidecar path. `LazyTrackGpsResolver` performs this inexpensive
interval check before opening any detailed data. Only a media timestamp inside
exactly one valid interval can proceed. The matching derived sidecar is loaded
on demand and must have the current fingerprint and valid monotonic
`timed_track_points`; Standard and Time-Lapse map sidecars are fallback inputs
for projects not yet refreshed. The parsed timeline
is cached once per track and surrounding points are found with binary search.
The resolver never reparses the GPX file and never extrapolates beyond a stage.
At a `<trkseg>` boundary it selects the nearer endpoint instead of
interpolating across the gap.

New media sidecars use one GPS-independent timestamp-selection path. Embedded
ExifTool fields are authoritative in this order: `DateTimeOriginal`, then the
camera/container-key `CreationDate`. If those are absent, `GPSDateTime` or
`GPSDateStamp` plus `GPSTimeStamp` is interpreted as UTC and normalized to local
time. Generic track `MediaCreateDate` and `CreateDate` follow because exports
can replace them. Only then does processing fall back to Spotlight
content/filesystem creation dates and finally filesystem birth/modification
time. Debug processing uses the identical order and records the selected source.

Inferred sidecars contain `gps_source: track_time_interpolation` and a nested
`gps_inference` object with the track identity/fingerprint, bounding times,
fraction, timing-estimation flag, and source derived-data sidecar (or transitional
map-sidecar fallback). Embedded GPS always
wins. A changed fingerprint causes only inferred GPS to be revalidated; a
changed coordinate clears its old reverse-geocoded place so metadata
maintenance can resolve it again. Place-name writes must preserve this
provenance.

The place-name phase of Update Metadata Extraction is a dedicated sidecar-only
pass. It validates the
extension-aware sidecar, reverse-geocodes only coordinates already stored
there, and patches only place-related fields. It does not create a temporary
control list, read a compact track summary, open Track Map sidecars, parse GPX,
or call Spotlight/ExifTool. Missing, malformed, ownership-mismatched,
invalid-date, and GPS-less sidecars are counted and skipped. The writer starts
from the original JSON object, preserving GPS inference provenance and unknown
future fields.

The media browser is also a strict sidecar consumer. It opens directly without
a `GetGeoLocations.py` worker. `validate_media_sidecar(...)` supplies the
shared `available`, `missing`, or `invalid` state; unavailable metadata leaves
date, GPS, and place blank, with no filesystem timestamp fallback. Merge New
Media remains the intentional writer for selected missing/invalid rows and
passes the current summary separately from `merge_tracks`, allowing position
inference without turning the operation into Track Map synchronization.

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

Initial sorting and Update Control File share `assign_adjacent_day_track(...)`.
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
They use the same map provider and rendering parameters as track maps. Their
header contains the date and, when available, a stage name derived from the
first and last resolved media place. They store `map_kind: media`, all media coordinates,
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
path and is shared by initial control-file creation, Update Control File, and
automatic/manual Map Generation. Merge classification calls
`build_control_sections(...)`,
so exact-date, adjacent-day, and remaining-media passes cannot drift from
initial creation.

The GUI and update review use the shared side-effect-free
`analyze_track_map_reference_updates(...)` preflight for:

- current track maps missing from the control file
- old `#Overviewmap:` or `#Map:` entries that no longer match the current track
  summary
- map entries whose files no longer exist

The Slide Show Control File status line reports these problems.
`analyze_control_file_updates(...)` parses the control file and summary once
and combines a `TrackMapReferenceUpdatePlan` with the selective media plan.
`commit_control_file_update_plan(...)` applies required map corrections first,
then approved media changes to the same in-memory model, stages sidecars and
affected media maps, and atomically replaces the control file last. Cancelled
or failed updates leave it unchanged. The reusable Cocoa table window
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
- `#PLAY: target, $A - $D, ...`: finite music selection followed by exact
  return to the interrupted title.
- `#NARRATOR: target, $A - $D, ...`: finite narration selection on the retained
  narrator channel.
- `#CONTROL: command, ...`: ordered non-display slide-show timing, style,
  label, jump, pause, and end commands.
- `#CAPTION: command, ..., text`: a retained visual caption for the immediately
  following enabled image or video.
- `#FONT: command, ...`: persistent partial caption/header font changes or
  `#DEFAULT`.
- `# original line`: a disabled line retained for later restoration.

Media lines have exactly the normal filename/time/GPS/place fields; directives
are never attached to another row. The shared parsers use strict CSV quoting,
normalize case-insensitive `$LABEL` references, and reject malformed command
syntax. `#CONTROL` labels are indexed once; duplicate labels warn and the first
definition wins in direct-player launches. GetGeoLocations merge/sync and
Adventure rename/copy and control-file maintenance preserve disabled rows and
all four directive families without interpreting their payload as a map path.
The player precomputes caption targets and font state by model row so Start
Here, resume, jumps, backward navigation, and Time-Lapse use identical state.

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
- `PLY`: temporary music selection
- `NAR`: narrator selection
- `CTL`: slide-show control directive

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
context and derives both the GPX summary label and map freshness from
its tracks. Do not add a separate `_format_gpx_summary(...)` traversal to that
path; synchronous callers may continue to use it directly.

The editor must return the most recently saved GPX path. The main GUI then
updates the GPX field, refreshes GPX statistics, regenerates the track summary
JSON, refreshes Map Generation status, and refreshes the control-file track-map
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

`#PLAY:` and `#NARRATOR:` share one selection parser and playlist resolver.
Selections preserve order and duplicates and expand inclusive label ranges.
The narration controller retains at most two additional players, plays only
the requested finite sequence, and reports active-state changes to music and
video gain. Music may remain parallel, be reduced, or be faded and paused;
video gain is independently reduced during narration. Both playlist-generation
workflows call the same recursive discovery, label generation, and append-only
update functions.

The effective music gain is the configured music percentage multiplied by the
0–9 directive level and the retained slot's crossfade envelope. A level change
therefore updates both players during a crossfade. Video players use the
separate configured video percentage. Runtime `n` replaces an active player
item with the normalized/original counterpart, seeks to the prior timestamp,
and restores its playing state.

The transport supports ordered target queues that return to the interrupted
playlist title and time position, permanent playlist jumps, continue,
single/line/range/album/all loops, a 0–9 gain level, and `#ON/#OFF`. Targets
open the control gate; volume and gate actions do not cancel transport modes.
The player reports unresolved targets but keeps the current title running.

Player entry points accept music and narration sources/playlists, crossfade,
music/video gain, normalized-video preference, and normalization-signature
settings. CLI equivalents include `--music`, `--music-playlist`, `--narration`,
`--narration-playlist`, narration gain/reduction options,
`--audio-crossfade-seconds`, `--music-volume-percent`,
`--video-volume-percent`, `--use-normalized-videos`, and
`--no-normalized-videos`. A directory without an explicit playlist
looks for the control-list stem (without `-sorted`) plus `.playlist`, then
falls back to recursive case-insensitive relative-path order.

The player atomically publishes active control-row changes to its state file.
The main GUI watcher reads sequence-numbered updates while the subprocess is
alive. Control-editor synchronization validates control-file identity and
signature, and maps the active model index through the current filter. While
**Jump to Show** following is active, a unique manual row selection is sent to
the player through a separate atomic command file and following continues from
that row. Cell editing or multiple selection suspends following. Time-Lapse
animation frames do not write state unless their active row or display phase
changed.

The same command file carries sequence-numbered `settings` and `restart`
commands. A Settings request snapshots the active map-window, screen-swap, and
fullscreen roles in the player. The Apply acknowledgement may contain no
changed values; its `restore_display` flag still reactivates the player,
restores missing fullscreen roles, and redraws the retained overview. Pressing
Continue while that player is already alive sends `restart` so the current
control row is rebuilt instead of launching a second subprocess.

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
When recorded timing provides running speeds, a retained analog speedometer
interpolates `running_speed_kmh` beside the clock. Its scale is selected from
the current track maximum, rounded upward to a readable value of at least
7 km/h; the needle takes one second to traverse the complete scale.

GPX-backed stages optionally begin with an elevation-profile inset. The player
reads `processed_track_segments` from the selected Track Map sidecar, so it
does not parse the GPX file during playback. `elevation_profile_cache.py` owns
sample extraction, the same min/max-plus-five-percent range used by
`ElevationProfileView`, cache naming, and freshness signatures. The native
renderer writes one shared Standard/Time-Lapse PNG and manifest under
`trackimages/elevation-profiles`. Track fingerprint, GPX processing parameters,
retained-point count, and renderer version invalidate the cache. The inset uses
the shared route-free placement frontier but omits the normal white media
frame. Standard playback composites it into the roomier Time-Lapse map variant
for the initial Stage Map without replacing the centered map context retained
for the other display. Time-Lapse presents the overview inset first, then the
profile, then route motion. `PlaybackPhase.ELEVATION_PROFILE` makes this order
and reverse navigation explicit. `e` changes the retained session preference
without rewriting the Adventure.

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
order at the end of its stage. Lowercase `t` cycles forward and uppercase `T`
backward through Time-Lapse and Standard transition styles without
intentionally skipping a media row. Entering Time-Lapse cancels the standard
continuation timer and presenter transitions, then hides every standard
content layer so both modes cannot animate concurrently.

Every fresh Time-Lapse stage first displays the overview in the stage view as a
framed medium for `--duration`, then the optional elevation profile for the
same duration, and finally starts route motion. This sequence is retained when
a separate overview window is active; that window continues to show its
centered overview simultaneously.
`timelapse.overview_on_stage_map_dual` defaults to true and controls only this
additional dual-display inset; single-window playback always retains the
overview phase.
`draw_time_lapse_overview_media(...)` projects the active stage's repaired
track points through the overview sidecar coordinates before the rendered
overview is scaled into its media frame, preserving the route highlight.
The separately displayed Time-Lapse overview is likewise rendered without an
embedded caption because both retained map views draw the same runtime header;
this prevents an older translucent caption from appearing underneath it.
`timelapse.overview_as_media=false` or `--time-lapse-overview-fullscreen`
retains the former full-window presentation. A resumed stage starts at its
saved progress without replaying that overview. Standard single-window
playback uses Stage Map, marked Tour Overview, and media. The overview PNG is a
full-tile basemap; `_handle_overview(...)` suppresses its generic header, while
`draw_overview_overlay(...)` adds the active stage header with a black runtime
background. Time-Lapse overview media uses the same renderer without that
background. The
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
placement frontiers, extent optimization, cache validation, and normalized/view
coordinate conversion. Plot sidecars store `media_clear_boxes` version 3 in
`image_fraction_bottom_left` coordinates. The cache covers the four corners
plus center-left, center, center-right, top-center, and bottom-center. Each
position contains a largest-area `maximum` and a width/height `frontier`,
together with image size, margin, grid size, and track fingerprint. The player
validates those fields and converts the normalized rectangles into the current
aspect-fit image rectangle. Legacy, malformed, stale, or wrong-size caches fall
back to the same shared nine-position runtime calculation. Every medium is
aspect-fitted against the frontier member that produces the largest display
area.

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
changes, not on each 20 ms tick. `draw_runtime_header(...)` is shared with the
full-window media presenter, keeping its compact three-line title area, clock
geometry, and three track-statistics rows identical across every playback
style.
Generated Time-Lapse basemaps continue beneath the runtime header. Off and
Semi-transparent retain the full presentation frame and anchor overlays to the
fitted image edge. Header area creates a real band in the configured slideshow
background color and fits media beneath it; Time-Lapse maps use independent X/Y
display scales so all map pixels remain visible across the complete screen
width without regenerating map assets. GPS overlays use the same display scales.
`create_clock_overlay_image(...)` applies the configured shadow color at 50%
opacity, offset down/right by one stroke width, before drawing the clock and
date in the configured header font color. The clock time comes from the interpolated current
track-point time. Once progress reaches the track endpoint,
`time_lapse_clock_datetime(...)` advances it to the current medium's later
capture timestamp while leaving the marker at the endpoint. The view draws the
current medium's reverse-geocoded place in
the enlarged stage header with the same font size as the optional date subtitle
and draws total distance, stage distance, and elevation as three right-aligned
header lines. When a usable clock is active, the dynamic GPX heading omits its
duplicate date. These text layers are drawn directly by the retained view and
do not allocate per-frame overlay images.

### Slide-Show Resume Protocol

The GUI launches the separate player with a project-local `--state-file`.
During orderly quit, the player atomically writes the active zero-based control-
file row, its exact line text, mode, stage/phase, Time-Lapse progress, optional
visible-media row, a control-file identity, a human-readable display snapshot,
and a timezone-aware stop timestamp. It captures music before releasing
AVPlayer objects: playlist/path identity, elapsed seconds, path-based transport
sequence and position, continuation and interrupted-queue positions, gate,
manual audio state, and volume. Natural completion writes `completed: true`.
The GUI watcher reads and removes the transient file on the main thread and
prepends non-completed version-4 checkpoints to `slideshow_resume_history` in
the `.adv`, retaining at most twenty.

Before a later launch, the GUI validates the control-file path, size, nanosecond
modification time, row range, exact row text, and required media/map assets.
Stale entries remain visible but disabled in the Continue table. **Start**
deliberately omits resume arguments and leaves history unchanged. **Continue**
passes `--resume-index`, `--resume-progress`, `--resume-media-index`, phase, and
the hidden JSON `--resume-audio-state` and `--resume-control-state` after selection. The same optional values
are available to `config_from_options(...)` and `run_with_options(...)`.
Standard playback reopens the exact media/map row; Time-Lapse reconstructs the
containing stage, marker progress, and visible medium. Music state remaps saved
paths after playlist reordering and falls back to replaying preceding
`#MUSIC:` directives when exact restoration is impossible.
The control snapshot restores the effective media duration and playback style.

Only checkpoint format version 4 is accepted. Older
`slideshow_resume_position` data and unsupported history entries are ignored
and are omitted by the next Adventure save. Copying an Adventure clears its
history; renaming preserves it. Natural completion neither adds a checkpoint
nor clears earlier ones.

The control-table **Start Slide Show Here** action constructs a transient
resume payload with the selected model-row index and exact line text. It passes
that payload through the same `--resume-index` path without replacing the
Adventure's saved Continue position before launch. Time-Lapse rewinds media
rows to their owning map, but starts non-display directives such as `#Datum:`,
`#MUSIC:`, and `#CONTROL:` at their exact row so they execute normally.

`track_timing_utils.py` is the single timestamp-repair policy shared by the
GPX editor, `gpx_tracks_table.py`, and the player. It preserves usable GPX
times, interpolates missing intervals by cumulative distance, and falls back to
3.5 km/h when a duration cannot be derived. Completely untimed tracks retain
`time=None` and receive monotonic `elapsed_seconds`; the routine must never use
the current time as a fabricated absolute anchor. It returns repaired in-memory
values only. GPX files are changed only by an explicit GPX Editor save.

Derived track-data sidecars include `timed_track_points`, an ordered list of latitude,
longitude, elapsed seconds, optional ISO timestamp, absolute-time flag,
estimated-time flag, elevation in metres, segment identity, and cumulative
stage distance. Summaries carry `timing_status` and `has_absolute_time`.
Calendar-based media assignment, track-time GPS inference, and adjacent-day
classification must skip untimed tracks. Control-file creation still emits
their map stages and orders each beside the preceding dated track from its
original GPX sequence.

The Adventure settings window now persists the global slide-show parameters in
`.adv` files: media and stage durations, time-lapse media minimum size,
transition, background/marker/arrow styling, font and clock/place overlays,
fullscreen/display swap/map-window/joined-window/end-behavior options, and collage
size and maximum. Playback key changes remain session-only by design.

`timelapse.marker_style` selects `pilgrim` (default), `bike`, `car`, `plane`,
or `arrow`. The player
loads `pilgrim-frame00-rigged-512.png` through
`pilgrim-frame08-rigged-512.png` once and draws retained images in
`TimeLapseMapView`. Frame 0 is the standing pose; movement after a stationary
interval resumes at frame 3. Motion tolerance scales with the map view
diagonal, and the 0.1-second sprite cadence is independent of the 50 Hz GPS
position updates. The view calculates and caches one orientation per stage
from the same fixed normal used by the arrow. It mirrors the right-facing
source frames when their transformed facing direction opposes the route. The
bicycle, car, and airplane are drawn as vector markers and rotate with the
route. They remain visible on the overview; the animated pilgrim is represented
there by an arrow. Missing pilgrim animation assets also fall back to the arrow.

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

`EditorTableView.menuForEvent_()` applies standard macOS context-click
selection and asks the controller for a freshly validated Track menu. Shared
helpers handle disjoint-group movement, block duplication, and copy naming.
Context and header sorting both call `sort_by_column()`; selected-row sorting
replaces only the selected slots and includes processed moving-average speed.

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

### Dynamic Map Overlays

`map_overlay.py` is the renderer-independent boundary between generated
basemaps and visible route information. `MapOverlayScene` normalizes GPX
segments, media points, overlay mode, and header lines. It deliberately has no
AppKit, Matplotlib, GPX-parser, or tile-provider dependency.

Map sidecars with `background_only: true` describe
PNGs containing only basemap pixels and the existing reserved header area.
Their `overlay_geometry`, `header_lines`, and stage metadata are rendered by:

- `GPSTrackShow.draw_dynamic_map_overlay(...)` for Standard and Time-Lapse
  playback;
- the main GUI plot viewer for previews;
- the PDF Matplotlib path, which uses the same scene semantics while retaining
  vector lines and text at PDF resolution.

Legacy sidecars are never overdrawn. `map_uses_dynamic_overlays(...)` is the
single compatibility check. Geometry, extent, provider, zoom, layout, and
background remain map-affecting; colors, widths, point styles, overview labels,
track-title choice, and header presentation are runtime overlay parameters.
Map content version 5 reserves a 25% taller stage header; older background-only
maps remain display-compatible until regenerated.

`trackmaps.track_title` selects `endpoint_places` (default) or `track_name`.
`track_header_lines(...)` formats the chosen title and compact
`DATE · NN.N km - HH:MM h` subtitle, omitting the date when the Time-Lapse
clock is active. The sidecar-only place-name pass loads the compact track
summary, resolves each current track's start and end before media, and writes
`track_endpoint_places.start/end` into both fingerprint-matching map sidecars.
Map regeneration preserves this object only when the track fingerprint still
matches, preventing stale endpoint names from moving to edited tracks.

`GetGeoLocations.py` reuses `#MediaMap:` as the first-class media-stage format.
When no `TracksSummary` is supplied, all dated groups are media stages and
`create_media_overview_for_records(...)` produces a shared overview from their
available GPS coordinates. Media map sidecars store ordered `media_points`
with optional source names and times. No synthetic GPX file is created and GPX
processing is not invoked. The GUI's Media-only Create/Update/View path reads
the active control file once and rerenders only its overview and media-map
specifications.

`render_media_map_specs(...)` accepts either one `map_layout` or a
`map_layouts=("standard", "time-lapse")` sequence. Guided first creation uses
the sequence and per-layout render signatures. In Time-Lapse playback,
`placement_obstacle_points(...)` falls back from animated route points to all
sidecar `media_points`/`overlay_geometry` points before calculating route-free
placement rectangles. Adventure parameter schema 2 removes the redundant
media-dot size; the GUI passes twice `slideshow.marker_radius` as the diameter.
The old default orange migrates to blue while custom colors are preserved.

`validate_media_sidecar(...)` is the common read boundary for metadata
consumers. Fresh metadata extraction is restricted to Create, Update Control
File, explicit sidecar migration, and explicit `--ignorejson` maintenance runs.

Selective media analysis uses `analyze_media_updates(...)`; the public GUI
workflow wraps it with `analyze_control_file_updates(...)` and commits through
`commit_control_file_update_plan(...)`.
`discover_media_update_candidates(...)` first finds imported, missing, invalid,
legacy-unsigned, and signature-changed media without extracting metadata.
Analysis is side-effect free and extracts only automatic candidates
or files deliberately chosen for rechecking. It reads the compact track summary once and
uses `LazyTrackGpsResolver` without parsing GPX. The commit reuses the normal
classification functions, ignores unchecked `MediaUpdateItem` objects, stages
affected media maps, backs up replaced files, and replaces the control file last.
Legacy verification recommends targeted reverse geocoding only for new or
changed GPS, or for an existing place whose lookup coordinate is stale; an
unchanged GPS without a place remains for the place-name phase of Update
Metadata Extraction.

Newly generated sidecars may contain `source_file_signature` (size and
nanosecond mtime), `datetime_source`, and `metadata_updated_at`. Reverse
geocoding writes `place_coordinate`; a place is stale when this coordinate is
farther from the current GPS than `locations.reuse_radius_m` (150 m by
default). The same threshold is passed to selective analysis, lazy track-time
GPS inference, and the sidecar-only place-name pass. Accepted coordinate
changes retain the place and rewrite `place_coordinate`. Legacy sidecars
without signatures are extracted once to establish freshness; signed current
sidecars remain source-of-truth inputs.

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
its configured upstream. After that push succeeds, it calls
`scripts/publish_website_release.sh`, which uploads the verified DMG, validates
its size and SHA-256 on the server, registers its metadata, and atomically
changes the protected website download to the new artifact. Use `--yes` for a
non-interactive invocation. The DMG also remains at
`dist/myCamino-GPS-Track-Show.dmg`; `dist/` is ignored and therefore the binary
is not pushed to GitHub. A failed test or DMG build occurs before Git staging
and prevents the commit.

Relevant specs:

- `GPSTrackShow.spec`: bundled slide-show player executable.
- `myCamino GPX Editor.spec`: standalone GPX editor app.
- `myCamino GPS Track Show.spec`: main GUI app with bundled player resources.
