# Uso de IA

## Ferramentas

**Claude (Anthropic)** — usei ao longo de todo o desafio, como par de trabalho.

## Para quê

**Exploração inicial dos dados.** Pedi ajuda para mapear os problemas de qualidade
plantados nos dois arquivos antes de escrever o pipeline. Isso me poupou as horas mais
mecânicas do trabalho e me deixou focar nas decisões de tratamento, que são o que o
enunciado avalia.

**Estrutura do código.** `pipeline.py`, `tools.py`, `agente.py`, `confronto.py` e a
camada `llm.py` foram escritos em conjunto — eu descrevendo o que precisava e revisando,
Claude propondo implementação. Rodei tudo, testei contra a base, e em vários pontos o
resultado da execução me obrigou a mudar o que estava escrito.

**Redação técnica.** Células de markdown do notebook e este conjunto de documentos.

**Depuração.** Ambiente Windows, Python 3.14, SDK do Groq, erros da API.

## Onde a IA me levou para o caminho errado

Registro os casos em que segui uma sugestão e a execução mostrou que estava errada. Todos
foram corrigidos, e a maioria virou trade-off documentado no `DECISOES.md`.

**O `min_length=1` no `red_flags`.** Foi sugerido — e eu aceitei — exigir ao menos um
indício no schema do parecer. Parecia razoável. Só descobri o problema quando rodei o
agente sobre `CLI-029`: ele investigou, concluiu que a sinalização da regra não se
sustentava, devolveu lista vazia, e a validação rejeitou. **O schema tornava "não
encontrei nada" inexprimível e forçaria o modelo a inventar suspeita para conseguir
responder.** Um schema que só sabe acusar. Trocamos por uma restrição condicionada ao
risco alto. É o erro que mais me ensinou no desafio, e ele não apareceria em revisão de
código — só rodando.

**A instrução que piorou o prompt.** O agente falhava em metade dos casos emitindo o
parecer como chamada de uma ferramenta inexistente. A sugestão foi acrescentar ao prompt
"não invente outra ferramenta para entregá-lo". O resultado foi **10 falhas em 10** contra
5 antes — mencionar a possibilidade a tornou saliente. A correção que funcionou foi
estrutural, não textual: criar `emitir_parecer` como ferramenta de verdade, dando ao
modelo um caminho explícito para concluir.

**O `requirements.txt` com versões que não instalam.** As versões fixadas inicialmente
(`pandas==2.2.3`) não têm wheel para Python 3.14, que é o que tenho na máquina. O pip
tentaria compilar do código-fonte e falharia. Verifiquei a compatibilidade real antes de
rodar e troquei por faixas.

**Cifrões quebrando o markdown do notebook.** As células de texto usavam `R$` livremente.
O Jupyter renderiza `$...$` como fórmula MathJax, então cada par de `R$` abria uma região
de LaTeX que engolia o texto entre eles — inclusive separadores de tabela. O arquivo
estava correto; a renderização é que destruía. Só apareceu quando eu **li** o notebook
renderizado, não quando revisei o código.

**Um comando de verificação errado que me fez perder tempo.** O regex sugerido para
conferir se o problema acima tinha sido corrigido estava errado e reportava 34 problemas
onde havia zero. Passei alguns minutos investigando um arquivo que já estava certo.

## O que não delegei

As decisões que o enunciado avalia. O que fazer com as linhas sem data, como interpretar
"ultrapassa" e "atinge", se consolidar contrapartes por prefixo, qual critério usar no
confronto depois de descobrir que o do enunciado dava conjunto vazio, e se recalibrar a
régua depois de ver 0% de concordância — decidi não recalibrar, porque ajustar o critério
depois do resultado seria sobreajuste.

Também não delegei a leitura dos resultados. Os quatro casos em que a LLM calculou sob
proibição explícita, o padrão de divergência unidirecional no confronto e o defeito de
desenho da Regra 1 exposto por `CLI-029` foram vistos olhando saída de execução, não
lendo documentação.

## Observação

A ironia não passou despercebida: metade dos achados deste projeto são sobre **como
modelos de linguagem falham** — calculam quando proibidos, inventam ferramentas, achatam
arrays em strings, invertem o sentido de limiares. Usei um para construir o projeto e
passei o projeto inteiro medindo os limites de outro. As duas coisas se reforçaram: eu
não teria desconfiado tanto do `gpt-oss` se não estivesse revisando código gerado por IA
o tempo todo.
