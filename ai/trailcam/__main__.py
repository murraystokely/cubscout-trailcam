"""So that `python3 -m trailcam ...` works."""

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main())
