# -*- coding: utf-8 -*-
import io, re

def show(path, a, b, tag=''):
    print("=== %s %s [%d-%d] ===" % (tag, path, a, b))
    lines = io.open(path, encoding='utf-8', errors='replace').read().splitlines()
    for i, ln in enumerate(lines[a-1:b], a):
        print("%5d| %s" % (i, ln))
    print()

show('services/flow_account_manager.py', 170, 260, 'FAM')
show('services/flow_account_manager.py', 299, 410, 'FAM')
