@echo off
rem PRAMANA demo launcher: backend + frontend (the assistant with push-to-talk is inside the app now).
rem Ollama must be running (it starts with Windows). Close the two windows to stop.
set ROOT=%~dp0
set PYTHONUTF8=1

start "PRAMANA backend :8000" cmd /k "cd /d %ROOT%sih-project\backend && python main.py"
timeout /t 8 /nobreak >nul
start "PRAMANA frontend :5173" cmd /k "cd /d %ROOT%sih-project\frontend && npm run dev"
rem load the speech models and the LLM now (about 20 s) so the first question in the app is not slow
start "warm-up" /min cmd /c "curl -s -m 180 -X POST http://127.0.0.1:8000/assistant/warm >nul"
timeout /t 4 /nobreak >nul
start http://localhost:5173
echo.
echo Choose an engine in the browser, then open Assistant (hold the mic or F9 to talk).
echo Add ?engine=vrde_180 to the address to skip the engine screen.
