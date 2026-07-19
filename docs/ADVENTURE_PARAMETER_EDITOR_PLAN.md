# Adventure Parameter Editor Plan

Status: Implemented in the development version. This document records the
design decisions and verification scope.

## Confirmed Decisions

- Show common settings by default and technical settings through a **Show Advanced Settings** option.
- Keyboard adjustments during playback affect only the running slide-show session.
- Offer OpenStreetMap and Esri presets plus a configurable custom map provider.

## Summary

Add a standard macOS `gearshape` button immediately left of the myCamino logo. It opens a resizable parameter window with a section sidebar, scrollable forms, an optional **Show Advanced Settings** switch, and inline help.

Parameter changes are edited as a draft. **Apply** validates them, updates the current project, and triggers Adventure auto-save. **Cancel** discards the draft. Every row gets a reset-arrow button, while **Reset All** restores only parameter defaults, never project names or files.

## Editable Parameters

- **Slide Show:** initial style (Time-Lapse by default, followed by all standard transitions), media duration `3 s`, transition duration, background/font/marker colors, font size `30`, marker radius `6 px`, arrow scale `1.0`, clock, place names, fullscreen mode `Auto/On/Off`, automatic/single/separate overview window mode, optional marked track map before each Standard medium, joined windows, display swap, black/loop-once/loop-forever end behavior, manual start, collage range `33-66%`, and maximum collage images `9`.
- **Time-Lapse:** stage duration `30 s`, preferred minimum media size `50%`, framed/full-screen single-window overview, and walking-pilgrim/arrow marker style.
- **Map Generation:** ordering by date/track number, image size `1920x1080`, zoom `15`, route width `4`, route/endpoint/background/title colors, endpoint size `0`, font factor `2.2`, overview labels, removable track-name prefix, and Time-Lapse edge margin `5%`. Standard and Time-Lapse maps are always maintained together.
- **GPX Processing:** fallback walking speed `3.5 km/h`, horizontal smoothing `10 m`, minimum retained-point spacing `10 m`, elevation smoothing `50 m`, maximum horizontal/vertical error `10/20 m`, maximum HDOP/VDOP `20/20`, GPX Editor autosave interval `300 s`, map padding `8%`, default map zooms, elevation-profile headroom, and maximum interactive map tiles `48`. Zero disables an individual smoothing, spacing, or quality limit.
- **PDF Export:** document DPI `200`, embedded-map DPI `600`, overview/track zooms `8/14`, and maximum PDF map tiles `24`.
- **Locations:** known-place reuse radius `150 m`, reverse-geocoding timeout `10 s`, and advanced request pacing `1-5 s`.
- **Map Service:** OpenStreetMap, Esri, or Custom provider; custom `{z}/{x}/{y}` URL template, attribution, maximum zoom, request timeout `12 s`, and tile-cache retention `24 h`.

Custom providers accept HTTP or HTTPS templates but require `{z}`, `{x}`, and `{y}` plus attribution. The provider is shared by Map Generation and GPX Editor. Apple `CLGeocoder` remains the place-name service because it does not expose a configurable server endpoint.

Internal safety and implementation values remain hidden: memory-watchdog limits, 20-ms animation timing, metadata/cache versions, free-box calculation grid, history depth, and file-format constants.

## Architecture and Integration

- Introduce one typed parameter registry containing keys, sections, defaults, control types, ranges, choices, help text, advanced status, and validation. All components consume these defaults instead of duplicating constants.
- Store normalized values under a versioned `parameters` object in `.adv`. Continue reading and mirroring existing top-level Time-Lapse/map fields for compatibility. Missing, malformed, and future unknown fields are handled safely.
- Build controls from the registry: checkboxes, numeric/text fields, popups, color wells, and reset buttons. Invalid fields show an inline explanation and disable Apply.
- Pass Slide Show settings through its Python API and CLI. Add a tri-state fullscreen option while retaining existing CLI compatibility. Keyboard changes remain temporary for that running show.
- Pass project settings into embedded GPX Editor calls. Standalone GPX Editor stores its GPX/PDF/map-service subset in macOS Application Support. An already-open embedded editor receives applied settings for subsequent operations and rearms its autosave timer when needed.
- Extend `gpx_tracks_table.run_with_options()` and `GetGeoLocations.run_with_options()` with the relevant map, timing, filtering, provider, and geolocation parameters.
- Centralize Contextily/xyzservices provider creation and tile URL generation so map rendering, cache diagnostics, missing-tile handling, and GPX Editor all use the same configured service.
- Include map-affecting parameters in track-map metadata and freshness checks. Legacy maps remain current under legacy defaults; changed rendering parameters mark them stale. GPX filtering or timing changes regenerate the summary asynchronously and mark affected maps for Update.
- Loading an adventure restores settings before asynchronous project-status checks. Applying only Slide Show or geolocation settings does not unnecessarily invalidate maps.
- Update user help, user guides, programmer documentation, CLI help, and the `.adv` format description.

## Verification

- Test defaults, per-setting reset, Reset All, validation, Apply/Cancel, dirty-state handling, and loading older `.adv` files.
- Test all Slide Show settings reach both Standard and Time-Lapse launches without changing existing CLI behavior.
- Test settings propagation to embedded GPX Editor, map generation, PDF export, timestamp repair, and GetGeoLocations.
- Test OSM, Esri, and custom providers, including malformed templates, attribution, zoom limits, cache diagnostics, timeouts, and skipped 404 tiles.
- Test map staleness when visual, provider, filtering, timing, or edge-margin settings change.
- Smoke-test the gear placement, scalable parameter window, keyboard traversal, advanced-field visibility, color wells, tooltips, and project save/reload.

## Assumptions

- All exposed values are project-scoped. Machine-specific window coordinates are deliberately excluded.
- Applying settings marks the adventure dirty but does not silently save it.
