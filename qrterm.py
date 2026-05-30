"""Print pull commands as ASCII QR codes in the terminal."""

import sys
from pathlib import Path


def _load_qrcode_module():
    try:
        import qrcode  # type: ignore

        return qrcode
    except ImportError:
        pass

    vendor = Path(__file__).resolve().parent / "qrcode"
    if vendor.is_dir():
        parent = str(vendor.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        import qrcode  # type: ignore

        return qrcode
    return None


def print_qr(text, quiet=False):
    """Render a scannable QR in the terminal, or warn if the library is missing."""
    if not text:
        return False

    qrcode = _load_qrcode_module()
    if qrcode is None:
        if not quiet:
            print("QR: pip install qrcode  (optional; pull command is printed above)")
        return False

    qr = qrcode.QRCode(border=1, box_size=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    return True


def pull_command_text(remote, port=None):
    if remote and str(remote).startswith("Edrnko_"):
        return f"edr pull {remote}"
    if port and int(port) != 5005:
        return f"edr pull {remote} --port {port}"
    return f"edr pull {remote}"
