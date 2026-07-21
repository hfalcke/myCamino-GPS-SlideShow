# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ffmpeg_binary = Path('vendor/ffmpeg/ffmpeg')
ffmpeg_license = Path('vendor/ffmpeg/COPYING.LGPLv2.1')


a = Analysis(
    ['GPSTrackShow.py'],
    pathex=[],
    binaries=[(str(ffmpeg_binary), '.')] if ffmpeg_binary.is_file() else [],
    datas=([
        (f'pilgrim-frame{index:02d}-rigged-512.png', '.')
        for index in range(9)
    ] + [
        ('build/license_bundle/app_resources/licenses', 'licenses'),
    ] + ([(str(ffmpeg_license), 'licenses/ffmpeg')] if ffmpeg_license.is_file() else [])),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GPSTrackShow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GPSTrackShow',
)
