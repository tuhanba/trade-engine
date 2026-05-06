@echo off
echo ===================================================
echo AURVEX AI - Gereksiz Dosyalari Temizleme ve GitHub Push
echo ===================================================

cd /d "%~dp0"

echo.
echo [1/3] Gecici ve cop dosyalar siliniyor...
del /F /Q _git_push*.py 2>nul
del /F /Q _delete_old_files.py 2>nul
del /F /Q $null 2>nul
del /F /Q aurvex-bot.logrotate 2>nul
del /F /Q setup_all.sh 2>nul
del /F /Q FINAL_*.md 2>nul
del /F /Q PERFORMANS_RAPORU.md 2>nul
del /F /Q deploy_to_server.py 2>nul
del /F /Q bot_v3.log 2>nul
del /F /Q dashboard.log 2>nul

echo.
echo [2/3] Degisiklikler Git'e ekleniyor...
git add -A
git commit -m "chore: Cleaned up temporary AI helper scripts and junk logs"

echo.
echo [3/3] GitHub'a gonderiliyor...
git push origin main

echo.
echo ===================================================
echo ISLEM TAMAMLANDI! (Eger hata alirsaniz lutfen kontrol ediniz)
echo ===================================================
pause
