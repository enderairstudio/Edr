# DEPRECATED for Windows releases — use Inno Setup instead (avoids PyInstaller AV flags).
#   powershell -File build.ps1
# Or manually: pyinstaller edr.spec  then  ISCC installer\EDR-Setup.iss
#
# This spec remains only as a fallback if Inno Setup is not installed.

block_cipher = None

a = Analysis(
    ['setup_installer.py'],
    pathex=[],
    binaries=[],
    datas=[('dist\\edr', 'edr'), ('icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EDR-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
    version='version_info.py',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='EDR-Setup',
)
