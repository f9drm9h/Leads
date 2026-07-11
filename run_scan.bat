@echo off
rem One-click runner: runs the targeted SDE scan matrix (capped at 40 API
rem requests as a safety net), scores the leads, exports the reports, then
rem opens the PRIVATE research report in your browser.
rem Preview first without spending API requests:
rem     py scripts\scan_places.py --matrix --dry-run
cd /d "%~dp0"

py scripts\scan_places.py --matrix --max-requests 40 || goto :error
py scripts\score_leads.py || goto :error
py scripts\export_report.py || goto :error

start "" "private\leads.html"
echo.
echo Done! The private research report just opened in your browser.
echo (The public directory pages were refreshed in reports\.)
pause
exit /b 0

:error
echo.
echo Something went wrong - read the message above.
echo (Most common cause: no .env file with your API key yet. See README.md.)
pause
exit /b 1
