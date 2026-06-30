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

## Kodi auto-update repository

The `repo` branch hosts a small Kodi repository (`addons.xml` + `addons.xml.md5`
+ addon zips under `zips/`), served over `raw.githubusercontent.com`. Users
install `repository.myshows.me` once and then Kodi updates the addon
automatically.

To publish an update to repository users after cutting a release, run:

```powershell
./publish-repo.ps1
```

It reads the version from the `public` branch, rebuilds the addon zip, and
refreshes `zips/addons.xml` + `addons.xml.md5` on the `repo` branch (via a
temporary git worktree — your current branch is untouched), then pushes `repo`
to the `github` remote.

### Full release flow

1. Bump `version` in `addon.xml` (on `public`).
2. `./build.ps1 -Release` — publishes a GitHub Release with the zip.
3. `./publish-repo.ps1` — pushes the same version to the `repo` branch so
   installed users get the auto-update.

> The `repo` branch also contains the `repository.myshows.me` add-on source.
> Its zip (for first-time install) lives at
> `zips/repository.myshows.me/repository.myshows.me-<version>.zip`.
