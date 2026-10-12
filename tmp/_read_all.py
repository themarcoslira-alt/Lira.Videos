# -*- coding: utf-8 -*-
import io, re

def show(path, a, b, tag=''):
    print("@@@@ %s %s [%d-%d]" % (tag, path, a, b))
    lines = io.open(path, encoding='utf-8', errors='replace').read().splitlines()
    for i, ln in enumerate(lines[a-1:b], a):
        print("%5d| %s" % (i, ln))
    print()

def grep(path, pat, tag=''):
    print("@@@@ GREP %s %s /%s/" % (tag, path, pat))
    lines = io.open(path, encoding='utf-8', errors='replace').read().splitlines()
    rx = re.compile(pat)
    for i, ln in enumerate(lines, 1):
        if rx.search(ln):
            print("%5d| %s" % (i, ln.rstrip()))
    print()

# 1. FAM helpers + gravar_saldo_lido
show('services/flow_account_manager.py', 60, 77, 'FAM-HELPERS')
show('services/flow_account_manager.py', 299, 356, 'FAM-GRAVAR')

# 2. scene_plan_service API
grep('services/scene_plan_service.py', r'^def |^STATUS_|^IMAGE_STATUS_|^CAMPOS_EDITAVEIS|^SCENE_PLAN_', 'SPS')

# 3. api_v2 queue + marcar_hook
grep('services/api_v2.py', r'def v2_producao_iniciar_fila|def v2_marcar_hook_video|orcamentador|modo.*animacao|animacao', 'APIV2')
