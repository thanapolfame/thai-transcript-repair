#!/usr/bin/env bash
# Start the browser GUI on macOS or Linux.  First run builds .venv and installs
# the dependencies; later runs go straight to the browser.
#
# Linux users: run ./start-gui.sh.  On macOS, double-click start-gui.command
# instead — Finder will not run a .sh.

set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"

find_python() {
    for candidate in python3.13 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            # 3.13 is the runtime this project pins; anything older is refused
            # here rather than failing later inside the type annotations.
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

if [ ! -x "$PY" ]; then
    echo "ครั้งแรก: กำลังติดตั้ง (first run: setting up, a few minutes)…"
    if ! PYTHON=$(find_python); then
        echo "ไม่พบ Python 3.13 — ติดตั้งจาก https://www.python.org/downloads/" >&2
        echo "Python 3.13 or newer is required." >&2
        exit 1
    fi
    "$PYTHON" -m venv "$VENV"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
    echo "ติดตั้งเสร็จแล้ว (setup complete)"
fi

exec "$PY" gui.py "$@"
