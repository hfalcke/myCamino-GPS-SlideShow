# myCamino GPS Track Show User Guide

## Photo-Derived GPX Tracks

Use **Create Tracks from Photos** to open GPX Editor's **Add Media Points**
workflow. For a new media-only journey, the editor supplies an empty destination
track. With an existing GPX, select the destination track before adding media.
Only selected photos and videos are processed, current sidecars are reused, and
each accepted medium remains a raw linked waypoint. Save the edited GPX before
making it the Adventure's journey source.

## License and source code

myCamino is free and open-source software licensed under GPL-3.0-or-later.
Choose **Help**, then **License**, **Third-Party Notices**, or **Source Code** to
read the documents installed with the application. Release DMGs also contain
the exact source archive used for that build. Donations may be invited in the
future, but are voluntary and do not change available features or GPL rights.

`myCamino GPS Track Show` helps you assemble one adventure from GPX tracks,
photos, videos, map images, geolocation metadata, and a final slide-show
control file.

An adventure is one project directory. The project directory contains all files
created or imported by the workflow.

## Adventure Map

The normal starting screen is the Adventure Map. Drop an `.adv` file or an
Adventure directory to load it. Existing tracks appear as a lightweight
overview and media are grouped into numbered location markers. Click a track
to select it and double-click it to open the Track Table. From there,
double-click its row to open the Waypoint Table; the map context menu can open
either table directly. Nearby tracks remain visible but subdued. Double-click empty map space or press
`Cmd-0` to return to the complete overview. The map preserves geographic
proportions while resizing; black margins are used instead of stretching map
tiles when the window and map have different shapes.

Click a numbered media group to select all media in it. Command-click adds or
removes another group. Command-drag from empty map space to draw a black selection
rectangle and add the enclosed groups or thumbnails; a simple empty-map click
clears the selection. Selected media have a red outline. Right-click a media
group to fit all selected media to the map or create a new track from their
GPS positions. Ordinary dragging and two-finger scrolling pan the map.

You may also drop GPX tracks, photos, videos, audio, a control file, or a media
folder. Fast media metadata preparation runs in the background and never
regenerates map images. The small activity indicator shows the current phase;
click it for the complete processing journal. Work that has not yet
been saved is regularly placed in temporary crash-recovery snapshots.

myCamino also checks the active Adventure at startup and while it remains open.
After a newly copied photo or video has stopped changing, metadata is prepared
once. An existing edited control file is never changed silently: new media is
shown in **Review Control File Updates** first. If there is no control file,
myCamino creates an initial one from the available media and opens the editor.
Previously excluded media is remembered and is not proposed repeatedly.

Choose **myCamino > Settings** or press `Cmd-,` at any time, including before
creating an Adventure. Pre-Adventure changes remain in memory and are written
as the initial Adventure settings when it is created. The first genuinely new
medium that needs metadata also asks once whether historical weather should be
included. Free non-commercial retrieval needs no Open-Meteo account or API key.
Customer access requires an Open-Meteo account, a suitable paid plan, and an API
key, which myCamino stores in macOS Keychain. The answer is remembered and can
be changed later in Settings.

The native **Track**, **Media**, **Maps**, **Control File**, **Audio**, and
**Slide Show** menus expose the normal workflow directly, without showing the
Advanced Interface. **Adventure > Adventure Properties** edits the protected
name, project directory, description, and Intro title image. Contextual Settings
entries open the one shared Settings window at the appropriate section; the
canonical command remains **myCamino > Settings** (`Cmd-,`). The **Window** menu
lists visible, hidden, minimized, processing, and unsaved windows and restores a
selected one. **Adventure > Advanced Interface** remains available for detailed
status and uncommon operations.

myCamino checks its project website at most once per day for published news and
update notices. A newly received item is announced once; unread items remain
visible as a count under **myCamino > News and Updates** and on the application
Dock icon. Opening the News window marks the downloaded items as read. The
request sends the application version but no installation identifier, Adventure
name, media, location, or usage information. Use **Automatically Check for
News** in the myCamino menu to disable or re-enable background checks; manual
checking remains available.

The map itself has no permanent button strip. Its transparent startup message
also reminds you that `h` opens help. Keyboard shortcuts are `Cmd-G`, `Cmd-E`,
`Cmd-L`, `Cmd-S`, Space, and `h`; `Cmd-Q` quits. Two-finger scrolling moves the
map in the same direction as your fingers, while dragging continues to grab
and move the map directly. Experts who prefer the former control-first startup
can pass `--full-gui`.

After installation, start the application by clicking the
**myCamino GPS Track Show** icon in Applications. Because the beta is unsigned,
macOS may say that Apple cannot check it for malicious software. Dismiss that
warning without moving the app to the Bin. Within about one hour, open **Apple
menu → System Settings → Privacy & Security**, scroll to **Security**, click
**Open Anyway**, authenticate, and confirm **Open**. After this one-time
approval, a normal click opens it. Only approve a DMG obtained from the official
myCamino website whose SHA-256 checksum matches the value published there. See
[Apple's Gatekeeper instructions](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac).

Experts working directly from the source code can instead use the command-line
interface (CLI):

```bash
./.venv/bin/python GPSTrackShowGUI.py
```

Experts can also preload a project:

```bash
./.venv/bin/python GPSTrackShowGUI.py --project-directory /path/to/project
./.venv/bin/python GPSTrackShowGUI.py /path/to/project
./.venv/bin/python GPSTrackShowGUI.py /path/to/project/adventure.adv
./.venv/bin/python GPSTrackShowGUI.py --full-gui /path/to/project
```

When a project directory is supplied, the GUI loads its most recently modified
valid Adventure file. If the directory has no Adventure yet, it opens the
directory as a new project.

## Basic Workflow

The normal order is:

1. Choose or create the adventure directory.
2. Enter the adventure title and optional description.
3. Confirm a detected GPX file, choose other GPX files, or select
   **no GPX file - use only photos**. One detected file is used directly;
   several detected files can be joined in the GPX Editor.
4. Confirm media already in the folder or import photos and videos.
5. The retained **Adventure Processing** window prepares metadata, optionally
   adds place names, generates both map variants, and then creates or reviews
   the control file. It keeps the output from every phase; **Skip** appears only
   during the slower place-name phase.
6. Existing edited control files are never replaced automatically. After maps
   are ready, choose whether to review an Update Control File operation.
7. Review and edit the control file.
8. Export a PDF track summary if desired.
9. Start the slide show.

Audio is optional. New Adventures start with **No Audio** selected, which
disables both music and narration and hides both playlist rows. Every project
has fixed `audio` and `narration` folders, recreated automatically if removed.
Clear **No Audio**, open either folder, and add supported recordings or folder
groups. Both selected playlists are saved with the Adventure.

In the **GPX Files** section, select **No GPX file - use only photos** when the Adventure has
no recorded GPX track. The GPX filename controls are then hidden and the
program builds journey stages from GPS positions stored with the photos and
videos. This choice is saved with the Adventure. If several GPX files are
selected instead, the GPX Editor joins them into one Adventure file and allows
individual tracks to be rearranged, split, joined, inspected, plotted, and
edited.

When exactly one GPX file is already present in a new project folder, the
Assistant offers that filename as the default. Continue accepts it immediately
without opening another chooser. Cancelling Choose or the GPX Editor leaves the
journey source unconfirmed.

Green check marks beside sections show that the minimum required step for that
section is complete. Red marks show what is still missing.

New Adventures also start with the **Assistant** enabled. Its yellow speech
bubble now contains recommended actions as well as an explanation. It guides
project directory and name, a GPX choice or the GPX section's explicit
**No GPX file - use only photos** option, explicit use or
import of media, smart metadata preparation, combined control/map creation,
and the first slide-show start. When existing media is found, three radio-style
rows offer Use Existing Media, Import More, or Not Yet, followed by one
Continue button. Return activates the recommended choice. Click
its `x`, or clear **Assistant** in the header, to disable it for the active
Adventure. Choices and completed steps are saved automatically.

## Adventure Section

Use this section to choose the project directory and describe the adventure.

Controls:

- Project directory field: type or choose the adventure folder.
- Folder icon: opens a directory chooser. A missing selected directory is
  created when selected.
- Adventure name: a menu of the Adventures in the selected folder. A new name
  is editable until it is first committed. Existing names are protected; click
  **Edit** before deliberately renaming or copying an Adventure.
  Selecting one loads it immediately. The most recently modified Adventure is
  loaded automatically when a folder is chosen. Adventures copied from another
  project directory are marked **Needs adaptation** until a valid copy is
  created for the new folder.
- Description: optional two-line description.

Files created:

- `<projectname>.adv`: adventure settings file. This is JSON content with a
  `.adv` extension.

The `.adv` file explicitly stores its project directory, GPX filename, active
`.lst` file, generated map family, settings, description, import directory, and
last stopped slide-show position. After creation or loading, every relevant
change is saved automatically.

If you duplicate a complete project folder in Finder, its `.adv` files still
record the previous absolute project directory. The GUI recognizes these as
copied Adventure templates instead of reporting them as damaged. If the new
folder contains no valid Adventure, select one of the templates and use
**Create from Selected**. The proposed new name is the folder name. This creates
a new `.adv` for the copied folder, clears old Continue positions, and shares
the GPX, control file, generated maps, media, music, and narration files already
present there. The selected template remains unchanged. Use **Create Blank
Adventure** only when none of the copied settings and project-file references
should be retained.

In an empty folder, confirm the suggested Adventure name to create the first
`.adv`. Committing a changed name for an existing Adventure offers **Rename**,
**Copy**, or **Cancel**. The related-files checkbox is enabled by default and
renames/copies GPX, control list, overview, summaries, and all Standard and
Time-Lapse maps consistently. Photos and videos remain shared. Turning
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
- When Settings was opened with `s` from a running dual-screen show, Apply
  returns focus to the player and restores the same windows and fullscreen
  arrangement that were active before Settings opened.
- Invalid entries are explained at the bottom and disable Apply.

Settings cover Standard and Time-Lapse playback, Track Map appearance and
ordering, GPX processing, PDF output resolution, place-name lookup, and map
providers. Interactive GPX Editor maps and generated PNG/PDF maps have separate
provider choices. Public OpenStreetMap remains the interactive default and may
also generate one manually selected map plan, but it is never used for an
automatic whole-project download. Geoapify is the recommended production
provider; Thunderforest, Stadia, Esri, and Custom XYZ are also available. Before
the first automatic Map Generation, myCamino offers these services in a guided
setup. For Geoapify, Thunderforest, and Stadia it opens the provider's official
account page, validates the pasted API key with one small map request, and stores
the key in macOS Keychain. Esri and Custom XYZ transfer to Map Service Settings
for manual configuration. **Manage Map Provider...** reopens this setup later.
The chosen provider becomes this Mac's default for new Adventures, while each
Adventure can override it. API keys are never stored in an Adventure and must be
entered again on another Mac when they are not available in that Mac's Keychain.
A custom provider requires an HTTPS tile address containing
`{z}`, `{x}`, and `{y}`, plus attribution. Numerical settings can be typed or
adjusted with the adjacent up/down stepper. All providers share a persistent
tile cache; public OSM tiles are retained for at least seven days.

GPX Processing separates horizontal smoothing (default 10 m), retained-point
spacing (10 m), and elevation smoothing (50 m). Horizontal/vertical uncertainty
limits default to 10/20 m, and HDOP/VDOP limits to 20/20. Missing quality data
is accepted. Setting any individual smoothing, spacing, or quality limit to
zero disables it. The same processed geometry drives statistics, generated maps,
PDFs, timing, and Time-Lapse motion; the original GPX points are not rewritten.

The Time-Lapse **Moving marker** setting offers the animated walking pilgrim,
bicycle, car, airplane, and traditional arrow. The pilgrim is the default. It uses a
standing pose while the GPS marker remains within the screen-scaled motion
tolerance, then resumes its walking cycle from frame 3. At the start of each
stage it adopts the fixed arrow angle and is mirrored when necessary so that it
faces from the stage start toward its end. The overview keeps an arrow when the
pilgrim is selected; transport symbols appear on both stage and overview maps.

**Show elevation profile** is enabled by default. With one window, every
GPX-backed stage retains the existing introductory profile phase. With a
separate map window, the profile instead remains visible throughout the stage
on the map that does not display photos or videos. It is placed in the largest
route-free area. A solid red dot follows the Time-Lapse pilgrim and a red ring
marks the currently displayed photo or video. Its vertical axis follows the
track minimum and maximum with five percent headroom rather than starting at
zero. The first use creates a PNG under `trackimages/elevation-profiles`; later
shows reuse it until the track or its GPX processing settings change. Press
`e` during a show to toggle all elevation-profile displays for the session.

**GPS route tracking** is also enabled by default. During Standard
playback with a separate map window, photos and videos retain the selected
Blend, Fade, Switch, or other transition while the second display uses the
roomier Time-Lapse map and moves the route marker according to recorded track
times. Photos have the configured minimum duration; videos hold the route until
they finish. Press `g` to toggle GPS route tracking for the running show. It has no
effect when only one slideshow window is available.

The Locations radius groups nearby GPS positions under an already determined
place name. A larger radius reduces the number of lookups and can speed up
place-name extraction, while a smaller radius gives more locally specific names.

Changing a map-rendering or GPX-filtering setting marks affected maps or the
track summary as needing generation/update. Slide-show and place-name settings do not
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

- In a new project containing GPX files, **Choose** first offers all detected
  files as the default selection. One file is used directly; several files are
  opened together in the GPX Editor.
- If one existing GPX file in the project directory is selected, the GUI uses
  it directly.
- If a GPX file outside the project directory is selected, the GUI asks whether
  to copy it into the project directory.
- If multiple GPX files are selected, the GUI opens the GPX Editor so you can
  produce one combined GPX file.
- If the selected GPX file does not exist, the GPX Editor opens with the default
  output file.
- If the GPX file directory does not exist, the GUI shows an error.
- When the GPX Editor saves, the GUI immediately returns control and updates
  compact summary plus per-track derived geometry/timing data once in the
  background. It never downloads maps as part of Save.

The summary line shows the number of tracks, track/date range information, how
many track plot images exist, and whether an overview plot exists.

## Maps Section

Map Generation normally runs automatically after media have been accepted or
imported and their metadata has been prepared. It maintains one shared overview
plus Standard and Time-Lapse variants for every required GPX or media stage.
Only missing or outdated maps are rendered.

Automatic project generation requires a configured production provider. With
Public OpenStreetMap selected, missing maps remain pending and the GUI directs
you to choose a production provider or manually generate one selected overview
or stage (both Standard and Time-Lapse variants are produced together). Provider
setup may be deferred; this retains policy-limited OSM access but does
not enable automatic whole-project downloads. Every installation uses the
user's own hosted-provider account and quota; myCamino does not distribute a
shared API key. Before a manual OSM request, a confirmation reports estimated required, cached, and
downloadable tiles. HTTP
403 blocks stop the provider job without retry; HTTP 429 responses report the
provider's delay. Existing map images remain usable while changed tracks are
clearly marked outdated.

Newly created maps separate the downloaded basemap from the route and header.
The PNG stores the basemap pixels; Standard maps include their normal title
margin while Time-Lapse basemaps fill the frame. The slideshow, map viewer, and
PDF export draw routes, location dots, endpoints, and titles from the matching
map information. Therefore changing route colors, line widths, point styles,
or header visibility in Settings no longer requires downloading the basemap
again.

GPX-stage maps reserve a 25% taller black header. By default their title uses
the reverse-geocoded start and destination of the track (`PLACE1 - PLACE2`);
**Settings > Slide Show > Header > Track title** can instead select the GPX track
name. If either endpoint name is unavailable, the GPX track name is used as a
safe fallback. The subtitle shows
`DATE · NN.N km - HH:MM h`. During Time-Lapse, an active clock removes the
repeated date and leaves `NN.N km - HH:MM h`, with the current place on the
third line in the same subtitle size.

Under **Settings > Map Generation**, **Journey source** controls how stages are
formed:

- **Automatic** uses the selected GPX when one is available and otherwise uses
  media locations.
- **GPX tracks** requires a GPX file and retains the traditional workflow.
- **Media locations** groups media by local calendar date even if a GPX file is
  present.

For media stages, **Media route display** offers **Photo dots**, **Interpolated
line**, and **Hidden**. Photo dots are the default because they represent
measured locations. An interpolated line only connects successive photo
locations and must not be interpreted as a measured walking route.

### Adventures Without a GPX Track

1. In the Assistant choose **no GPX file - use only photos**.
2. Accept media already in the project folder or import more.
3. Metadata preparation starts automatically. Keep **Add place names** selected
   unless you only want metadata; current sidecars are reused and only missing,
   invalid, or changed metadata is extracted.
4. Map Generation starts automatically. Media are grouped by date and the
   program creates one overview plus Standard and Time-Lapse variants for every
   located stage.
5. Choose **Continue** to create the control file.
6. Start the slide show or choose Later. The initial style defaults to
   Time-Lapse and can be changed in Settings or during playback.

Media without GPS remain in their date section but do not create a dot. A
media-derived Time-Lapse stage is static: it shows media positions and the
current media marker but does not pretend that a measured GPX route exists.
Media position dots are vivid blue with a white edge and use the same visible
radius as the moving slide-show marker. If cached free-corner information is
missing, all media coordinates in the map sidecar are used as placement
obstacles so framed media avoids the known locations.

Controls:

- Generate and Update Maps: open a selection window with missing or outdated
  stages preselected. Selecting a current stage deliberately regenerates it.
  Standard and Time-Lapse variants are always generated together and directly
  after one another.
- View Maps: view the overview followed by Standard and Time-Lapse maps paired per
  stage. This reads the existing control-file and compact-summary references;
  it does not reprocess the GPX file.
- Folder icon: open the `trackimages` folder in Finder.
- Track ordering is configured under **Settings > Map Generation**. Choose
  `date` or `track number` for generated map order and control-file insertion.
- Cancel: shown only while map generation is running.

Files created:

- `trackimages/`: subdirectory in the project directory.
- `<projectname>.png`: overview map image.
- `<projectname>.json`: overview map metadata sidecar.
- `<projectname>-summary.json`: track summary used later by the control-file
  section.
- `0001_...png`, `0002_...png`, etc.: centered Standard per-track maps.
- `0001_...-timelapse.png`, etc.: Time-Lapse maps shifted to leave a large
  practical route-free area for photos and videos.
- Matching `.json` files: timing, map coordinates, track identity, and cached
  route-free placement rectangles. The player compares the four corners with
  center-left, center, center-right, top-center, and bottom-center, then uses
  the position that displays each photo or video largest without covering the
  route. It recalculates the rectangles only when cached data is missing or
  invalid.

The summary and map sidecar files store separate versioned identities for route
geometry and for timing/elevation data. Map images depend only on processed
route geometry. Renaming, renumbering, copying a project, or harmless GPX XML
normalization therefore does not redraw an unchanged map. The map-selection
window opens immediately, then myCamino verifies old sidecars against current
geometry in the background and repairs unique matches without downloading
tiles. The window remains movable and can cancel this analysis. The overview is still
updated when the complete route set or its extent changed.

Media sidecars use the file's full SHA-256 content identity in addition to the
quick size and modification-time signature. A Finder copy with an altered file
date is hashed once; matching content reuses the existing date, GPS, place name,
and provenance. Existing current sidecars are indexed incrementally in the
**Indexing media identity** phase. This phase reads files but does not extract
EXIF data or repeat place-name lookups.

When maps are created or updated, obsolete numbered track-map files in
`trackimages/` are removed if they no longer match any current GPX track. The
same check removes obsolete media-stage maps from the active Adventure's map
family. The overview, summary, other Adventures, and unrelated files are not
removed.

Generate and Update Maps window:

- Opens when you press **Generate and Update Maps**.
- Initially shows `Analyzing track N of M`; selection and Generate become
  available when this background check finishes.
- Shows overview as number `0` and all tracks as numbered rows.
- Rows marked with `*` need update.
- Missing or outdated maps are preselected and listed in the Range field.
- You can regenerate all maps, select rows manually, or type a range such as
  `1,2,3-6,8`.
- Manual row selection automatically disables the all-images checkbox.
- **Mark Selected Up to Date** accepts existing selected maps without redrawing
  them. It is useful after changing providers when older maps should retain
  their original appearance. Only maps whose geometry, media positions, and
  required sidecar structure still match are accepted; missing or genuinely
  changed maps remain marked for generation. Original provider attribution is
  preserved.
- For more than one map with Public OpenStreetMap selected, choose a configured
  production provider or **Use Cached OSM Tiles Only**. Cache-only mode sends no
  network requests and stops safely if a required tile is missing. GPX Editor
  map views remain available as interactive, explicitly requested plots.

Plot viewer window:

- Opens while plots are created and updates after each new image.
- Also opens from Track images View.
- Arrow keys move through images.
- `h` toggles help.
- `q` hides/closes the viewer.
- The window keeps the image aspect ratio while resizing.

## Media Section

Use this section to import and inspect the media files for the adventure.

Controls:

- Import: choose images and videos to import into the project directory.
- View: open the project media browser.
- Folder icon: open project media in Finder.
- Update Metadata: refresh only missing, invalid, or changed
  companion metadata. The button is separated at the right of the section
  because it is a maintenance action rather than an import/view action.
- If current Track Map sidecars are missing start or destination place names,
  the Photos and Map Generation status lines report the number missing and
  **Update Metadata** receives an `*`. Run it again to continue the
  place-name pass. Completed endpoints are retained and each completed track is
  saved immediately.
- Add place names: selected by default; after metadata preparation, obtain
  readable names for stored GPS positions. For GPX Adventures this resolves
  each track's start and end first and stores those names with both map
  variants, then processes the media. These endpoint names provide the default
  stage titles.

Existing valid place names are retained. If GPS changes sufficiently, its old
place name is invalidated automatically and can be looked up again. For a
deliberate complete metadata rebuild, remove that media file's companion
metadata file in Finder and run **Update Metadata**.

Files created:

- Imported image/video files are copied directly into the project directory.
- A file already present under the destination name is reported as
  **Skipping existing** and is not copied or counted as newly imported.
- Existing files are skipped so duplicates are not imported.
- Companion metadata files are prepared when media is added and can be refreshed
  with **Update Metadata**, **Create**, or **Update Control File**.
  Opening the media browser never scans the media or creates metadata.
- When companion metadata is created, the embedded exposure timestamp is
  preferred: the original photo time first, then the camera-supplied creation
  time, followed by a GPS timestamp when available. Generic video-track dates,
  Spotlight, and filesystem dates are used only as fallbacks.

Media browser window:

- Shows media files in the project directory.
- Columns include included status, type, metadata status, name, time, GPS, and
  place.
- Metadata status is **Available**, **Missing**, or **Invalid**. Every media file
  remains visible; date, GPS, and place stay blank when its metadata is not
  usable.
- Missing dates are sorted after valid dates. The browser never substitutes a
  file creation or modification time.
- Sort rows by clicking a column header.
- Double-click a row or press View to open the media viewer.
- The normal **Update Control File** workflow analyzes clear changes automatically.
  Use **Choose Other Media...** only when you deliberately want to re-read an
  otherwise unchanged file.

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

## Control File Section

This section creates and edits the file that tells the slide show what to show
and in what order.

Controls:

- Control file: editable menu of `.lst` files in the project folder. Select an
  existing list, or enter the name that **Create** should produce.
- Create: run geolocation/metadata processing and create the sorted control
  file.
- Edit: open the editable control-file table.
- Update Control File: check Track Map references and media together. It adds,
  replaces, or removes map references that no longer agree with the current
  generated maps, inventories newly discovered media, finds missing, invalid,
  legacy, or changed metadata, and shows one review before writing. **Add**,
  **Update Metadata**, and **Move** are independent choices. It does not render
  maps; use **Generate and Update Maps** for that.

During **Create** and **Update Control File**, a photo or video without embedded
GPS can receive a position from its exposure time. The program first compares
that time only with the start and end of each stage. If it falls inside exactly
one stage, the two surrounding timed track points are loaded from the current
map information and the position is interpolated. Media outside a stage,
inside overlapping stages, or associated with missing/outdated maps is left
without GPS rather than guessed. **Generate and Update Maps** makes its timing
information available again.

The inferred position is saved in the media's companion metadata together
with the source stage. A real camera GPS position is never replaced. If the
source track later changes, Create or Update Control File refreshes only positions
that were previously inferred. Place-name extraction from **Update Metadata
Extraction** does not scan tracks; it uses the position already stored with
each medium and obtains a readable place name from it. It also never re-reads
the media through Spotlight or ExifTool.

During Create and Update Control File, media from a date without its own track can
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

Like track maps, location maps have Standard and Time-Lapse versions. Map
Generation always maintains both variants together. Time-Lapse versions shift
the mapped positions at the same scale to maximize useful free space for
framed media. They appear as **Media locations** in the Generate and Update
Maps list and receive an `*` when either required version is missing or out of
date.

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
map file already highlighted. The **Previews** popup at the top left offers
**Off**, **Small**, **Medium**, and **Large**. Off keeps every row compact and
is the default on first use. The other choices enlarge only rows that can show
a preview; directive and date rows remain compact. The choice is remembered
for the current macOS user.

Music control uses separate `MUS` rows rather than an extra column. Slide-show
flow and timing use `CTL` rows. Press **Insert Row** or Command-I, choose the
desired Type, and enter the comma-separated commands in **File / Date / Map**.
A nonmodal command reference opens for a `MUS` or `CTL` row. The music reference
is also available from the small **Help** link beside **No Audio** in the main
window. The filter popup offers All Rows, No Media, Media, Maps,
`MUS - Music control`, `CTL - Slide-show control`, each individual
image/video/map type, and Date. **Reset Filter** returns to All Rows. The selected row remains visible
where possible; otherwise the nearest preceding matching row is selected.
Filtering never changes the saved row order, and search uses only visible rows.

Press **Choose** in the main window's Start Slide Show section to open the
control-file editor. **Start Slide Show Here** is available both at the bottom
and in the row context menu. **Jump to Show** immediately selects the latest row reported by
the running player and then follows playback. In a filtered view it follows the
nearest preceding matching row, such as the current Track Map or Music command.
While following, selecting one different row jumps the running slide show to
that exact control-file location and following continues from there. Editing a
cell or selecting multiple rows stops following; press **Jump to Show** again
to return to the live position.

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

The status line below the buttons says either `No control file available yet`
or shows statistics such as number of images, videos, track maps, date rows,
whether an overview map is present, how many images have place names, and the
last modification date.

Adventure Processing window:

- Is reused for metadata, place names, map generation, and control-file work.
- Keeps phase headings and prior output so the whole operation can be followed.
- Shows the active phase, progress, and current filename in its title.
- Reports extracted dates/GPS values, skipped files, map work, warnings, and
  control-file actions.
- Always scrolls to the latest line.
- Cancel remains available throughout processing and preserves completed
  metadata and maps.
- Skip appears during place-name extraction and omits only that phase for the
  current run without changing the selected Add place names option.
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
- Off, Small, Medium, and Large thumbnail previews.
- Previews appear progressively without blocking scrolling. Compact cached
  thumbnails are reused when the editor is opened again.
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

## Updating The Control File

Update Control File:

- Reads the active control file and current Track Map summary once.
- Reports overview/stage-map references to add, replace, or remove. It
  preserves `Day before`/`Day after` section types and does not duplicate maps.
- A map recreated under the same filename needs no reference update.
- If maps or their summary are outdated, it asks you to run **Generate and
  Update Maps** first. Metadata can still be refreshed, but track-dependent
  media placement waits for current map information.
- Compares project media with a companion
  `<control-file>.mycamino-state.json` inventory. GUI imports and files first
  discovered after an established inventory are recommended additions. On the
  first legacy scan, media already in the control file is included; other files
  are unclassified and remain unchecked unless they match an otherwise empty
  stage unambiguously.
- Keeps **Add**, **Update Metadata**, and **Move** independent. Repairing a
  sidecar never inserts an intentionally excluded medium into the show.
- Provides Recommended, New, Unclassified, Excluded, Included Updates, and All
  filters with visible-row selection controls and a count beside every filter.
  Unchecked absent media is remembered as excluded after Apply and is hidden
  from the normal review, but remains available through the Excluded filter;
  select **Add** there to restore it to the control file.
- Treats disabled photo/video rows as included. If the control file was edited
  outside myCamino, removed media is shown as unclassified for confirmation
  rather than silently treated as a deliberate exclusion.
- Shows the exact myCamino import/discovery time when known. A legacy filesystem
  creation/copy date is marked as estimated rather than presented as exposure
  metadata.
- Use **Choose Other Media...** and **Recheck Selected** to inspect a Current
  file again when you nevertheless suspect its embedded metadata changed.
- Shows filename/type, membership reason, added-to-project time, exposure date,
  GPS, place, and proposed control section before writing; recommended updates
  are checked and can be disabled per row.
- Inserts new media and proposes repositioning changed rows through the same
  classifier used by Create. Repositioning can be rejected per file.
- Preserves an existing place when GPS remains within the configured
  **Place-name search radius** (150 m by default). A larger GPS change proposes
  a targeted place lookup; if declined, stale place fields are cleared so
  Update Metadata can fill them later.
- Applies required Track Map corrections and selected media changes to one
  staged model. It writes sidecars and affected media maps first, backs up the
  active control file, and replaces that file last.
- Creates Day before/Day after sections when the adjacent-date and distance
  rules qualify.
- Avoids duplicate media entries.
- Cancel or failure leaves the active project unchanged.

## Audio, Music, and Narration

The Slide Show control file can contain directives of the form
`#MUSIC: Parameters`. They can jump to a playlist location, play a particular
piece, select loop behavior, change the internal volume, or switch music off
and on at that point in the show. Playlists are editable text files and contain
`$LABEL` entries for songs and albums. You can add your own labels for useful
jump locations. Music is stored in the project's `audio` subdirectory as
individual files or album folders. **No Audio** disables both music and
narration. Click **Help** beside it for the
complete command reference and the labels currently available in the selected
playlist.

`#PLAY:` accepts a comma-separated mixture of labels, pathnames, and inclusive
label ranges such as `$A - $D`. It plays that finite music selection and then
resumes the interrupted title at its saved position. `#NARRATOR:` uses the same
syntax with the separate playlist in the project's `narration` folder. It plays
the selected recordings once while the visuals continue, then waits for the
next narrator row. Quote or backslash-escape pathnames containing separators or
blanks.

`#JUMP $LABEL` and `#GOTO $LABEL` are equivalent music commands.

## Slide-Show Control Commands

The control-file editor also supports `CTL – Slide-show control` rows, saved as
`#CONTROL: Parameters`. Editing a CTL row opens a command reference. Commands
and `$LABEL` names are case-insensitive, and multiple commands are separated by
commas.

- `#LABEL $NAME` defines a destination in the slide-show list.
- `#GOTO $NAME` or `#JUMP $NAME` continues from that destination.
- `#DURATION NN` changes how long following slides and map insets remain visible.
- `#TRANSITION STYLE` selects `TIME_LAPSE`, `BLEND`, `FADE`, `SWITCH`, `EXPAND`, `COLLAGE`, `QUAD`, or `RANDOM`, like pressing `t` or `Shift-t`.
- `#PAUSE NN` holds the current picture for the requested seconds while music continues.
- `#END` applies the Adventure's configured black, loop-once, or loop-forever ending.

Duration and transition settings remain active until another command changes
them or the show restarts from its title page. Use the editor's CTL filter to
find these rows quickly.

## Hidden Media, Captions, and Fonts

Choose **Hide Media** from an image or video row's context menu to retain that
row while excluding it from playback. Hidden rows are gray and are saved with
`# ` before the original line. Choose **Unhide Media** to restore them. The
prefix can also disable any other control-file line when entered manually.

`CAP – Photo/video caption` rows are saved as `#CAPTION:` and label only the
immediately following enabled photo or video. Captions default to bottom
center. Add `#TOP`, `#MIDDLE`, or `#BOTTOM` and `#LEFT`, `#CENTER`, or `#RIGHT`
to position and align them. Use `\\n` for line breaks and double quotes around
text containing commas. Video captions remain visible for the complete clip.

`FNT – Label font` rows are saved as `#FONT:`. Use `#SIZE`, `#STYLE`, and
`#FAMILY` independently; `#DEFAULT` restores the Adventure's Header settings.
These settings also control following automatic stage headers. Captions use the
configured text color and shadow without covering the image with a box.

The Audio section contains separate Music and Narration playlist rows:

Use the `+`/`−` button beside **Audio** to expand or collapse those rows. The
choice is remembered for each Adventure. **No Audio** disables both music and
narration independently; **Normalize Video Audio** remains available in the
compact header row.

- Audio folder icon: open the project's fixed `audio` folder in Finder. Copy
  MP3, M4A, AAC, WAV, AIFF, CAF, or FLAC files into it. Subdirectories that
  contain audio files are treated as albums.
- Playlist field and Choose: show and select an existing `.playlist` file
  directly inside the `audio` folder. Without a selected playlist, all audio
  files play in case-insensitive relative-path order.
- Create Playlist: choose a new playlist filename in the `audio` folder and
  write an editable playlist. Each album folder and filename receives a unique
  short `$LABEL`.
- Update Playlist: keep existing text and append newly discovered files,
  grouped by folder, with new unique album and file labels.
- Edit Playlist: open the active playlist in TextEdit. If no playlist exists
  yet, it is first created from the supported audio files in the directory.
- Normalize Video Audio: explicitly prepare reusable copies of project videos
  with a consistent sound level. Originals are never modified. Current copies
  are skipped, stale copies are rebuilt, and cancellation retains every
  completed result. Generated copies live in `normalized-videos` and are
  excluded from media import and control files.
- Settings > Audio controls music and video playback percentages, whether
  prepared copies are preferred at startup, the `-16 LUFS` target, maximum
  boost, true-peak ceiling, and music crossfade duration.
- The Adventure stores `audio` as its fixed music source and the explicit
  selected playlist path.
- Narration uses the fixed `narration` folder and the same recursive Create,
  Update, Edit, album-label, and file-label implementation as music.
- Settings > Audio chooses whether music remains parallel, is reduced to 25%
  by default, or is faded out and paused while narration speaks. Video sound is
  reduced to 25% by default during narration.
  Renaming or copying an Adventure with related files enabled also renames or
  copies its playlist; the audio recordings themselves remain unchanged.

## Start Slide Show

The Start Slide Show section contains:

- Start: launches from the beginning using the initial style saved in Settings.
- Continue: opens a table of up to twenty automatically saved checkpoints,
  newest first. The table shows the last playback time, image or map, place,
  media date, and availability. Select a valid entry and press Return,
  double-click it, or choose **Play**. **Abort** and Escape leave the GUI
  unchanged. If a slide show is already running, Continue does not launch a
  second process; it rebuilds the running show at its current control-file row.
- The initial style defaults to Time-Lapse. During playback, `t` cycles forward
  and `Shift-t` backward through Time-Lapse, Blend, Fade, Switch, Expand,
  Collage, Quad, and Random.
- Settings > Slide Show > **At end** controls completion. **Black final slide**
  ends on black, **Loop once** replays the complete show once and then ends on
  black, and **Loop forever** continuously replays it. Loop forever is the
  default. Every replay starts again with the title slide.
- A fresh Start first shows the Adventure title, date/place/distance summary,
  description, and title image directly over the Tour Overview without a
  background panel. The text is black with a light readability shadow, and the
  title image has a soft drop shadow. Choose the title image below Description
  in the Adventure section;
  **Use First** uses the first still image in the control file. The title image
  is limited to 35% of the screen width and height. Descriptions shorter than
  five displayed lines are centered. At show startup, the temporary `h` help
  hint is placed at the bottom and disappears after five seconds. The title information disappears without fading the
  unchanged background map through black, followed by the first Stage Map.
  Press Space or Right/Down to advance from the title immediately. Without a
  key press, it advances automatically after 30 seconds. Continue does not
  replay the introduction, while end-of-show loops do.
- In one-window mode each Standard stage is Stage Map, marked Tour Overview,
  then media. Cursor navigation crosses stage boundaries in both directions;
  Command-cursor jumps directly between Stage Maps.
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
  geocoded place can appear as one of the three centered title lines. When the
  clock option is enabled,
  the analog clock in the upper-left follows the moving GPX marker time
  and the optional speedometer beside it follows the 500 m running mean. It is
  shown only when recorded GPX timestamps support a meaningful speed. Changing
  its window or stationary threshold updates small sidecar metadata files and
  does not regenerate or download maps.
  throughout the stage rather than showing a fixed media time; the separate
  date subtitle is then omitted. The upper-right map header shows Total,
  Stage, and Height values.
  Fade, Blend, Collage, Quad, and every other media style use this same clock,
  three-line title area, sizing, and track-statistics layout. Settings have
  separate controls for the initial Full, Simple, or Off presentation header,
  the persistent logical map header, clock, stage name, track length and
  duration, place name, and statistics. Press `c` to cycle the presentation
  header for the current session; the map header follows its content if the
  physical displays are swapped. Enabled title fields are packed from the top with
  no empty rows, and the first displayed line is larger. One Header layout
  setting controls photos, Time-Lapse maps, and overview maps. No box and
  Semi-transparent overlay leave pictures and maps full-frame, with the header
  aligned to the fitted picture or map edge. Header area is the default; it
  uses the selected slideshow background color and fits the display beneath
  it while maps remain full-width. Font color also controls the clock marks and hands, time, and
  date. Shadow color is configurable and defaults to black. Apply changes the
  active slide show immediately, and `c` toggles the complete configured
  header.
- PDF Summary: opens the same PDF export panel used by `myCamino GPX Editor`.
  It exports the current GPX track table and can optionally include overview,
  track maps, elevation profiles, and page orientation choices.
- Quit: auto-save pending Adventure changes and close the main GUI.

The control-file editor also offers **Start Slide Show Here** in the
right-click menu. It starts at the selected map, media, date, or music row. If
the table has unsaved edits, choose **Save and Start** first. This is a one-time
start location; the existing Continue history is not changed until the running
player later records where it actually stopped.

Window behavior is **Automatic** by default. A computer with one display starts
with one slide-show window. At the beginning of every new Time-Lapse stage, the
overview is shown by default as a framed medium over the stage map for the
current media duration, including when a separate overview window is active.
The current stage route is projected into that smaller overview and highlighted
there, followed by the animated route. The separate overview map retains the
elevation profile throughout the stage. Settings can
disable the duplicate inset for a separate display or restore a full-screen
overview instead. In a Standard show, the single-window sequence is
Stage Map, marked Tour Overview, then media. The marked overview uses a runtime
black title panel; the Intro uses the same full-tile overview without a
duplicated baked-in title. A computer with two or more displays starts
with a separate overview window. Settings can force either Single window or
Separate overview window regardless of display count.

In Standard two-window playback, GPS route tracking is independent of `t` and
`Shift-t`: changing the photo transition does not stop the moving map. The
`t`/`Shift-t` cycle therefore remains Time-Lapse, Blend, Fade, Switch, Expand,
Collage, Quad, and Random; GPS route tracking is controlled separately with `g`.

In single-window automatic Time-Lapse playback, the temporary overview advances
to the elevation profile and then to the moving stage after the configured media
duration for each static phase. In separate-window playback the persistent
profile does not add another phase. Left/Right step through the visible phases;
going left from the overview enters the previous stage rather than merely
rewinding the moving marker. In Standard playback,
the track map is shown once at the beginning of each stage. The Settings option
**Track map before each medium** can show the
marked track map before every photo or video; it is off by default.

On two screens, Time-Lapse uses the same single runtime header overlay for the
Stage Map and marked Tour Overview. The Overview no longer retains a second
embedded gray caption beneath that header.

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
current control-file row, stage and phase, visible medium, Time-Lapse progress,
exact background-music state, active display duration, and transition style. The main GUI adds this checkpoint to the
Adventure's newest-first history and keeps at most twenty. **Continue** lets
you choose any still-valid checkpoint; stale entries remain visible with a
reason but cannot be played. **Start** begins at the beginning without deleting
history. A show that reaches its natural end adds no checkpoint and leaves
earlier history unchanged. Music restoration includes the current title and
elapsed time, queue or loop, control-file audio gate, manual Audio Off state,
and `#VOLUME` level. If songs or playlist entries changed, the slide show still
resumes and reconstructs music from preceding `#MUSIC:` directives.

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
  At the beginning, backward navigation returns through the Tour Overview,
  Stage Map, and Intro title page. The startup help hint is not shown again
  during backward navigation.
- Command-Left or Command-Right: jump to the previous or next map-backed stage,
  including media-only stages without a GPX track. Command-Left at the first
  stage returns to the Intro title page. Repeated presses browse lightweight
  stage previews immediately; full media, audio, profile, and timing setup runs
  only for the stage where you stop.
- `+` or `-`: change media duration. In Time-Lapse this is a minimum; the
  current image remains visible longer whenever no newer image is due.
- Command-`+` or Command-`-`: change the active stage time-lapse duration by
  five seconds.
- `t`: cycle forward through Time-Lapse and the Standard transition styles.
- `T` (Shift-`t`): cycle backward through the same styles.
- `w`: switch between one slide-show window and a separate overview window.
  Closing the overview window also returns to one-window playback without
  stopping the show.
- `i`: show photo metadata overlay.
- `c`: cycle the logical presentation header through Full, Off, and Simple.
  A separate logical map display keeps the configured map header.
- `f`: toggle fullscreen.
- `d` and `D`: move or swap the configured displays.
- `e`: switch the Stage Map elevation-profile introduction on or off. The
  Adventure setting is on by default; the key changes only the running show.
- `n`: switch subsequent videos between valid normalized copies and originals.
  If a video is active, playback resumes at the same time and in the same
  paused/playing state. If no current normalized copy exists, playback is left
  unchanged.
- `h`: show help while held; it remains visible for five seconds after release.
- `s`: open Adventure Settings directly at the combined Slide Show section in
  the main myCamino window. Header and Time-Lapse options are grouped in their
  own subsections, separated by thin lines. Apply updates the running show.
  It also restores the previous dual-screen/fullscreen display state.
- `q` or Escape: quit slide show.

Because the slide show runs separately, quitting it should not close or crash
the main GUI.

### Music playlists and directives

A playlist is a plain text file. Blank lines are ignored, paths are relative to
the project's `audio` directory, and a line such as `$MORNING` labels the next
audio file. Several labels may precede one file. Labels are case-insensitive;
ambiguous extensionless names are skipped with a warning. Subfolders that
directly contain audio are albums and receive an `$ALB_...` label when a
playlist is created.

Add a standalone line such as `#MUSIC: #JUMP $MORNING` to the control file.
Entries are CSV-style comma-separated; quote a pathname containing commas.
Available commands are:

- `#PLAY: $A, $B - $D, "Song.mp3"`: temporarily play a finite mixed selection,
  then resume the interrupted playlist title at its saved position.
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

The time-lapse uses recorded GPX point times where available. Gaps in a partly
timed track are estimated along travelled distance. A completely untimed track
uses a relative distance-based timeline and the configured fallback walking
speed; no artificial calendar date is created. Its map, elevation profile,
distance, PDF output, and moving marker remain available, but its clock and
date are omitted. Photos cannot be assigned to that track by exposure time and
missing photo GPS cannot be inferred until the user supplies and saves a track
start time in the GPX Editor. Creating or playing a time-lapse does not change
the GPX file.

The position marker normally continues moving while a photo or video is shown.
If it reaches the scheduled position of another medium before the current one
has received its full display time, the marker waits at that position. The next
medium then starts there after the previous one has finished. If the marker has
already reached the final track time but later-dated media remain in the stage,
the marker stays at the endpoint while the analog clock advances to the capture
time and date of each displayed medium.

Adjacent-day sections are static in Time-Lapse mode. The related stage map is
shown first with **Day before** or **Day after** in the second header line,
where the normal track length and duration would appear. If the clock is
visible, media-only stage headers also omit their separate date line because
the clock already shows it. Framed media then appears in the available map
corners. No pilgrim, arrow, or travelled-distance figures are added to this
special section. A media GPS position is still marked when available, and the
overview window continues to highlight the associated route. These sections
do not count as extra travelled stages.

## Standalone GPX Editor App

The DMG also contains `myCamino GPX Editor.app`. Start it by clicking the
**myCamino GPX Editor** icon in Applications. This is the same GPX editor
that opens from Add & Edit Tracks, but it can also be launched directly for GPX
work that is not part of a slide-show project. Its gear button edits GPX
processing, PDF export, and map-service settings. Standalone settings are kept
for future sessions; when opened from an Adventure, they are auto-saved in that
Adventure instead.

In the main track table, right-click one selected track or a multiple selection
to inspect, plot, sort, duplicate, move, show/hide, join, or delete it. A
right-click on an unselected row first selects only that track. **Sort Selected
Tracks…** lets you choose a metric and direction while leaving every unselected
row in place. Duplicated tracks are inserted together after the last selected
row and receive unique copy names. Every ordering, visibility, duplication,
join, and deletion operation can be undone with the editor's Undo command.

**Renumber Tracks** stores the current row order as persistent track numbers.
You can therefore arrange tracks manually, renumber them, temporarily sort by
another column, and later sort by **Track Number** to restore the recorded
order. Hidden tracks remain part of that order, but appear gray and are not
included in the bold summary row's visible-track count. If existing generated
maps match by track fingerprint, myCamino relinks their numbered filenames and
metadata without downloading tiles or rendering the images again.

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

Current map sidecars also identify whether the PNG is a background-only map
and contain the route or ordered media positions used for dynamic drawing.
For a media-only Adventure, the same folder contains one date map per
`#MediaMap:` section; a GPX summary file is not required.

## Historical weather

Select **Add historical weather** in Media to enrich valid media sidecars from
Open-Meteo. This is optional because the request sends the media coordinate and
exposure time to an external service. The free endpoint is limited to
non-commercial use and needs no account or API key. Customer access requires an
Open-Meteo account and suitable paid plan; its API key is stored in macOS
Keychain rather than in the Adventure.

Weather retrieval is a separate Adventure Processing phase. Current weather is
reused, nearby media are grouped, and missing weather does not cause EXIF
metadata or place names to be recalculated. The slideshow can show a condition
icon and up to two selected values beside the speedometer. During Time-Lapse,
the latest reached weather remains visible for up to one hour of track/media
time. Weather data is an historical model estimate, not a camera measurement.

Weather data: [Open-Meteo](https://open-meteo.com/), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Troubleshooting

- If the GPX section says no GPX file is available, choose or create a GPX file
  first, or select **Journey source: Media locations** when the Adventure has
  no measured GPX track.
- If maps are missing or outdated, use **Generate and Update Maps**. Missing or
  stale stages are preselected.
- If the Slide Show Control File status says its references need updating, use
  Update Control File. If it instead says maps need regeneration, run
  **Generate and Update Maps** first and then Update Control File.
- If the control file cannot be created with tracks, check that
  `trackimages/<projectname>-summary.json` exists.
- If media have GPS metadata but no place names, use Update Metadata
  with Add place names selected.
- If the media browser reports **Missing** or **Invalid** metadata, select the
  files in Update Control File or use Update Metadata; place-name
  extraction does not repair missing media metadata by itself.
- If the slide show does not start, check that the control file and
  `trackimages/` directory both exist.
