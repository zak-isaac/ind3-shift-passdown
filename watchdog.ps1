# watchdog.ps1 — restart uvicorn if either port goes dark
$app  = "C:\Users\ins0002\Documents\shift-passdown-v2"
$uvicorn = "$app\.venv\Scripts\uvicorn.exe"

while ($true) {
    $up8765 = (netstat -ano | Select-String "0\.0\.0\.0:8765")
    if (-not $up8765) {
        Start-Process -FilePath $uvicorn `
                      -ArgumentList "main:app --host 0.0.0.0 --port 8765" `
                      -WorkingDirectory $app `
                      -WindowStyle Hidden
        Start-Sleep 10
    }

    $up8003 = (netstat -ano | Select-String "0\.0\.0\.0:8003")
    if (-not $up8003) {
        Start-Process -FilePath $uvicorn `
                      -ArgumentList "main:app --host 0.0.0.0 --port 8003" `
                      -WorkingDirectory $app `
                      -WindowStyle Hidden
        Start-Sleep 10
    }

    Start-Sleep 25
}
