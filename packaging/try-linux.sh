#!/bin/sh
# Installs the package built in dist/ on a fresh copy of its system (a Docker image, as
# given to build-linux.sh) with the system's own installer, then checks that it starts:
# `kaden --help` answers, and the desktop app is still running after 30 s, drawn off
# screen. Run from the project root.
set -eu
image=$1
case "$image" in
    ubuntu:*) install='apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q ./dist/*.deb' ;;
    fedora:*) install='dnf install -y -q ./dist/*.rpm' ;;
    archlinux:*) install='pacman -Syu --noconfirm --quiet && pacman -U --noconfirm dist/*.pkg.tar.zst' ;;
    *) echo "no installer known for $image" >&2; exit 2 ;;
esac
docker run --rm -v "$PWD/dist:/work/dist:ro" -w /work "$image" sh -euc "
    $install
    kaden --help > /dev/null
    echo 'kaden --help: ok'
    status=0
    QT_QPA_PLATFORM=offscreen timeout 30 kaden-desktop > /tmp/app.log 2>&1 || status=\$?
    if [ \$status -ne 124 ] || grep -q Traceback /tmp/app.log; then
        cat /tmp/app.log
        echo \"the app did not keep running (exit \$status)\" >&2
        exit 1
    fi
    echo 'kaden-desktop: still running after 30 s'
"
