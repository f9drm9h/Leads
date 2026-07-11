@echo off
rem One-click runner: refreshes the COMPLETE 61-request targeted SDE matrix
rem (36 original pairs + 25 service-category expansion pairs), scores the
rem leads, exports the reports, then opens the PRIVATE research report.
rem
rem The cap is exactly 61 = the number of configured matrix pairs. If the
rem matrix in config\scan_matrix.yml grows or shrinks, update the two 61s
rem below (tests/test_project.py fails when this file falls out of sync).
rem
rem To refresh ONLY the new service categories (25 requests), use
rem     run_service_expansion_scan.bat
rem Preview without spending API requests:
rem     py scripts\scan_places.py --matrix --dry-run
cd /d "%~dp0"

rem Safety check BEFORE any API request: the dry run must plan exactly 61.
py scripts\scan_places.py --matrix --dry-run --max-requests 61 > "%TEMP%\leads_dry_run.txt" || goto :error
type "%TEMP%\leads_dry_run.txt"
findstr /C:"Total requests that would be made: 61" "%TEMP%\leads_dry_run.txt" >nul || goto :plan_mismatch

py scripts\scan_places.py --matrix --max-requests 61 || goto :error
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
echo STOPPED BEFORE ANY API REQUEST: the dry run did not plan exactly 61
echo requests, so config\scan_matrix.yml no longer matches this runner.
echo Update the two "61"s in run_scan.bat to the new matrix size.
pause
exit /b 1

:error
echo.
echo Something went wrong - read the message above.
echo (Most common cause: no .env file with your API key yet. See README.md.)
pause
exit /b 1
