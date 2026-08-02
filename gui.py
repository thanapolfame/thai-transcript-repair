#!/usr/bin/env python3
"""Open the browser GUI for repairing transcripts.

    ./gui.py

The same repair as ``repair.py``, for people who would rather drag a file onto a
page than type a command.  Nothing leaves the machine: the server listens on
127.0.0.1 and the outputs come back as browser downloads.
"""

import argparse

from thairepair.webgui import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--port", type=int, default=0, help="port to serve on (default: any free port)"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    args = parser.parse_args(argv)

    # argparse hands back a Namespace of Any; pin the types at the boundary.
    port: int = args.port
    no_browser: bool = args.no_browser

    serve(port=port, open_browser=not no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
