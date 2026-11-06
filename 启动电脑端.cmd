@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LC_EXE=%~dp0发布\LC互传\LC互传.exe"
if not exist "%LC_EXE%" goto missing
if not exist "%~dp0发布\LC互传\_internal\python313.dll" goto missing
"%LC_EXE%" %*
set "LC_RESULT=%ERRORLEVEL%"
if "%LC_RESULT%"=="0" exit /b 0
echo.
echo 客户端启动失败，错误码：%LC_RESULT%
echo 请保留整个 发布\LC互传 文件夹，不能只复制其中的 EXE。
echo 日志目录：%LOCALAPPDATA%\LCTransfer
pause
exit /b %LC_RESULT%
:missing
echo 找不到完整的客户端。请解压 Windows 便携版，并保留 EXE 旁边的 _internal 文件夹。
pause
exit /b 1
