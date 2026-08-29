# Third-Party Notices

myCamino GPS SlideShow is licensed under GPL-3.0-or-later. The applications
also bundle third-party software under the separate licenses described below.
Those components are not relicensed as original myCamino project content.
Complete license texts and build-specific package versions are included in the
`licenses/third-party` directory of each application and in the DMG.

## Open-Meteo Weather Data

Optional historical weather is supplied by [Open-Meteo](https://open-meteo.com/).
API data is licensed under the Creative Commons Attribution 4.0 International
licence: https://creativecommons.org/licenses/by/4.0/. Weather is downloaded
only after the user enables this feature; Open-Meteo's service terms apply.

## Python Runtime

The applications include Python, distributed under the Python Software
Foundation License. Source and license information is available from
https://www.python.org/.

## Python Packages

The packaged runtime includes the following projects and their transitive
runtime dependencies. The build records the exact installed version and copies
each package's supplied license or notice files.

- PyObjC and the Cocoa, AVFoundation, AVKit, CoreAudio, CoreLocation,
  CoreMedia, and Quartz wrappers: MIT license; https://pyobjc.readthedocs.io/
- NumPy: BSD and other compatible licenses documented by NumPy;
  https://numpy.org/
- Matplotlib, ContourPy, Cycler, FontTools, KiwiSolver, PyParsing, Packaging,
  Pillow, and Python Dateutil: permissive licenses supplied by each project.
- Rasterio, Affine, Contextily, Mercantile, XYZServices, Joblib, Click, and
  Cligj: permissive licenses supplied by each project.
- Geopy and GeographicLib: permissive licenses supplied by each project.
- Requests, Certifi, Charset Normalizer, IDNA, and urllib3: permissive or
  MPL-2.0 licenses supplied by each project.
- Setuptools and its bundled runtime support modules: MIT and other permissive
  licenses supplied in the Setuptools distribution.

## FFmpeg

The bundled FFmpeg command-line executable is built from FFmpeg 8.1.1 with
GPL, nonfree, and version-3 components disabled. It is distributed under
LGPL-2.1-or-later. The DMG includes the exact upstream source archive, its
SHA-256 checksum, the configuration used to build it, and the LGPL text.

Upstream source: https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz

## Map and Location Services

Map tiles, geographic names, and other remotely retrieved data remain subject
to the attribution and usage terms of their selected providers. They are not
bundled third-party software and are not covered by the myCamino license.
