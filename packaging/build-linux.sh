#!/bin/sh
# Builds the desktop app as a system package into dist/: for this Linux, or with a Docker
# image as the first argument (ubuntu:24.04, fedora:41, archlinux:latest), for that one.
# Briefcase makes one program, kaden-desktop; this adds kaden and kd as links to it, which
# run the kaden command (kaden_desktop/commands.py). Run from the project root.
set -eu
# A build for this Linux runs Briefcase on its own Python, as the package will use it.
if [ $# -gt 0 ]; then target="--target $1" python=3.13; else target="" python=/usr/bin/python3; fi
briefcase() { uvx --python "$python" briefcase "$@" linux system --no-input $target; }
rm -rf build/kaden-desktop
briefcase create
briefcase build
for bin in build/kaden-desktop/*/*/kaden-desktop-*/usr/bin; do
    ln -sf kaden-desktop "$bin/kaden"
    ln -sf kaden-desktop "$bin/kd"
done
briefcase package
