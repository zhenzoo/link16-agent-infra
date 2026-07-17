[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Source = (Join-Path $HOME '.gstack\repos\gstack'),
    [string]$Destination = (Join-Path $HOME '.agents\skills'),
    [string]$BackupRoot = (Join-Path $HOME '.codex-migration-backups')
)

$ErrorActionPreference = 'Stop'

function Assert-ChildPath {
    param([string]$Parent, [string]$Child)
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childFull = [IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch path outside $Parent`: $Child"
    }
}

$sourceFull = [IO.Path]::GetFullPath($Source)
$destFull = [IO.Path]::GetFullPath($Destination)
$generated = Join-Path $sourceFull '.agents\skills'
$versionFile = Join-Path $sourceFull 'VERSION'

if (-not (Test-Path -LiteralPath (Join-Path $sourceFull '.git'))) {
    throw "gstack source checkout not found: $sourceFull"
}
if (-not (Test-Path -LiteralPath $versionFile)) {
    throw "gstack VERSION not found: $versionFile"
}
if (-not (Test-Path -LiteralPath $generated)) {
    throw "Generated Codex skills not found. Run gstack setup --host codex first: $generated"
}

$version = (Get-Content -Raw -LiteralPath $versionFile).Trim()
$newSkills = @(Get-ChildItem -LiteralPath $generated -Directory -Force |
    Where-Object { $_.Name -like 'gstack*' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'SKILL.md')) } |
    Sort-Object Name)
if (-not $newSkills) {
    throw "No generated gstack Codex skills found under $generated"
}

# Old direct installs copied the Claude-format source directories straight into
# ~/.agents/skills. Archive those directories instead of deleting them.
$legacyNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
Get-ChildItem -LiteralPath $sourceFull -Directory -Force | ForEach-Object {
    if (Test-Path -LiteralPath (Join-Path $_.FullName 'SKILL.md')) {
        [void]$legacyNames.Add($_.Name)
    }
}
[void]$legacyNames.Add('gstack')
[void]$legacyNames.Add('connect-chrome')
# Pre-1.58 installs exposed the browser body as a second skill named `gstack`
# under this internal directory. The current top-level gstack is a router, so
# retaining the old command produces a duplicate skill name in Codex.
[void]$legacyNames.Add('_gstack-command')
foreach ($skill in $newSkills) {
    [void]$legacyNames.Add($skill.Name)
}

$existingToArchive = @()
foreach ($name in ($legacyNames | Sort-Object)) {
    $candidate = Join-Path $destFull $name
    if (Test-Path -LiteralPath $candidate) {
        Assert-ChildPath -Parent $destFull -Child $candidate
        $existingToArchive += Get-Item -Force -LiteralPath $candidate
    }
}

Write-Host "gstack source : $sourceFull"
Write-Host "gstack version: $version"
Write-Host "destination   : $destFull"
Write-Host "archive count: $($existingToArchive.Count)"
Write-Host "publish count: $($newSkills.Count)"

if (-not $Apply) {
    Write-Host '[dry-run] Existing gstack entries that would be archived:'
    $existingToArchive | ForEach-Object { Write-Host "  - $($_.Name)" }
    Write-Host '[dry-run] Generated Codex entries that would be published:'
    $newSkills | ForEach-Object { Write-Host "  + $($_.Name)" }
    exit 0
}

New-Item -ItemType Directory -Force -Path $destFull | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $BackupRoot "$stamp\gstack-agents-before-$version"
New-Item -ItemType Directory -Force -Path $backup | Out-Null

foreach ($item in $existingToArchive) {
    $target = Join-Path $backup $item.Name
    Assert-ChildPath -Parent $backup -Child $target
    Move-Item -LiteralPath $item.FullName -Destination $target
}

foreach ($skill in $newSkills) {
    $target = Join-Path $destFull $skill.Name
    Assert-ChildPath -Parent $destFull -Child $target
    Copy-Item -LiteralPath $skill.FullName -Destination $target -Recurse -Force
}

$manifest = [ordered]@{
    managed_by = 'link16-agent-infra/codex-personal/refresh_gstack_codex.ps1'
    generated_at = (Get-Date).ToString('o')
    version = $version
    source = $sourceFull
    destination = $destFull
    backup = $backup
    skills = @($newSkills.Name)
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $destFull '.link16-gstack-sync.json')

Write-Host "Published gstack $version for Codex."
Write-Host "Previous entries archived at: $backup"
