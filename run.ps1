if (-not (Test-Path ".venv")) {
    Write-Host "Creating Virtual Environment..." -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "Activating Virtual Environment..." -ForegroundColor Cyan
. .venv\Scripts\Activate.ps1

Write-Host "Installing dependencies..." -ForegroundColor Green
python -m pip install --upgrade pip
pip install -r requirements.txt

if (-not $env:GEMINI_API_KEY) {
    Write-Host "WARNING: GEMINI_API_KEY environment variable is not set." -ForegroundColor Yellow
    Write-Host "Set it before uploading PDFs so the AI can process them." -ForegroundColor Yellow
    Write-Host "Example: `$env:GEMINI_API_KEY=`"your_key_here`"" -ForegroundColor Yellow
}

Write-Host "Refreshing Environment Variables..." -ForegroundColor Cyan
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "Starting PDF AI Assistant on http://localhost:5000 ..." -ForegroundColor Green
python app.py
