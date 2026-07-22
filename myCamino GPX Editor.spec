# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from application_metadata import APP_BUNDLE_VERSION, bundle_build_number

hiddenimports = ['pyexpat', 'PIL._tkinter_finder']
hiddenimports += collect_submodules('objc')
hiddenimports += collect_submodules('AppKit')
hiddenimports += collect_submodules('Foundation')
hiddenimports += collect_submodules('Quartz')
hiddenimports += collect_submodules(
    'matplotlib',
    filter=lambda name: not (
        name == 'matplotlib.tests' or name.startswith('matplotlib.tests.')
    ),
)
hiddenimports += collect_submodules('contextily')
hiddenimports += collect_submodules(
    'rasterio',
    filter=lambda name: not (
        name == 'rasterio.rio' or name.startswith('rasterio.rio.')
    ),
)

a = Analysis(
    ['GPXEditor.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('MyCaminoLogo-ohneText.png', '.'),
        ('docs', 'docs'),
        ('build/license_bundle/app_resources/licenses', 'licenses'),
    ],
    hiddenimports=hiddenimports,
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
    name='myCamino GPX Editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='myCamino GPX Editor',
)
app = BUNDLE(
    coll,
    name='myCamino GPX Editor.app',
    icon='build/MyCaminoLogo-ohneText.icns',
    bundle_identifier=None,
    version=APP_BUNDLE_VERSION,
    info_plist={'CFBundleVersion': bundle_build_number()},
)
