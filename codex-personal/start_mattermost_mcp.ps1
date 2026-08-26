$ErrorActionPreference = 'Stop'

function Read-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $line = Get-Content -LiteralPath $Path | Where-Object {
        $_ -match ('^' + [regex]::Escape($Name) + '=')
    } | Select-Object -First 1
    if (-not $line) {
        throw "Missing $Name in $Path"
    }
    return ($line -replace ('^' + [regex]::Escape($Name) + '='), '').Trim()
}

$vibeRoot = $env:VIBECODING_ROOT
if (-not $vibeRoot) {
    $vibeRoot = [Environment]::GetEnvironmentVariable('VIBECODING_ROOT', 'User')
}
$envFile = if ($vibeRoot) { Join-Path $vibeRoot '.env' } else { $null }
if (-not $envFile) {
    throw 'Cannot find VibeCoding .env. Set VIBECODING_ROOT first.'
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Cannot find VibeCoding .env at $envFile. Check VIBECODING_ROOT."
}

$server = Join-Path $HOME '.claude\mcp-servers\mattermost\server.py'
if (-not (Test-Path -LiteralPath $server)) {
    throw "Mattermost MCP server is missing: $server"
}

$env:DOS_URL = Read-DotEnvValue -Path $envFile -Name 'DOS_URL'
$env:DOS_TOKEN = Read-DotEnvValue -Path $envFile -Name 'DOS_TOKEN'
$env:NO_PROXY = '*'

& python $server
exit $LASTEXITCODE
