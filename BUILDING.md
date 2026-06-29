# Building & releasing

This repo is "flat" (`addon.xml` at the root), so the source archive GitHub
generates (`Code -> Download ZIP`) is **not** directly installable in Kodi —
its top folder is the repo name, and Kodi's built-in unpacker also trips on
GitHub's streaming zip format (`Failed to unpack archive`). Build a proper
package instead.

## Build a Kodi-installable zip

Run on the `public` branch:

```powershell
# Windows
./build.ps1
```
```sh
# Linux / macOS / LibreELEC
./build.sh
```

Output: `dist/plugin.video.myshows-<version>.zip`. The top folder is
`plugin.video.myshows`, so Kodi installs it via **Add-ons -> Install from zip
file**. Build scripts and other dev files are excluded from the zip via
`.gitattributes` (`export-ignore`).

## Cut a release

The version comes from `addon.xml`. Bump it there first, then:

```powershell
./build.ps1 -Release      # builds, then `gh release create vX.Y.Z` + uploads the zip
```

or manually:

```sh
gh release create v<version> dist/plugin.video.myshows-<version>.zip \
  --title "v<version> - MyShows.me for Kodi" \
  --notes "..."
```

Needs the GitHub CLI (`gh`) installed and authenticated (`gh auth login`).
