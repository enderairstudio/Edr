import sys

class CliError(Exception):
    pass

def handle_error(err_type, message):
    # Finish any in-progress "\r...N%" progress line and write to stderr —
    # matching print.error()'s convention — instead of leaving a stray
    # partial progress line and printing to stdout, where redirecting
    # stdout (e.g. `edr pull ... > out.json`) would silently swallow it.
    try:
        import print as p

        p.progress_finish()
    except Exception:
        pass
    print(f"[ERROR: {err_type}]", file=sys.stderr)
    print(f"Details: {message}", file=sys.stderr)
    sys.exit(1)

def fail(err_type, message):
    raise CliError(f"{err_type}: {message}")
