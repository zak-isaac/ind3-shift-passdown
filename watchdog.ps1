# watchdog.ps1 — restart uvicorn if port 8765 goes dark
$app  = "C:\Users\ins0002\Documents\shift-passdown-v2"
$uvicorn = "$app\.venv\Scripts\uvicorn.exe"

while ($true) {
    $up = (netstat -ano | Select-String "0\.0\.0\.0:8765")
    if (-not $up) {
        Start-Process -FilePath $uvicorn `
                      -ArgumentList "main:app --host 0.0.0.0 --port 8765" `
                      -WorkingDirectory $app `
                      -WindowStyle Hidden
        Start-Sleep 10
    }
    Start-Sleep 25
}
