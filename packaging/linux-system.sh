#!/bin/sh
# Builds the desktop app as a package for this Linux (an Arch .pkg.tar.zst here) into dist/.
# Briefcase makes one program, kaden-desktop; this adds kaden and kd as links to it, which
# run the kaden command (kaden_desktop/commands.py). Run from the project root.
set -eu
briefcase() { uvx --python /usr/bin/python3 briefcase "$@" linux system --no-input; }
rm -rf build/kaden-desktop
briefcase create
briefcase build
for bin in build/kaden-desktop/*/*/kaden-desktop-*/usr/bin; do
    ln -sf kaden-desktop "$bin/kaden"
    ln -sf kaden-desktop "$bin/kd"
done
briefcase package
