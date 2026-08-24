"""
Corrige a renderizacao das celulas de markdown do notebook.

Problema: o Jupyter usa MathJax, que trata $...$ como formula inline.
Cada par de "R$" no texto abria e fechava uma regiao de LaTeX, e todo o
conteudo entre eles era renderizado como equacao — engolindo texto,
numeros e ate separadores de tabela.

Correcao: escapar como "R\\$" nas celulas de MARKDOWN apenas. As
celulas de CODIGO nao sao tocadas: la o "R$" esta dentro de strings
Python que vao no prompt da LLM, e escapar corromperia o texto enviado.

Rode uma vez, da raiz do projeto:
    python nivel_1\\corrigir_cifrao.py
"""

from pathlib import Path

import nbformat as nbf

CAMINHO = Path(__file__).resolve().parent / "nivel_1.ipynb"

nb = nbf.read(CAMINHO, as_version=4)

alterados = 0
ocorrencias = 0

for celula in nb.cells:
    if celula.cell_type != "markdown":
        continue
    if "R$" not in celula.source:
        continue
    # Nao re-escapa o que ja esta escapado
    novo = celula.source.replace("R\\$", "\x00").replace("R$", "R\\$").replace("\x00", "R\\$")
    if novo != celula.source:
        ocorrencias += celula.source.count("R$")
        celula.source = novo
        alterados += 1

nbf.write(nb, CAMINHO)
print(f"{ocorrencias} cifroes escapados em {alterados} celulas de markdown.")
print("Celulas de codigo preservadas.")
