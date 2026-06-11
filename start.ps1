# CMA Python backend launcher
# 1. Starts Ollama (if not already running) for the credit-committee agents
# 2. Starts the FastAPI server on http://127.0.0.1:8000

$projectRoot = $PSScriptRoot
if (-not $projectRoot) { $projectRoot = "D:\CMA_Python" }

# --- Ollama -----------------------------------------------------------------
$ollamaUp = $false
try {
    Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -UseBasicParsing | Out-Null
    $ollamaUp = $true
    Write-Host "Ollama already running."
} catch {}

if (-not $ollamaUp) {
    Write-Host "Starting Ollama..."
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "`$env:OLLAMA_MODELS='D:\OllamaModels'; & '$env:LOCALAPPDATA\Programs\Ollama\ollama.exe' serve"
    Start-Sleep -Seconds 3
}

# --- FastAPI ----------------------------------------------------------------
# The API package lives under src\, so uvicorn needs --app-dir.
& "$projectRoot\venv\Scripts\python.exe" -m uvicorn api.main:app `
    --app-dir "$projectRoot\src" `
    --host 127.0.0.1 --port 8000
