@echo off
chcp 65001 >nul
echo [FinVision] 백엔드 재시작 - 포트 8000 정리 중...

rem 포트 8000 점유 프로세스를 찾아, 워커면 부모(uvicorn 리로더)까지 트리째 종료.
rem 이렇게 해야 리로더가 워커를 다시 살리지 못한다. 텔레그램 크롤러(main.py)는 건드리지 않음.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$owners = (Get-NetTCPConnection -LocalPort 8000 -State Listen -EA SilentlyContinue).OwningProcess | Select-Object -Unique; foreach ($op in $owners) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $op) -EA SilentlyContinue; $root = $op; if ($p) { $par = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId) -EA SilentlyContinue; if ($par -and $par.CommandLine -like '*uvicorn*') { $root = $par.ProcessId } }; taskkill /F /T /PID $root 2>$null | Out-Null }; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null }; $d = (Get-Date).AddSeconds(10); while ((Get-NetTCPConnection -LocalPort 8000 -State Listen -EA SilentlyContinue) -and (Get-Date) -lt $d) { Start-Sleep -Milliseconds 300 }"

echo [FinVision] 백엔드 시작 (http://localhost:8000)
start "FinVision Backend" cmd /k "cd /d %~dp0backend && python -m uvicorn app.main:app --reload --port 8000"
echo 완료. 백엔드 로그는 새 창에서 확인하세요.
