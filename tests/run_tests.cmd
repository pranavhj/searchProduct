@echo off
rem Run price_watch unit tests with the shopping-deals venv.
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%\tools"
"%ROOT%\tools\shopping-deals-mcp-server\.venv\Scripts\python.exe" -m pytest "%ROOT%\tests" -q -p no:cacheprovider %*
