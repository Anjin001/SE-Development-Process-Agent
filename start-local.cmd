@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
  if not errorlevel 1 set "SE_PY=py -3"
)
if not defined SE_PY (
  python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
  if not errorlevel 1 set "SE_PY=python"
)
if not defined SE_PY (
  set "SE_PY="C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1118-904-0_26.1118.904.0_x64__3k8sg7r9htsxt\dependencies\python\python.exe""
)
%SE_PY% -m se_agent.workbench --port 8008
pause
