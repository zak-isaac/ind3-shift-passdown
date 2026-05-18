# install_service.ps1
# Run this ONCE as Administrator to register the passdown server
# as a Windows Scheduled Task that starts at boot (no login required),
# restarts on hibernation resume, and auto-restarts on crash.
#
# Usage (from an elevated PowerShell):
#   cd "C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server"
#   .\install_service.ps1

$TaskName   = "IND3PassdownServer"
$ProjectDir = "C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server"
$Launcher   = "$ProjectDir\start_server.bat"
$LogFile    = "$ProjectDir\service_install.log"

# Remove old task if it exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Action: run the batch launcher hidden (no console window)
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$Launcher`"" `
    -WorkingDirectory $ProjectDir

# Triggers: at system startup + on session resume (wake from sleep/hibernate)
$TriggerBoot   = New-ScheduledTaskTrigger -AtStartup
$TriggerResume = New-ScheduledTaskTrigger -AtLogOn   # catches resume events too
# Use CIM to add a "On resume from sleep" trigger
$ResumeXml = @"
<CallerInfo xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
</CallerInfo>
"@

# Settings: restart on failure, run whether user logged in or not, highest privilege
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

# Principal: SYSTEM account — no password needed, runs with no user logged in
$Principal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

# Register the task
Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $Action `
    -Trigger    $TriggerBoot `
    -Settings   $Settings `
    -Principal  $Principal `
    -Description "IND3 Reliability Passdown server — auto-starts at boot without requiring a user login." `
    -Force

# Add the resume-from-sleep trigger by patching the XML directly
# (New-ScheduledTaskTrigger doesn't expose SessionStateChange natively)
$Task = Get-ScheduledTask -TaskName $TaskName
$Xml  = [xml]($Task | Export-ScheduledTask)
$ns   = "http://schemas.microsoft.com/windows/2004/02/mit/task"

$ResumeTrigger = $Xml.CreateElement("SessionStateChangeTrigger", $ns)
$StateChange   = $Xml.CreateElement("StateChange", $ns)
$StateChange.InnerText = "RemoteConnect"   # covers wake/resume
$ResumeTrigger.AppendChild($StateChange) | Out-Null

# Also add a plain "on resume" trigger
$WakeTrigger = $Xml.CreateElement("SessionStateChangeTrigger", $ns)
$WakeState   = $Xml.CreateElement("StateChange", $ns)
$WakeState.InnerText = "ConsoleConnect"
$WakeTrigger.AppendChild($WakeState) | Out-Null

$Xml.Task.Triggers.AppendChild($ResumeTrigger) | Out-Null
$Xml.Task.Triggers.AppendChild($WakeTrigger)   | Out-Null

Register-ScheduledTask `
    -TaskName $TaskName `
    -Xml      $Xml.OuterXml `
    -Force | Out-Null

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host " IND3 Passdown Server registered as a service!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host " Task name   : $TaskName"
Write-Host " Runs as     : NT AUTHORITY\SYSTEM"
Write-Host " Starts on   : Boot, resume from sleep/hibernate"
Write-Host " Restarts    : Every 1 min if it crashes (up to 99x)"
Write-Host " Port        : 8765"
Write-Host " Log file    : $ProjectDir\server.log"
Write-Host ""
Write-Host "Starting the task now..." -ForegroundColor Yellow
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2
$State = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host " Current state: $State" -ForegroundColor Cyan
Write-Host ""
Write-Host "To remove the task:" -ForegroundColor Gray
Write-Host '  Unregister-ScheduledTask -TaskName "IND3PassdownServer" -Confirm:$false' -ForegroundColor Gray
