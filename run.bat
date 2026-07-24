@echo off
chcp 65001 >nul
echo FinVision 시작 중...
echo.

rem 이전에 남은 서버(포트 8000/5173)를 먼저 정리한다.
rem 포트 소유자를 찾아, 워커면 부모 프로세스까지 트리째 종료 → 재실행해도 포트 충돌 없음.
echo 이전 서버 정리 중 (포트 8000, 5173)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($port in 8000,5173) { $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue).OwningProcess | Select-Object -Unique; foreach ($op in $owners) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $op) -EA SilentlyContinue; $root = $op; if ($p) { $par = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId) -EA SilentlyContinue; if ($par -and ($par.CommandLine -like '*uvicorn*' -or $par.CommandLine -like '*vite*' -or $par.CommandLine -like '*npm*')) { $root = $par.ProcessId } }; taskkill /F /T /PID $root 2>$null | Out-Null } }; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null }"
timeout /t 2 /nobreak >nul

echo 백엔드 시작 (http://localhost:8000)
start "FinVision Backend" cmd /k "cd /d %~dp0backend && python -m uvicorn app.main:app --reload --port 8000"
timeout /t 2 /nobreak >nul
echo 프론트엔드 시작 (http://localhost:5173)
start "FinVision Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
timeout /t 3 /nobreak >nul
echo.
echo 브라우저에서 http://localhost:5173 을 열어주세요.
start http://localhost:5173
