# myCamino GPS SlideShow

myCamino is a macOS application for presenting multi-day journeys from GPX
tracks and recorded media. It associates photographs and videos with track
positions and times, then presents the journey using maps, route progress,
elevation information and media.

The repository contains two applications:

- **myCamino GPS Track Show** prepares and runs the presentation.
- **myCamino GPX Editor** inspects, corrects, joins, splits and exports GPX
  tracks.

## Main functions

- Import GPX tracks, photographs and videos from a multi-day journey.
- Generate stage and overview maps using OpenStreetMap or another configured
  tile provider. Guided first-use setup links to supported provider accounts;
  hosted API keys remain in the user's macOS Keychain.
- Display route progress as a time-lapse with a moving position marker.
- Show photographs and videos together with the applicable map, track,
  elevation, place, date and time information.
- Use standard full-window photo transitions, overview maps, stage maps and
  elevation profiles within the same presentation.
- Animate the timed route independently on a second display while Standard
  photos and videos retain their selected transition.
- Add optional background music and retain video sound.
- Prepare the presentation automatically while keeping its control file,
  media order, tracks, music and playback settings editable.
- Correct or restructure tracks in the included GPX Editor without modifying
  the original files.

## Current beta

The current build supports **Apple-Silicon Macs only**. It is not signed or
notarized by Apple. macOS therefore requires explicit approval through
**System Settings → Privacy & Security → Open Anyway** for both applications.
Read the complete installation instructions before overriding this warning.

The software is in beta testing and is supplied without warranty. Use the
[structured bug-report form](https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/new?template=01-bug.yml)
for reproducible problems or the
[feature-request form](https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/new?template=02-feature-request.yml)
for proposed improvements. Reports containing personal material or security
information should instead use the private
[project contact form](https://mycamino.heinofalcke.de/contact/).

## Download and documentation

- [Project website](https://mycamino.heinofalcke.de/)
- [Download page, release details and installation instructions](https://mycamino.heinofalcke.de/download/)
- [Current DMG](https://mycamino.heinofalcke.de/downloads/myCamino-GPS-Track-Show.dmg) — requires a verified beta-download session
- [Web documentation](https://mycamino.heinofalcke.de/documentation/)
- [GPS Track Show user guide](docs/MYCAMINO_GPS_TRACK_SHOW_USER_GUIDE.md)
- [GPX Editor user guide](docs/GPXEDITOR_USER_GUIDE.md)
- [Programmer guide](docs/PROGRAMMER_GUIDE.md)

The published SHA-256 checksum should be compared with the downloaded DMG
before installation.

## Source code and licence

myCamino is free software licensed under
[GPL-3.0-or-later](LICENSE). It may be used, studied, modified and
redistributed under those terms.

Release DMGs include the corresponding source archive, build identification
and third-party licence notices. Further details are in
[SOURCE_CODE.md](SOURCE_CODE.md), [COPYRIGHT](COPYRIGHT) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
