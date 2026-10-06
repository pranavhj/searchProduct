@echo off
rem Price watch CLI: pricewatch run ^| list ^| add ^| remove ^| enable ^| disable ^| history
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%tools;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
"%ROOT%tools\shopping-deals-mcp-server\.venv\Scripts\python.exe" -m price_watch %*
