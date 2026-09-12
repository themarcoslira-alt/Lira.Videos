@echo off
cd /d %~dp0
%~dp0\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'%~dp0'); from services.broll_director import gerar_storyboard; import json; r=gerar_storyboard('2026',usar_claude=True); open('%~dp0/teste4_output.json','w',encoding='utf-8').write(json.dumps(r,indent=2,ensure_ascii=False)); print('OK')" > %~dp0\teste4_stdout.txt 2> %~dp0\teste4_stderr.txt