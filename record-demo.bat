@echo off
REM Screen recorder for the TikTok app review demo.
REM Captures the whole screen to review\demo.mp4, sized to stay under the
REM 50 MB per file limit for roughly ten minutes of footage.
REM Press q in this window to stop. Closing the window leaves a broken file.

cd /d "%~dp0"
if not exist review mkdir review

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ffmpeg is not on PATH. Install it, or edit this file to give the full path.
  pause
  exit /b 1
)

if exist review\demo.mp4 (
  echo review\demo.mp4 already exists. Rename or delete it first.
  pause
  exit /b 1
)

echo.
echo Recording the whole screen. Press q here to stop.
echo.

ffmpeg -hide_banner -loglevel warning -stats ^
  -f gdigrab -framerate 12 -i desktop ^
  -vf "scale=1280:-2" ^
  -c:v libx264 -preset veryfast -crf 30 -maxrate 1200k -bufsize 2400k ^
  -pix_fmt yuv420p -movflags +faststart ^
  review\demo.mp4

echo.
if exist review\demo.mp4 (
  for %%A in (review\demo.mp4) do echo Saved review\demo.mp4  ^(%%~zA bytes^)
  echo Upload that file on the TikTok submission form.
)
pause
