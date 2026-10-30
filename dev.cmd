@rem dev.cmd
@rem Windows shortcut:  .\dev chd 192.168.1.0/24   is the same as   py -3.13 dev.py chd 192.168.1.0/24
@echo off
py -3.13 "%~dp0dev.py" %*
exit /b %ERRORLEVEL%
