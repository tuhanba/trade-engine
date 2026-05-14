@echo off
cd /d "c:\Users\pc\Desktop\AURVEX Ai"

echo ========================================
echo   AURVEX - GitHub Pull and Push
echo ========================================
echo.

echo [1/5] Git pull yapiliyor (remote degisiklikler alinıyor)...
git pull origin main
if errorlevel 1 (
    echo  main branch bulunamadi, master deneniyor...
    git pull origin master
)
echo.

echo [2/5] Tum dosyalar stage ediliyor...
git add -A
echo.

echo [3/5] Degisen dosyalar:
git status --short
echo.

echo [4/5] Commit yapiliyor...
git commit -m "feat: Production upgrade v5.0 - SSE real-time dashboard, multi-TP trailing engine, ghost learning AI, backtest v6 with slippage simulation"
echo.

echo [5/5] GitHub'a push yapiliyor...
git push origin main
if errorlevel 1 (
    echo  main basarisiz, master deneniyor...
    git push origin master
)

echo.
echo ========================================
echo   TAMAMLANDI
echo ========================================
echo.
pause
