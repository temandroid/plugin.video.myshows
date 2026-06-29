<#
Build a Kodi-installable zip of this addon from the current git branch.

Usage (run on the `public` branch):
    .\build.ps1            # build dist\plugin.video.myshows-<version>.zip
    .\build.ps1 -Release   # also create the GitHub release and upload the zip (needs gh, logged in)

The zip's top folder is the addon id, and `git archive` writes a plain zip that
Kodi's built-in unpacker accepts (unlike the streaming source archives that
GitHub's "Download ZIP" produces). Dev-only files are excluded via .gitattributes.
#>
param([switch]$Release)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

[xml]$addon = Get-Content (Join-Path $root 'addon.xml')
$id      = $addon.addon.id        # plugin.video.myshows
$version = $addon.addon.version   # e.g. 1.6.2

$dist = Join-Path $root 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$zip = Join-Path $dist "$id-$version.zip"
if (Test-Path $zip) { Remove-Item $zip }

git archive --format=zip --prefix="$id/" -o $zip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
Write-Host "Built: $zip"

if ($Release) {
    gh release create "v$version" $zip `
        --title "v$version - MyShows.me for Kodi" `
        --notes "plugin.video.myshows $version. Install the attached zip via Kodi: Add-ons -> Install from zip file."
    if ($LASTEXITCODE -ne 0) { throw "gh release failed" }
    Write-Host "Released v$version"
}
