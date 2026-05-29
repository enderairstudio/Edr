import sys

import handler


if __name__ == "__main__":
    try:
        sys.exit(handler.main())
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(130)
