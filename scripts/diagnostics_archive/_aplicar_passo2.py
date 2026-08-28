import sys
sys.path.insert(0, "C:/ultracut3")

lines = open("C:/ultracut3/gui.py", encoding="utf-8").readlines()

# Encontrar o final de _selecionar_projeto_lista
# A função termina com a abertura de _transcrever_novamente
# Vamos encontrar o ultimo bind de Retomar Pipeline e inserir depois

# Procurar o ultimo retomar_pipeline ou transcrever_novamente
insert_at = None
for i, l in enumerate(lines):
    if "Retomar Pipeline" in l and "command=lambda:" in l:
        # Pega a linha do botao "Transcrever Novamente" logo depois
        pass
    
# Mais simples: procurar o render_ok que é o ultimo widget do frame
for i in range(len(lines)):
    if 'render_ok = steps.get("renderizar", {}).get("status")' in lines[i]:
        # Linha do if render_ok e o label depois
        pass

# Vou inserir DEPOIS da linha que fecha _selecionar_projeto_lista
# Procurar a linha 1346 que tem o if transcricao_ok fechando
print("Procurando final de _selecionar_projeto_lista...")
for i in range(1280, 1400):
    if i < len(lines):
        if "def _transcrever_novamente" in lines[i]:
            print(f"Encontrado _transcrever_novamente na linha {i+1}")
            print(f"Linha anterior ({i}): {lines[i-1]}")
            break