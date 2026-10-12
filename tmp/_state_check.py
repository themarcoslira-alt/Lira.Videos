# -*- coding: utf-8 -*-
import io, os, json, re

def exists(p):
    return os.path.exists(p)

def nlines(p):
    try:
        return len(io.open(p, encoding='utf-8', errors='replace').read().splitlines())
    except Exception as e:
        return 'ERR:%s' % e

print("=== STATE ===")
for p in ['services/flow_account_manager.py', 'services/orcamento_producao_service.py',
          'tests/test_orcamento_producao.py', 'config/flow_custos.json',
          'config/flow_accounts.json', 'logs/flow_custos.jsonl',
          'services/playwright_flow.py', 'services/api_v2.py', 'static/app.js',
          'static/index.html']:
    print("%-55s exists=%-5s lines=%s" % (p, exists(p), nlines(p) if exists(p) else '-'))

print()
print("=== flow_custos.json ===")
if exists('config/flow_custos.json'):
    print(io.open('config/flow_custos.json', encoding='utf-8').read())

print()
print("=== flow_account_manager.py: defs ===")
s = io.open('services/flow_account_manager.py', encoding='utf-8').read()
for i, ln in enumerate(s.splitlines(), 1):
    if re.match(r'^(def |class |    def )', ln):
        print("%5d %s" % (i, ln.rstrip()))

print()
print("=== flow_account_manager.py: signature block (first 60 lines) ===")
for i, ln in enumerate(s.splitlines()[:60], 1):
    print("%4d %s" % (i, ln))

print()
print("=== flow_account_manager.py: lines 78-140 ===")
for i, ln in enumerate(s.splitlines()[77:140], 78):
    print("%4d %s" % (i, ln))
