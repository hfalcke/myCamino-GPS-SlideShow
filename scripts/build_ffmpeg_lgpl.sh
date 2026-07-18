#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="8.1.1"
BUILD_ROOT="${FFMPEG_BUILD_ROOT:-/tmp/mycamino-ffmpeg-${VERSION}}"
ARCHIVE="${BUILD_ROOT}/ffmpeg-${VERSION}.tar.xz"
SOURCE_DIR="${BUILD_ROOT}/ffmpeg-${VERSION}"
PREFIX="${BUILD_ROOT}/install"
OUTPUT_DIR="${ROOT_DIR}/vendor/ffmpeg"

mkdir -p "${BUILD_ROOT}" "${OUTPUT_DIR}"
if [[ ! -f "${ARCHIVE}" ]]; then
  curl --fail --location --output "${ARCHIVE}" \
    "https://ffmpeg.org/releases/ffmpeg-${VERSION}.tar.xz"
fi
if [[ ! -d "${SOURCE_DIR}" ]]; then
  tar -C "${BUILD_ROOT}" -xf "${ARCHIVE}"
fi

cd "${SOURCE_DIR}"
./configure \
  --prefix="${PREFIX}" \
  --disable-gpl \
  --disable-nonfree \
  --disable-version3 \
  --disable-autodetect \
  --disable-doc \
  --disable-debug \
  --disable-ffplay \
  --disable-ffprobe \
  --enable-static \
  --disable-shared \
  --enable-audiotoolbox \
  --enable-videotoolbox
make -j"$(sysctl -n hw.logicalcpu)" ffmpeg
make install

cp "${PREFIX}/bin/ffmpeg" "${OUTPUT_DIR}/ffmpeg"
chmod +x "${OUTPUT_DIR}/ffmpeg"
cp "${SOURCE_DIR}/COPYING.LGPLv2.1" "${OUTPUT_DIR}/COPYING.LGPLv2.1"
printf '%s\n' \
  "FFmpeg ${VERSION}" \
  "Built without GPL, nonfree, or version3 components by scripts/build_ffmpeg_lgpl.sh." \
  "Source: https://ffmpeg.org/releases/ffmpeg-${VERSION}.tar.xz" \
  > "${OUTPUT_DIR}/README.txt"

echo "Created ${OUTPUT_DIR}/ffmpeg"
