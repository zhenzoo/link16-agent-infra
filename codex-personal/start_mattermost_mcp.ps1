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

$roots = @()
if ($env:VIBECODING_ROOT) {
    $roots += $env:VIBECODING_ROOT
}
$roots += 'D:\410_VibeCoding'

$envFile = $roots |
    ForEach-Object { Join-Path $_ '.env' } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $envFile) {
    throw 'Cannot find VibeCoding .env. Set VIBECODING_ROOT first.'
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
