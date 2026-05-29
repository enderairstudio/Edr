import sys

class CliError(Exception):
    pass

def handle_error(err_type, message):
    print(f"\n[ERROR: {err_type}]")
    print(f"Details: {message}")
    sys.exit(1)

def fail(err_type, message):
    raise CliError(f"{err_type}: {message}")
