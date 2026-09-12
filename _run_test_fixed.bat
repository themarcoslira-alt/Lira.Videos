@echo off
cd /d %~dp0
echo %DATE% %TIME% - INICIO > teste_timestamps.txt
%~dp0\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'%~dp0'); from services.broll_director import gerar_storyboard; import json; r = gerar_storyboard('2026', usar_claude=True); open('%~dp0/teste_output.json','w',encoding='utf-8').write(json.dumps(r, indent=2, ensure_ascii=False))" > teste_stdout.txt 2> teste_stderr.txt
echo %DATE% %TIME% - FIM >> teste_timestamps.txt