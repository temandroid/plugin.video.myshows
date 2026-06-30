<#
publish-repo.ps1 — update the `repo` branch so the Kodi repository serves the
current plugin.video.myshows version. Installed users then auto-update.

Run from anywhere in the repo (uses a temporary git worktree, so it does NOT
touch your current branch/working tree). Requires:
  - a `github` remote pointing at the GitHub repo
  - the addon source on the `public` branch (the published version)
  - the `repo` branch already initialised (zips/ + repository.myshows.me/)

Usage:
    ./publish-repo.ps1
#>
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
Set-Location $root
$id = 'plugin.video.myshows'

# Version comes from the published (public) addon.xml
[xml]$addon = [string]::Join("`n", (git show "public:addon.xml"))
$version = $addon.addon.version
Write-Host "Publishing $id $version to the 'repo' branch..."

$wt = Join-Path ([IO.Path]::GetTempPath()) 'msshows-repo-wt'
if (Test-Path $wt) { git worktree remove --force $wt 2>$null; if (Test-Path $wt) { Remove-Item -Recurse -Force $wt } }
git worktree add -q $wt repo
try {
    $zdir = Join-Path $wt "zips\$id"
    New-Item -ItemType Directory -Force $zdir | Out-Null
    Get-ChildItem $zdir -Filter "$id-*.zip" -ErrorAction SilentlyContinue | Remove-Item -Force
    $zip = Join-Path $zdir "$id-$version.zip"
    git archive --format=zip --prefix="$id/" public -o $zip
    if ($LASTEXITCODE -ne 0) { throw 'git archive failed' }

    # Regenerate addons.xml = plugin (from public) + repo addon (from repo branch)
    $enc = New-Object Text.UTF8Encoding $false
    $pluginLines = (git show 'public:addon.xml') | Where-Object { $_ -notmatch '<\?xml' }
    $repoLines   = (Get-Content (Join-Path $wt 'repository.myshows.me\addon.xml')) | Where-Object { $_ -notmatch '<\?xml' }
    $lines = @('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<addons>') + $pluginLines + $repoLines + @('</addons>')
    $addonsPath = Join-Path $wt 'zips\addons.xml'
    [IO.File]::WriteAllText($addonsPath, [string]::Join("`n", $lines) + "`n", $enc)
    $md5 = (Get-FileHash $addonsPath -Algorithm MD5).Hash.ToLower()
    [IO.File]::WriteAllText((Join-Path $wt 'zips\addons.xml.md5'), $md5, $enc)

    git -C $wt add -A
    if (-not (git -C $wt status --porcelain)) {
        Write-Host "Nothing to publish — repo branch already serves $version."
    } else {
        git -C $wt commit -q -m "Publish $id $version"
        git -C $wt push -q github repo
        Write-Host "Published $id $version. addons.xml md5 = $md5"
    }
} finally {
    git worktree remove --force $wt 2>$null
}
