@echo off
rem One-click runner: scans all areas/categories, scores the leads,
rem exports the reports, then opens the HTML report in your browser.
cd /d "%~dp0"

py scripts\scan_places.py --all || goto :error
py scripts\score_leads.py || goto :error
py scripts\export_report.py || goto :error

start "" "reports\leads.html"
echo.
echo Done! The report just opened in your browser.
pause
exit /b 0

:error
echo.
echo Something went wrong - read the message above.
echo (Most common cause: no .env file with your API key yet. See README.md.)
pause
exit /b 1
