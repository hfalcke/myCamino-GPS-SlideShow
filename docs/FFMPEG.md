# FFmpeg in the standalone bundle

myCamino uses FFmpeg only when the user explicitly runs **Normalize Video
Audio**. The build is pinned to FFmpeg 8.1.1 and configured without GPL,
nonfree, version3, or auto-detected third-party components.

Build it with:

```bash
scripts/build_ffmpeg_lgpl.sh
```

The script downloads the corresponding source archive from `ffmpeg.org`,
builds the `ffmpeg` command, and stores the executable and LGPL 2.1 license in
`vendor/ffmpeg`. `build_dmg.sh` invokes this reproducible build when that
executable is absent. `GPSTrackShow.spec` includes the executable and license
in the standalone player bundle.

FFmpeg is a trademark of Fabrice Bellard, originator of the FFmpeg project.
The generated `vendor/ffmpeg/README.txt` records the exact source URL and build
configuration used for redistribution.
