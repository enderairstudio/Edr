# Build from the project root with: pyinstaller build/edr-gui.spec
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent
block_cipher = None
a = Analysis(
    [str(ROOT / 'edr_gui.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / name), '.') for name in (
        'command.py', 'handler.py', 'share.py', 'relay.py', 'guard.py',
        'error.py', 'print.py', 'watch.py', 'qrterm.py', 'doctor_checks.py'
    )],
    hiddenimports=['tkinter', 'qrcode'],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EDR Desktop', console=False, icon=str(ROOT / 'icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, name='EDR Desktop')
