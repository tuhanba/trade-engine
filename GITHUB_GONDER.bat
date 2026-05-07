@echo off
echo ===================================================
echo AURVEX AI - GITHUB'A GONDERILIYOR...
echo ===================================================

cd /d "%~dp0"

echo [1/3] Eski dosyalar kaldiriliyor...
git rm aurvex-bot.service aurvex-dashboard.service aurvex-watchdog.service 2>nul

echo.
echo [2/3] Degisiklikler Git'e ekleniyor...
git add scalp_bot_v3.py app.py config.py database.py execution_engine.py
git add ax-bot.service ax-dashboard.service README.md SETUP.md deprecated/
git commit -m "Restore damaged files and remove obsolete aurvex services"

echo.
echo [3/3] GitHub'a gonderiliyor...
git push origin main

echo.
echo ===================================================
echo ISLEM TAMAMLANDI!
echo ===================================================
pause
