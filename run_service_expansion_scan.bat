@echo off
rem One-click runner for the SERVICE-CATEGORY EXPANSION only: refreshes the
rem 25 matrix pairs for nightlife, event venues, fitness, pet services,
rem transport rental and moving/storage, then scores, exports and opens the
rem PRIVATE research report. The original 36 matrix pairs are NOT re-scanned
rem (use run_scan.bat for the complete 61-request matrix).
rem
rem The cap is exactly 25 = the expansion pairs in config\scan_matrix.yml.
rem If those pairs change, update the two 25s below (tests/test_project.py
rem fails when this file falls out of sync).
cd /d "%~dp0"

set CATS=nightlife,event_venues,fitness,pet_services,transport_rental,moving_storage

rem Safety check BEFORE any API request: the dry run must plan exactly 25.
py scripts\scan_places.py --matrix --matrix-categories %CATS% --dry-run --max-requests 25 > "%TEMP%\leads_dry_run.txt" || goto :error
type "%TEMP%\leads_dry_run.txt"
findstr /C:"Total requests that would be made: 25" "%TEMP%\leads_dry_run.txt" >nul || goto :plan_mismatch

py scripts\scan_places.py --matrix --matrix-categories %CATS% --max-requests 25 || goto :error
py scripts\score_leads.py || goto :error
py scripts\export_report.py || goto :error

start "" "private\leads.html"
echo.
echo Done! The private research report just opened in your browser.
echo (The public directory pages were refreshed in reports\.)
pause
exit /b 0

:plan_mismatch
echo.
echo STOPPED BEFORE ANY API REQUEST: the dry run did not plan exactly 25
echo requests, so the expansion pairs in config\scan_matrix.yml no longer
echo match this runner. Update the two "25"s in this file to match.
pause
exit /b 1

:error
echo.
echo Something went wrong - read the message above.
echo (Most common cause: no .env file with your API key yet. See README.md.)
pause
exit /b 1
