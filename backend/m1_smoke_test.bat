@echo off
setlocal EnableExtensions

set "BASE_URL=%~1"
if "%BASE_URL%"=="" set "BASE_URL=http://127.0.0.1:8000"

set "SID=%~2"
set "TMP_DIR=%TEMP%\m1_smoke_%RANDOM%%RANDOM%"
set "PAYLOAD_FILE=%TMP_DIR%\payload.json"
set "CREATE_FILE=%TMP_DIR%\create.json"
set "STATUS_FILE=%TMP_DIR%\status.json"
set "BODY_FILE=%TMP_DIR%\body.json"

mkdir "%TMP_DIR%" >nul 2>&1

echo ========================================
echo M1 Smoke Test
echo BASE_URL: %BASE_URL%
echo ========================================

if not defined SID (
  > "%PAYLOAD_FILE%" (
    echo {
    echo   "candidate_id": "cand-%RANDOM%",
    echo   "job_id": "job-%RANDOM%",
    echo   "meeting_url": "https://demo.daily.co/room",
    echo   "meeting_type": "daily",
    echo   "schedule_time": "2026-04-09T00:00:00Z",
    echo   "config": {
    echo     "max_duration_minutes": 45,
    echo     "max_questions": 10,
    echo     "topics": ["technical_skills","problem_solving","behavioural","culture_fit","topic5","topic6","topic7","topic8","topic9","topic10"],
    echo     "language": "en",
    echo     "avatar_persona": "alex"
    echo   }
    echo }
  )

  curl -s -X POST "%BASE_URL%/api/v1/sessions" -H "Content-Type: application/json" --data-binary "@%PAYLOAD_FILE%" > "%CREATE_FILE%"
  if errorlevel 1 (
    echo [FAIL] Could not call POST /api/v1/sessions
    goto :cleanup_fail
  )

  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$json = Get-Content -Raw '%CREATE_FILE%' | ConvertFrom-Json; $json.session_id"`) do set "SID=%%I"
  if not defined SID (
    echo [FAIL] Could not parse session_id from response:
    type "%CREATE_FILE%"
    goto :cleanup_fail
  )

  call echo [OK] Created session: %%SID%%
) else (
  echo [INFO] Using provided session: %SID%
)

call :get_status
if errorlevel 1 goto :cleanup_fail

call :post_command pause
if errorlevel 1 goto :cleanup_fail

call :post_command resume
if errorlevel 1 goto :cleanup_fail

call :post_command extend_5min
if errorlevel 1 goto :cleanup_fail

echo [INFO] Waiting up to 40s for session to reach ENDED...
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$sid='%SID%'; $base='%BASE_URL%'; for($i=0; $i -lt 40; $i++){ try { $state = (Invoke-RestMethod -Uri ($base + '/api/v1/sessions/' + $sid) -TimeoutSec 3).state } catch { $state = 'ERR' }; if($state -eq 'ENDED'){ 'ENDED'; exit 0 }; Start-Sleep -Seconds 1 }; 'TIMEOUT'"`) do set "END_WAIT=%%I"

if /I not "%END_WAIT%"=="ENDED" (
  echo [FAIL] Session did not reach ENDED in expected time.
  call :get_status
  goto :cleanup_fail
)

echo [OK] Session reached ENDED
call :get_status
if errorlevel 1 goto :cleanup_fail

call :post_command_expect_409 skip_question
if errorlevel 1 goto :cleanup_fail

call :post_command_expect_409 end_interview
if errorlevel 1 goto :cleanup_fail

call :post_command_expect_409 extend_5min
if errorlevel 1 goto :cleanup_fail

call :post_no_body_expect_409 "%BASE_URL%/api/v1/sessions/%SID%/events/candidate_left" candidate_left_terminal
if errorlevel 1 goto :cleanup_fail

call :post_no_body_expect_409 "%BASE_URL%/api/v1/sessions/%SID%/events/candidate_rejoined" candidate_rejoined_terminal
if errorlevel 1 goto :cleanup_fail

echo ========================================
echo [PASS] M1 smoke test completed successfully.
echo Session: %SID%
echo ========================================
goto :cleanup_ok

:get_status
curl -s "%BASE_URL%/api/v1/sessions/%SID%" > "%STATUS_FILE%"
if errorlevel 1 (
  echo [FAIL] Could not call GET /api/v1/sessions/%SID%
  exit /b 1
)
echo [status]
type "%STATUS_FILE%"
echo.
exit /b 0

:post_command
set "COMMAND_NAME=%~1"
for /f "delims=" %%C in ('curl -s -o "%BODY_FILE%" -w "%%{http_code}" -X POST "%BASE_URL%/api/v1/sessions/%SID%/command" -H "Content-Type: application/json" -d "{\"command\":\"%COMMAND_NAME%\"}"') do set "HTTP_CODE=%%C"
echo [%COMMAND_NAME%] HTTP %HTTP_CODE%
type "%BODY_FILE%"
echo.
if "%HTTP_CODE%"=="200" exit /b 0
if "%HTTP_CODE%"=="201" exit /b 0
exit /b 1

:post_command_expect_409
set "COMMAND_NAME=%~1"
for /f "delims=" %%C in ('curl -s -o "%BODY_FILE%" -w "%%{http_code}" -X POST "%BASE_URL%/api/v1/sessions/%SID%/command" -H "Content-Type: application/json" -d "{\"command\":\"%COMMAND_NAME%\"}"') do set "HTTP_CODE=%%C"
echo [%COMMAND_NAME%_terminal] HTTP %HTTP_CODE%
type "%BODY_FILE%"
echo.
if "%HTTP_CODE%"=="409" exit /b 0
exit /b 1

:post_no_body_expect_409
set "TARGET_URL=%~1"
set "LABEL=%~2"
for /f "delims=" %%C in ('curl -s -o "%BODY_FILE%" -w "%%{http_code}" -X POST "%TARGET_URL%"') do set "HTTP_CODE=%%C"
echo [%LABEL%] HTTP %HTTP_CODE%
type "%BODY_FILE%"
echo.
if "%HTTP_CODE%"=="409" exit /b 0
exit /b 1

:cleanup_ok
rd /s /q "%TMP_DIR%" >nul 2>&1
exit /b 0

:cleanup_fail
echo ========================================
echo [FAIL] M1 smoke test failed.
echo Session (if created): %SID%
echo Temp files kept at: %TMP_DIR%
echo ========================================
exit /b 1
