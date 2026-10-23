@echo off
chcp 65001 >nul
cd /d %~dp0
REM (Tuy chon) dien san API key de khoi nhap tren giao dien:
REM set ANTHROPIC_API_KEY=sk-ant-...
REM set GEMINI_API_KEY=AIza...
python -m streamlit run app.py --server.showEmailPrompt false --browser.gatherUsageStats false
pause
