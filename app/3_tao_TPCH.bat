@echo off
chcp 65001 >nul
cd /d %~dp0
echo === Tao database TPC-H (SF 0.05) trong MySQL cho demo ===
set /p PW=Nhap mat khau MySQL root: 
python tpch_core.py --password "%PW%" --sf 0.05
echo.
echo Xong! Mo app bang 2_chay_demo.bat, vao tab 5 TPC-H.
pause
