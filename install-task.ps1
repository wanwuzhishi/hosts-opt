#Requires -RunAsAdministrator
param(
    [int]$IntervalHours = 1,
    [string]$TaskName = "HostsOpt"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "hosts_opt.py"
$releaseExe = Join-Path $root "hosts-opt.exe"

if (-not (Test-Path $script) -and -not (Test-Path $releaseExe)) {
    throw "Neither hosts-opt.exe nor hosts_opt.py found in: $root"
}
if ($IntervalHours -lt 1 -or $IntervalHours -gt 999) {
    throw "IntervalHours must be between 1 and 999."
}

$exe = $null
$arguments = ""

if (Test-Path $releaseExe) {
    $hiddenRunner = Join-Path $root "run-hidden.vbs"
    if (Test-Path $hiddenRunner) {
        $exe = Join-Path $env:SystemRoot "System32\wscript.exe"
        $arguments = "`"$hiddenRunner`""
    } else {
        $exe = $releaseExe
        $arguments = "--cli update --apply"
    }
} else {
    $argPrefix = ""
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*WindowsApps*") {
        $pythonw = Join-Path (Split-Path $python.Source) "pythonw.exe"
        $exe = if (Test-Path $pythonw) { $pythonw } else { $python.Source }
    } else {
        $pyw = Get-Command pyw.exe -ErrorAction SilentlyContinue
        if ($pyw) {
            $exe = $pyw.Source
            $argPrefix = "-3 "
        } else {
            $py = Get-Command py.exe -ErrorAction SilentlyContinue
            if (-not $py) {
                throw "Python 3.10+ not found. Install Python or use the release EXE."
            }
            $exe = $py.Source
            $argPrefix = "-3 "
        }
    }
    $arguments = "$argPrefix`"$script`" update --apply"
}
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Write-Host "Installed scheduled task '$TaskName'."
Write-Host "Interval: every $IntervalHours hour(s), highest privileges."
Write-Host "Command : $exe $arguments"
Write-Host "Log      : $root\logs\hosts_opt.log"
Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File `"$root\uninstall-task.ps1`""
