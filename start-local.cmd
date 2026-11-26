@echo off
setlocal
cd /d "%~dp0" || goto :project_error
if not exist "se_agent\workbench.py" goto :project_error

if defined SE_AGENT_PYTHON (
    call :check_python "%SE_AGENT_PYTHON%"
    if not errorlevel 1 goto :run
    echo SE_AGENT_PYTHON must point to a working Python 3.10 or newer.
    pause
    exit /b 1
)

call :check_python "%~dp0.venv\Scripts\python.exe"
if not errorlevel 1 goto :run
if defined CONDA_PREFIX (
    call :check_python "%CONDA_PREFIX%\python.exe"
    if not errorlevel 1 goto :run
)
where.exe py >nul 2>nul
if not errorlevel 1 (
    call :check_python py -3
    if not errorlevel 1 goto :run
)
where.exe python >nul 2>nul
if not errorlevel 1 (
    call :check_python python
    if not errorlevel 1 goto :run
)
call :check_python "%SystemDrive%\Anaconda\python.exe"
if not errorlevel 1 goto :run
call :check_python "%~d0\Anaconda\python.exe"
if not errorlevel 1 goto :run
echo Python 3.10+ was not found. Install Python or set SE_AGENT_PYTHON to python.exe.
pause
exit /b 1

:run
%PY_EXE% %PY_ARG% -m se_agent.workbench --port 8008
set "WORKBENCH_EXIT=%ERRORLEVEL%"
if not "%WORKBENCH_EXIT%"=="0" pause
exit /b %WORKBENCH_EXIT%

:check_python
%1 %2 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 exit /b 1
set "PY_EXE=%1"
set "PY_ARG=%2"
exit /b 0

:project_error
echo Project files not found. Run start-local.cmd from the extracted project folder.
pause
exit /b 1
