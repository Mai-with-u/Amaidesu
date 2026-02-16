@echo off
REM Gource 可视化脚本 - 从 scripts 目录运行
cd /d "%~dp0.."
gource --start-date "2025-03-01" --user-image-dir .git/avatars/ -1920x1080 --title "Amaidesu History" -o - | ffmpeg -y -r 60 -f image2pipe -vcodec ppm -i - -vcodec libx264 -preset veryfast -pix_fmt yuv420p -crf 18 -threads 0 -bf 0 amaidesu.mp4