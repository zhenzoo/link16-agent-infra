param([Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$Bot)
$ErrorActionPreference = 'Stop'
$name = "Link16-Proactive-Followup-$Bot"
$interpreter = (Get-Command pythonw.exe -CommandType Application | Select-Object -First 1).Source
$entry = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'proactive_followup.py')).Path
$arguments = '"{0}" --bot {1} tick' -f $entry, $Bot
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Actions.Count -ne 1 -or $existing.Actions[0].Execute -ne $interpreter -or
        $existing.Actions[0].Arguments -ne $arguments -or $existing.State -eq 'Disabled') {
        throw "Existing task has a different action or is disabled: $name"
    }
} else {
    $action = New-ScheduledTaskAction -Execute $interpreter -Argument $arguments -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(15)) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
}
Get-ScheduledTaskInfo -TaskName $name | Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult
