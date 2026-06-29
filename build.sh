#!/usr/bin/env bash
# Build a Kodi-installable zip of this addon from the current git branch.
# Usage (run on the `public` branch):
#   ./build.sh            # build dist/plugin.video.myshows-<version>.zip
#   ./build.sh --release  # also create the GitHub release and upload the zip (needs gh)
set -euo pipefail
cd "$(dirname "$0")"

id=$(sed -n 's/.*<addon[^>]* id="\([^"]*\)".*/\1/p' addon.xml | head -1)
version=$(sed -n 's/.*<addon[^>]* version="\([^"]*\)".*/\1/p' addon.xml | head -1)

mkdir -p dist
zip="dist/${id}-${version}.zip"
rm -f "$zip"

git archive --format=zip --prefix="${id}/" -o "$zip" HEAD
echo "Built: $zip"

if [ "${1:-}" = "--release" ]; then
    gh release create "v${version}" "$zip" \
        --title "v${version} - MyShows.me for Kodi" \
        --notes "plugin.video.myshows ${version}. Install the attached zip via Kodi: Add-ons -> Install from zip file."
    echo "Released v${version}"
fi
