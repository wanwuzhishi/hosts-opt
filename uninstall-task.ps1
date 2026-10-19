#Requires -RunAsAdministrator
param(
    [string]$TaskName = "HostsOpt"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Scheduled task '$TaskName' not found."
    exit 0
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Removed scheduled task '$TaskName'."
Write-Host "The managed hosts block is unchanged."
Write-Host "To remove it, run as Administrator:"
Write-Host "  python `"$root\hosts_opt.py`" restore"
