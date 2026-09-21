import re

def test_consolidation():
    with open('static/index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    assert 'id="s2-tab-arquivos"' not in html, 's2-tab-arquivos found in index.html'
    assert 'data-s2-tab="arquivos"' not in html, 'data-s2-tab=arquivos found in index.html'
    assert 'id="s2-tab-producao"' in html, 's2-tab-producao not found'
    assert 'id="s2-arquivo-grid"' in html, 's2-arquivo-grid not found'
    assert 'id="s2-arquivo-filtros"' in html, 's2-arquivo-filtros not found'
    assert '4. MONTAGEM' in html, '4. MONTAGEM not found in navbar'

    # Verify that #s2-arquivo-grid is inside #s2-tab-producao
    prod_match = re.search(r'<div id="s2-tab-producao"[^>]*>(.*?)</div>\s*<!-- ABA \d+:', html, re.DOTALL)
    assert prod_match, 'Could not locate #s2-tab-producao container boundary'
    prod_content = prod_match.group(1)
    assert 'id="s2-arquivo-grid"' in prod_content, '#s2-arquivo-grid is not inside #s2-tab-producao'
    assert 'id="s2-arquivo-filtros"' in prod_content, '#s2-arquivo-filtros is not inside #s2-tab-producao'
    assert 'id="s2-arquivo-contador"' in prod_content, '#s2-arquivo-contador is not inside #s2-tab-producao'
    assert 'id="btn-s2-abrir-pasta-explorer"' in prod_content, 'Explorer button not found in producao'
    assert 'id="btn-s2-baixar-zip-arquivos"' in prod_content, 'ZIP button not found in producao'

    # Verify Montagem tab remains intact
    assert 'id="s2-tab-montagem"' in html, 'Montagem tab removed or broken'
    assert 'montagem-v2-grid' in html, 'Montagem grid removed or broken'

    with open('static/app.js', 'r', encoding='utf-8') as f:
        js = f.read()

    assert 's2-tab-arquivos' not in js, 's2-tab-arquivos found in app.js'
    assert 'trocarAbaStudio2("arquivos")' not in js, 'trocarAbaStudio2(arquivos) found in app.js'
    assert 'renderGaleriaArquivosS2' in js, 'renderGaleriaArquivosS2 not found in app.js'
    assert 'removerMidiaCenaGaleria' in js, 'removerMidiaCenaGaleria not found in app.js'

    with open('static/style.css', 'r', encoding='utf-8') as f:
        css = f.read()

    assert '#s2-tab-arquivos' not in css, 's2-tab-arquivos found in style.css'

    print("All validation assertions passed successfully!")

if __name__ == '__main__':
    test_consolidation()
