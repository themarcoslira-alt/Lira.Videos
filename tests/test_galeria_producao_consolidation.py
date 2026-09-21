import re

def test_consolidation():
    with open('static/index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    assert 'id="s2-tab-arquivos"' not in html, 's2-tab-arquivos found in index.html'
    assert 'data-s2-tab="arquivos"' not in html, 'data-s2-tab=arquivos found in index.html'
    assert 'id="s2-tab-producao"' in html, 's2-tab-producao not found'
    assert '4. MONTAGEM' in html, '4. MONTAGEM not found in navbar'

    # Verify that Galeria de Mídias Geradas was completely removed from index.html & producao
    assert 'id="s2-arquivo-grid"' not in html, 's2-arquivo-grid still found in index.html'
    assert 'id="s2-arquivo-filtros"' not in html, 's2-arquivo-filtros still found in index.html'
    assert 'id="s2-arquivo-contador"' not in html, 's2-arquivo-contador still found in index.html'
    assert 'GALERIA DE MÍDIAS GERADAS' not in html, 'GALERIA DE MÍDIAS GERADAS still found in index.html'
    assert 'btn-s2-abrir-pasta-explorer' not in html, 'btn-s2-abrir-pasta-explorer still found in index.html'
    assert 'btn-s2-baixar-zip-arquivos' not in html, 'btn-s2-baixar-zip-arquivos still found in index.html'

    # Verify that Production grid remains intact
    assert 'id="s2-producao-grid"' in html, 's2-producao-grid not found in index.html'
    assert 'id="s2-fila-hud"' in html, 's2-fila-hud not found in index.html'

    # Verify Montagem tab remains intact
    assert 'id="s2-tab-montagem"' in html, 'Montagem tab removed or broken'
    assert 'montagem-v2-grid' in html, 'Montagem grid removed or broken'

    with open('static/app.js', 'r', encoding='utf-8') as f:
        js = f.read()

    assert 's2-tab-arquivos' not in js, 's2-tab-arquivos found in app.js'
    assert 'trocarAbaStudio2("arquivos")' not in js, 'trocarAbaStudio2(arquivos) found in app.js'
    assert 'renderGaleriaArquivosS2' not in js, 'renderGaleriaArquivosS2 still found in app.js'
    assert 'removerMidiaCenaGaleria' not in js, 'removerMidiaCenaGaleria still found in app.js'
    assert 'atualizarArquivosS2' not in js, 'atualizarArquivosS2 still found in app.js'

    with open('static/style.css', 'r', encoding='utf-8') as f:
        css = f.read()

    assert '#s2-tab-arquivos' not in css, 's2-tab-arquivos found in style.css'

    print("All validation assertions passed successfully!")

if __name__ == '__main__':
    test_consolidation()
