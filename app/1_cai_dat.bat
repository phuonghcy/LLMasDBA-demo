@echo off
chcp 65001 >nul
cd /d %~dp0
echo === Cai thu vien Python cho demo (chi can chay 1 lan) ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Xong! Bay gio chay file 2_chay_demo.bat
pause
