#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="8.1.1"
ARCHIVE_SHA256="b6863adde98898f42602017462871b5f6333e65aec803fdd7a6308639c52edf3"
BUILD_ROOT="${FFMPEG_BUILD_ROOT:-/tmp/mycamino-ffmpeg-${VERSION}}"
SOURCE_CACHE="${FFMPEG_SOURCE_CACHE:-${ROOT_DIR}/build/third-party-sources}"
ARCHIVE="${SOURCE_CACHE}/ffmpeg-${VERSION}.tar.xz"
SOURCE_DIR="${BUILD_ROOT}/ffmpeg-${VERSION}"
PREFIX="${BUILD_ROOT}/install"
OUTPUT_DIR="${ROOT_DIR}/vendor/ffmpeg"
SOURCE_ONLY=0

if [[ "${1:-}" == "--source-only" ]]; then
  SOURCE_ONLY=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--source-only]" >&2
  exit 2
fi

mkdir -p "${BUILD_ROOT}" "${SOURCE_CACHE}" "${OUTPUT_DIR}"
if [[ ! -f "${ARCHIVE}" ]]; then
  curl --fail --location --output "${ARCHIVE}" \
    "https://ffmpeg.org/releases/ffmpeg-${VERSION}.tar.xz"
fi
ACTUAL_SHA256="$(shasum -a 256 "${ARCHIVE}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA256}" != "${ARCHIVE_SHA256}" ]]; then
  echo "FFmpeg source checksum mismatch: ${ACTUAL_SHA256}" >&2
  exit 1
fi
if [[ "${SOURCE_ONLY}" -eq 1 ]]; then
  echo "Verified ${ARCHIVE}"
  exit 0
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
  "SHA-256: ${ARCHIVE_SHA256}" \
  "Configure: --disable-gpl --disable-nonfree --disable-version3 --disable-autodetect --disable-doc --disable-debug --disable-ffplay --disable-ffprobe --enable-static --disable-shared --enable-audiotoolbox --enable-videotoolbox" \
  > "${OUTPUT_DIR}/README.txt"

echo "Created ${OUTPUT_DIR}/ffmpeg"
