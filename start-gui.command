#!/bin/sh
# macOS: double-click this file in Finder to open the GUI.
#
# Finder starts it in the home directory, so the first job is to get back to
# the folder this file lives in; the real work is in start-gui.sh.
cd "$(dirname "$0")" || exit 1
exec ./start-gui.sh "$@"
