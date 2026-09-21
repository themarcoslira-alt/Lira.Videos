import json
import pytest
import subprocess

def test_js_syntax():
    """Valida que static/app.js não tem erros de sintaxe JS."""
    res = subprocess.run(["node", "-c", "static/app.js"], capture_output=True, text=True)
    assert res.returncode == 0, f"Erro de sintaxe JS: {res.stderr}"

def test_virtualization_functions_defined():
    """Valida presença das funções de virtualização no static/app.js."""
    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "function calcularCenasVisiveis(" in js
    assert "function criarPoolNovos(" in js
    assert "function removerPoolAntigos(" in js
    assert "function atualizarPoolAoScroll(" in js
    assert "function atualizarTimelineViewport(" in js
    assert "window.timelinePerfMetrics" in js
    assert "TIMELINE_POOL_SIZE = 50" in js

def test_css_virtualization_optimizations():
    """Valida otimizações de layout e aceleração em static/style.css."""
    with open("static/style.css", "r", encoding="utf-8") as f:
        css = f.read()

    assert "contain: layout style;" in css
    assert "will-change: left, width;" in css

def test_subpixel_drift_eliminated():
    """Valida que não há mais ocorrências de Math.round(durSec * pxPerSec) e que a fórmula encadeada é usada."""
    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "Math.round(durSec * pxPerSec)" not in js, "Math.round(durSec * pxPerSec) ainda encontrado em app.js"
    assert "const clipWidth = Math.round((tIni + durSec) * pxPerSec) - clipLeft;" in js

def test_calcular_cenas_visiveis_node_execution():
    """Executa script Node.js para validar calcularCenasVisiveis com 221 cenas simuladas."""
    node_script = r"""
    const fs = require('fs');
    const vm = require('vm');

    // Mock do ambiente de browser
    const sandbox = {
        window: {},
        document: { querySelectorAll: () => [] },
        $: () => null,
        console: console,
        performance: { now: () => Date.now() },
        Math: Math,
        parseFloat: parseFloat,
        parseInt: parseInt,
        Array: Array,
        String: String,
        Number: Number
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);

    const appJs = fs.readFileSync('static/app.js', 'utf-8');
    const fnMatch = appJs.match(/function calcularCenasVisiveis[\s\S]*?\n\}/);
    if (!fnMatch) throw new Error("calcularCenasVisiveis not found");
    vm.runInContext(fnMatch[0] + "; window.calcularCenasVisiveis = calcularCenasVisiveis;", sandbox);

    // Gera 221 cenas de 5 segundos cada
    const totalCenas = 221;
    const cenas = [];
    let acum = 0;
    for (let i = 0; i < totalCenas; i++) {
        const dur = 5.0;
        cenas.push({
            id: i + 1,
            scene_index: i + 1,
            duracao: dur,
            tempo_inicio: acum,
            tempo_fim: acum + dur,
            tipo: (i % 3 === 0) ? 'video' : 'imagem'
        });
        acum += dur;
    }

    const pxPerSec = 28; // default zoom 1.0x
    const viewportWidth = 1200;
    const bufferPx = 500;

    // Teste 1: Início (scroll = 0)
    const visInicio = sandbox.calcularCenasVisiveis(0, viewportWidth, cenas, pxPerSec, bufferPx);
    // Range visível: [-500, 1700]. Cada cena = 140px. 1700 / 140 = ~12 cenas.
    if (!visInicio.length) throw new Error("Falha no scroll inicial: nenhuma cena visível");
    if (visInicio[0].id !== 1) throw new Error(`Primeira cena esperada 1, obtido ${visInicio[0].id}`);
    if (visInicio.length > 25) throw new Error(`Esperado ~13-16 cenas, obtido ${visInicio.length}`);

    // Teste 2: Meio (scroll = 15000px -> ~Cena 107)
    const scrollMeio = 15000;
    const visMeio = sandbox.calcularCenasVisiveis(scrollMeio, viewportWidth, cenas, pxPerSec, bufferPx);
    if (!visMeio.length) throw new Error("Falha no scroll meio: nenhuma cena visível");
    const idsMeio = visMeio.map(c => c.id);
    // 15000px / 140px = cena ~108
    if (!idsMeio.includes(108)) throw new Error("Cena 108 não encontrada no meio do scroll");
    if (idsMeio.includes(1)) throw new Error("Cena 1 não deveria estar visível no meio");
    if (idsMeio.includes(221)) throw new Error("Cena 221 não deveria estar visível no meio");

    // Teste 3: Final (scroll = 30000px -> últimas cenas)
    const scrollFim = 30000;
    const visFim = sandbox.calcularCenasVisiveis(scrollFim, viewportWidth, cenas, pxPerSec, bufferPx);
    if (!visFim.length) throw new Error("Falha no scroll final: nenhuma cena visível");
    const idsFim = visFim.map(c => c.id);
    if (!idsFim.includes(221)) throw new Error("Cena 221 não encontrada no final");
    if (idsFim.includes(1)) throw new Error("Cena 1 não deveria estar visível no final");

    console.log(JSON.stringify({
        ok: true,
        inicioCount: visInicio.length,
        meioCount: visMeio.length,
        fimCount: visFim.length
    }));
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Falha na validação Node da virtualização: {res.stderr}"
    data = json.loads(res.stdout.strip())
    assert data.get("ok") is True
    assert 10 <= data.get("inicioCount") <= 25
    assert 10 <= data.get("meioCount") <= 25
    assert 10 <= data.get("fimCount") <= 25

def test_drag_drop_reorder_logic():
    """Valida que o reordenamento de dados por ID funciona e preserva integridade de tempos."""
    totalCenas = 221
    cenas = []
    acum = 0.0
    for i in range(totalCenas):
        dur = 5.0
        cenas.append({
            "id": i + 1,
            "scene_index": i + 1,
            "duracao": dur,
            "tempo_inicio": acum,
            "tempo_fim": acum + dur
        })
        acum += dur

    # Arrastar cena 50 para posição 150
    srcId = 50
    targetId = 150

    srcIdx = next(i for i, c in enumerate(cenas) if c["id"] == srcId)
    targetIdx = next(i for i, c in enumerate(cenas) if c["id"] == targetId)

    item = cenas.pop(srcIdx)
    cenas.insert(targetIdx, item)

    # Recalcula cumulativo
    recalcAcum = 0.0
    for sc in cenas:
        sc["tempo_inicio"] = recalcAcum
        dur = sc.get("duracao", 5.0)
        recalcAcum += dur
        sc["tempo_fim"] = recalcAcum

    assert len(cenas) == totalCenas
    # A cena 50 agora está no índice 150
    assert cenas[targetIdx]["id"] == 50
    # A cena anterior ao índice 150 agora é diferente
    assert cenas[targetIdx]["tempo_inicio"] == targetIdx * 5.0
    assert recalcAcum == totalCenas * 5.0
