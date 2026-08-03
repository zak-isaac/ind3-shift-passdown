# install_service.ps1
# Run ONCE as Administrator to register the passdown server as a Windows
# Scheduled Task that starts at boot, survives hibernation, and auto-restarts.
#
# Usage (elevated PowerShell):
#   cd "C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server"
#   .\install_service.ps1

$TaskName   = "IND3PassdownServer"
$ProjectDir = "C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server"
$Launcher   = "$ProjectDir\start_server.bat"

# Remove old task if it exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Action: run the batch launcher via cmd (fully-qualified paths inside the bat)
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument ("/c `"" + $Launcher + "`"") `
    -WorkingDirectory $ProjectDir

# Triggers: system startup + session connect (covers hibernate resume + user switch)
$TriggerBoot    = New-ScheduledTaskTrigger -AtStartup
$TriggerLogon   = New-ScheduledTaskTrigger -AtLogOn

# Settings: no time limit, restart every 1 min on failure, run without a logged-in user
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

# Principal: SYSTEM -- no password, survives logoff/user-switching
$Principal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

# Register with boot trigger
Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $Action `
    -Trigger    $TriggerBoot `
    -Settings   $Settings `
    -Principal  $Principal `
    -Description "IND3 Reliability Passdown server - starts at boot, no login required." `
    -Force | Out-Null

# Patch in the hibernate-resume trigger via XML (SessionStateChangeTrigger)
# New-ScheduledTaskTrigger does not expose this trigger type natively
$Task = Get-ScheduledTask -TaskName $TaskName
$Xml  = [xml]($Task | Export-ScheduledTask)
$ns   = "http://schemas.microsoft.com/windows/2004/02/mit/task"

foreach ($stateChange in @("ConsoleConnect", "RemoteConnect")) {
    $trigger = $Xml.CreateElement("SessionStateChangeTrigger", $ns)
    $state   = $Xml.CreateElement("StateChange", $ns)
    $state.InnerText = $stateChange
    $trigger.AppendChild($state) | Out-Null
    $Xml.Task.Triggers.AppendChild($trigger) | Out-Null
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Xml      $Xml.OuterXml `
    -Force | Out-Null

# Start it immediately
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$State = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  IND3 Passdown Server installed as a service!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Task name : $TaskName"
Write-Host "  Runs as   : NT AUTHORITY\SYSTEM"
Write-Host "  Triggers  : Boot, hibernate resume, session connect"
Write-Host "  Restarts  : every 1 min on crash (up to 99x)"
Write-Host "  Port      : 8765"
Write-Host "  Log       : $ProjectDir\server.log"
Write-Host "  State now : $State" -ForegroundColor Cyan
Write-Host ""
Write-Host "To remove:" -ForegroundColor Gray
Write-Host "  Unregister-ScheduledTask -TaskName IND3PassdownServer -Confirm:`$false" -ForegroundColor Gray
