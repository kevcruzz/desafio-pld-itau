# Decisões, trade-offs e limitações

Este documento registra o que escolhi, **contra o quê**, e por quê. O que o código já
mostra sozinho está fora daqui.

Quase tudo abaixo veio de rodar e olhar o resultado, não de planejar antes. Onde a
execução me obrigou a mudar de ideia, deixei o caminho registrado — a decisão original,
o que a evidência mostrou, e o que ficou no lugar.

---

## 1. Tratamento de dados

### 1.1 Linhas duplicadas — remover, com uma condição

Os dois arquivos trazem duplicatas exatas: 1 no Nível 1 (`OP-0007`), 5 no Nível 2.
Verifiquei antes de tratar se algum ID repetido tinha conteúdo divergente. Nenhum tinha.

Isso decide o tratamento. Conteúdo idêntico é reenvio ou reprocessamento do sistema
legado e pode sair com segurança. Se houvesse divergência, seria conflito de integridade
— e aí eu não teria como escolher sozinho qual linha é a boa. Viraria exceção para
tratamento manual, não decisão de código. Deixei essa verificação explícita no
`diagnosticar()` justamente para que a condição fique visível, e não implícita num
`drop_duplicates()` solto.

**Por que não é higiene:** sem deduplicar, `CLI-A-3` passa a somar R$ 65.700 em quatro
operações no mesmo dia e **dispara a Regra 1 de fracionamento**. Com a duplicata
removida: três operações, R$ 48.500, não dispara. A falha do legado fabricava um alerta
de PLD do nada. Na base do Nível 2, a duplicata desloca a mediana e muda o status de
`CLI-028` na Regra 2.

### 1.2 Data ausente — manter a linha, excluir só das análises temporais

7 operações no Nível 2 (6 após dedup) e 1 no Nível 1 têm `data: null`, com observação do
próprio sistema: *"data nao capturada pelo sistema"*. O campo `valor` está preenchido e
consistente — é falha de captura de um atributo, não registro corrompido.

| Opção | Por que não |
|---|---|
| Descartar a linha | Remove volume real do cliente e tira operação do denominador da Regra 2. Em PLD, perder operação por falha de captura **subnotifica risco** — o mais caro dos dois erros. Medi: descartar muda o status de `CLI-029` na Regra 2 |
| Imputar data | Fabrica evidência. Se a data imputada colidir com outras operações, eu **crio** um alerta de fracionamento inexistente. Inventar cronologia em contexto regulatório é indefensável |
| **Manter, marcar `sem_data=True`** ✅ | A Regra 1 (temporal) ignora explicitamente; a Regra 2 e o volume continuam contando |

Num sistema real isso deveria disparar alerta de qualidade para o time de origem — o
dado está errado na fonte, não aqui.

### 1.3 Moeda estrangeira — converter, e a ordem importa

7 operações em USD no Nível 2, 1 no Nível 1, todas com observação *"remessa
internacional"* e canal TED. Converto pela taxa que vem dentro do arquivo (5,4). Todas
as regras e agregações usam `valor_brl`, nunca `valor`.

Sem converter, a Regra 2 **não sinaliza nada** no Nível 1 e perde 4 clientes no Nível 2
(`CLI-007`, `CLI-022`, `CLI-024`, `CLI-030`). A regra continuaria rodando sem erro,
retornando menos.

**A ordem `deduplicar → converter → datar` não é estética.** Se a mediana for calculada
antes da dedup, a linha repetida entra duas vezes na distribuição. Se a comparação com
limite acontecer antes da conversão, ela compara moedas diferentes. A seção 5 do notebook
do Nível 1 mostra a tabela de sensibilidade com cada cenário medido.

**Conclusão que atravessa o projeto:** a limpeza não é etapa preparatória da análise —
ela é parte do controle. Uma regra correta sobre dados sujos erra nas duas direções: cria
alarme onde não há risco e silencia risco onde há. Nenhuma das duas falhas aparece como
erro de execução.

### 1.4 Contrapartes — não consolidar por prefixo

O Nível 2 tem 111 contrapartes distintas seguindo o padrão `Nome + Setor + Sufixo`, com
casos como `Cristal Comercio LTDA` e `Cristal Comercio SA`, ou `Alfa Transportes LTDA` e
`Alfa Transportes ME`.

**Decidi não consolidar.** LTDA, ME e SA são naturezas jurídicas distintas — podem ser
empresas do mesmo grupo ou entidades sem relação nenhuma. Sem CNPJ não há como afirmar
identidade societária, e consolidar por semelhança de string criaria vínculo falso entre
pessoas jurídicas diferentes. Num alerta de PLD, afirmar que duas contrapartes são a
mesma sem base documental é pior que não afirmar nada.

Com mais tempo: resolver por documento, e tratar semelhança de nome como *sinal* para
verificação, nunca como conclusão.

### 1.5 O que verifiquei e estava limpo

`canal`, `tipo` e `moeda` não têm variação de grafia ou caixa. Todos os valores são
positivos. Todas as datas preenchidas estão em ISO válido. Nenhum `cliente_id`
malformado. Registro para deixar claro que a ausência de tratamento foi decisão, não
descuido — normalizar campo já normalizado adiciona código sem adicionar controle.

---

## 2. As regras

### 2.1 Interpretações de fronteira

O enunciado está em linguagem natural e cada verbo tem uma leitura. Adotei a literal:

| Trecho | Implementação | Por quê |
|---|---|---|
| "soma **ultrapassa** R\$ 50.000" | `soma > 50_000` | ultrapassar é exceder; exatamente 50.000 não ultrapassa |
| "nenhuma operação **atinge** R\$ 20.000" | `max < 20_000` | atingir é alcançar; exatamente 20.000 atinge, logo desqualifica |
| "**superior a** 5× a mediana" | `> 5 * mediana` | estrito |
| "clientes com 4 ou mais operações" | `count >= 4`, **após** a limpeza | uma duplicata podia empurrar um cliente de 3 para 4 e fazê-lo entrar na regra sem motivo |

A mediana da Regra 2 **inclui a própria operação testada**. É a leitura mais direta do
enunciado e a mais conservadora: o outlier puxa a mediana para cima, então a regra dispara
menos, não mais.

### 2.2 Volume = soma absoluta, não líquida

`tipo` distingue enviada, recebida, pagamento, depósito e saque. "Volume transacionado"
podia ser giro ou saldo. Escolhi **giro**: em PLD interessa quanto passou pela conta.
Entrar R\$ 100 mil e sair R\$ 100 mil é justamente o padrão que se quer enxergar — no
líquido, esse caso zeraria.

### 2.3 Limiares nomeados, não embutidos

`MIN_OPS_FRACIONAMENTO`, `LIMITE_SOMA_FRACIONAMENTO` e afins ficam no topo de
`pipeline.py`. Limiar de compliance é **política**, não constante de código: num sistema
real vem de configuração e muda sem deploy, porque quem decide o limiar é a área de
compliance, não a engenharia.

---

## 3. Arquitetura

### 3.1 Módulo compartilhado em `nivel_1/`, ao custo de manipular `sys.path`

`pipeline.py` mora em `nivel_1/` porque é lá que o enunciado define o tratamento. O Nível
2 importa e reutiliza: **o Nível 2 Parte A não exigiu uma linha nova de código** — só
trocar o caminho do arquivo. Escrevi como módulo desde o começo prevendo essa troca.

O preço é `sys.path.insert` em cada arquivo de `nivel_2/` que importa de fora da própria
pasta. A alternativa mais limpa seria um pacote na raiz, mas isso foge da estrutura de
pastas que o enunciado exige seguir exatamente. Escolhi respeitar a estrutura e pagar o
custo no import.

### 3.2 Cache de respostas em disco

`llm.py` guarda cada resposta indexada por hash de modelo + prompt. Comecei por economia
de quota e terminou sendo essencial: com o teto de tokens/dia da camada gratuita, sem
cache não dá para iterar. Ver seção 5.4.

### 3.3 Falha nunca interrompe o lote

Nenhuma função de `llm.py` ou `agente.py` levanta exceção para fora. Falhas viram status
registrado (`erro_api`, `falha_validacao`, `sem_conclusao`). Num lote de dez clientes, um
caso problemático não pode derrubar os outros nove. Isso se provou certo: na execução
final, 4 falhas não impediram os 6 pareceres bons.

---

## 4. Saída estruturada

### 4.1 `Literal` no nível de risco — rejeitar, não coagir

`nivel_risco: Literal["baixo","medio","alto"]`. Se o modelo responder `"altíssimo"`, a
validação **rejeita**. Vocabulário fechado é o que torna a saída utilizável a jusante:
sem isso, o confronto do Nível 2 viraria comparação frouxa de string e um quarto nível
inventado passaria despercebido por meses, sem nenhum erro de execução.

### 4.2 `red_flags` — coagir formato, rejeitar significado

Na primeira execução real, o `gpt-oss-120b` devolveu `red_flags` como texto separado por
ponto e vírgula em vez de array. **O retry realimentado não resolveu** — as duas
tentativas falharam com o mesmo erro. Retry corrige descuido; não corrige viés estrutural
do modelo.

A solução foi um `field_validator` que aceita string delimitada e devolve lista. A
coerção é deliberadamente estreita, e o critério vale explicar:

- `red_flags` como `"a; b; c"` é **diferença de formato** — conteúdo íntegro, separação
  inequívoca. Normalizar na fronteira é o que qualquer camada de integração faz.
- `nivel_risco` como `"altíssimo"` seria **diferença de significado** — aceitar
  inventaria um nível que o resto do sistema não sabe comparar.

Coerção onde a intenção é recuperável sem ambiguidade; rejeição onde aceitar corromperia
a análise a jusante.

### 4.3 O erro que meu próprio schema causou

**Esta é a decisão que mais me ensinou no desafio.**

Escrevi `red_flags` com `min_length=1` por reflexo — parecia razoável exigir ao menos um
indício. Na primeira execução do agente sobre `CLI-029`, o modelo investigou, concluiu
que a sinalização da regra não se sustentava, e devolveu lista vazia. **A validação
rejeitou.**

O schema tornava "investiguei e não encontrei nada" **inexprimível**. O modelo teria duas
saídas: fabricar uma red flag para conseguir responder, ou falhar. Nas duas, meu schema
estaria produzindo o falso positivo que a triagem existe para evitar.

**Restrição de schema não é neutra: ela define quais conclusões o sistema consegue
representar.** Um schema que não sabe dizer "sem indícios" é um schema que só sabe acusar.

Troquei por uma restrição com significado (`_coerencia`): risco **alto** exige ao menos
um indício citado, porque parecer de risco alto sem evidência não é auditável. Risco
baixo pode legitimamente não ter nada a apontar. A exigência passou a acompanhar a
conclusão em vez de valer para todas.

---

## 5. LLM e agente

### 5.1 Provedor e modelo — o do enunciado não existe mais

Comecei com `llama-3.3-70b-versatile`, sugerido no enunciado. A API devolveu **404**:
o modelo não está mais no catálogo do Groq. Listei os modelos que a minha chave alcança e
escolhi `openai/gpt-oss-120b` por capacidade de raciocínio, suporte a tool calling e
limites do free tier. Depois de esgotar a quota diária dele, migrei para
`openai/gpt-oss-20b` (ver 5.4) e **reexecutei o lote inteiro** no modelo novo — misturar
modelos dentro do mesmo lote invalidaria a comparação.

**Descoberta que mudou o dimensionamento:** os `gpt-oss` são modelos de raciocínio. Numa
chamada de teste que respondia uma única palavra, 38 dos 48 tokens de saída foram
`reasoning_tokens` — pensamento que não aparece no conteúdo mas **conta no orçamento**.
Com `max_tokens=1200` o raciocínio consumia a cota e o JSON saía truncado, parecendo bug
de parsing. Subi para 3000 e ajustei o intervalo entre chamadas a partir dessa medição,
não de estimativa.

### 5.2 A LLM calcula mesmo sob proibição explícita — quatro vezes

Este é o achado que sustenta o critério de separação regra/LLM, e não é o que eu esperava
encontrar.

| Onde | O que escreveu | Real |
|---|---|---|
| Prompt v1 (Nível 1) | "supera em mais de duas vezes o limite" | 64.800 ÷ 27.250 = 2,38× — correto, mas calculado |
| Prompt v2 (Nível 1) | "supera em mais de cinco vezes a mediana" | 64.800 ÷ 5.450 = **11,9×** — apresentou o limiar como magnitude |
| Agente, `CLI-014` | citou "limite de 11.542,05" | 2.308,41 × 5 — multiplicou |
| Agente, `CLI-029` | "os valores estão **acima** do limite típico de reporte" | todos entre R\$ 14 mil e R\$ 19,4 mil, **abaixo** do teto de R\$ 20 mil — inverteu o sentido |

O caso da v2 é o mais grave dos quatro por contexto: o system prompt dizia, em caixa
alta, `Nao recalcule, nao estime e nao infira nenhum numero que nao esteja no dossie`. O
modelo calculou assim mesmo. E o caso de `CLI-029` mostra o padrão completo: sempre que
lida com **comparação numérica contra limiar**, ele erra ou distorce, mesmo acertando a
interpretação qualitativa em volta.

**A conclusão não é "a LLM erra conta". É mais forte: instruir a LLM a não calcular não é
um controle confiável.** Prompt melhor corrigiu tipologia, eliminou red flag artificial e
impediu uma inferência de intenção que os dados não sustentavam — mas não impediu o
cálculo espontâneo. Se a corretude de um número importa, ele precisa ser calculado fora
do modelo e entregue pronto. É por isso que todo número deste projeto sai do pandas.

**Diagnóstico da causa:** o modelo calculou porque **precisava do dado e ele não estava
lá**. A proibição não elimina a necessidade. A correção certa não é proibir com mais
ênfase — é fechar a lacuna no insumo. Ver seção 7.

### 5.3 Por que o agente é agente, e não script

O enunciado avisa que chamar todas as ferramentas sempre não é um agente. Três decisões
produziram variação real de trajetória, sem `if/else` forçando rota:

1. **O contexto do caso informa qual regra sinalizou o cliente**, e nada mais. Não entrego
   o dossiê completo — se entregasse, o modelo não teria motivo para usar ferramenta
   nenhuma.
2. **As docstrings dizem quando cada ferramenta serve e o que ela NÃO responde.** Em tool
   calling a docstring é a interface, não a documentação. Descrição vaga produz agente que
   chama tudo por precaução.
3. **`emitir_parecer` é uma ferramenta**, o que dá ao modelo um verbo explícito para
   concluir (ver 5.5).

O resultado na execução final:

| Cliente | Sinalização | Trajetória |
|---|---|---|
| CLI-029, CLI-017 | fracionamento (evento datado) | `operacoes_do_dia` → `historico_cliente` |
| CLI-014 | 3 valores atípicos | `historico_cliente` → `operacoes_do_dia` ×3 (um por operação) |
| CLI-001 | valor atípico | `historico_cliente` → `perfil_canal` |
| CLI-005 | valor atípico | `historico_cliente` apenas |

Nenhuma trajetória idêntica ao padrão das outras; nenhum cliente com as três ferramentas
na mesma sequência. `CLI-014` investigou cada operação atípica separadamente — sequência
que ninguém programou.

### 5.4 O teto de tokens da camada gratuita reorganizou a execução

A cota gratuita do Groq limita por **tokens por dia** (200.000), em janela deslizante que
reabastece devagar: medi ~1.400 tokens liberados em vários minutos depois de esgotar. Um
lote completo custa ~50.000. Depois de estourar, o projeto ficou inviável por horas.

A saída foi trocar de modelo, já que **a cota é por modelo** — `gpt-oss-20b` tem 200K
próprios. Registro como contorno, não solução: num sistema real isso vira desenho de
capacidade (fila com backoff, pool de provedores, orçamento de tokens por execução).

E é o que transformou o cache de otimização em requisito.

### 5.5 Recuperar o parecer de dentro do erro

O `gpt-oss-120b` frequentemente emitia o parecer como chamada de uma ferramenta
inexistente chamada `"JSON"`. O Groq rejeita com 400 — **mas devolve o conteúdo gerado no
campo `failed_generation`**, e ali dentro está o parecer completo e bem formado.

Duas correções. A estrutural: criei `emitir_parecer` como ferramenta, dando ao modelo um
caminho explícito para encerrar. A de contingência: `_recuperar_de_erro()` extrai o
parecer de dentro da mensagem de erro, porque o modelo já fez a análise inteira e errou
só o envelope — descartar 5.000 tokens de trabalho válido por causa do envelope seria
desperdício. O texto recuperado passa pelo **mesmo validador**, então nada entra sem
verificação.

**Limitação honesta:** depender do formato de `failed_generation` é acoplamento ao Groq.
Se o provedor mudar a mensagem de erro, quebra. É dívida técnica consciente, não solução.

Efeito colateral interessante do `emitir_parecer`: o parecer passou a chegar como
**argumentos tipados de função**, com `enum` e `array` impostos pelo provedor antes de
chegarem em mim. JSON em texto livre é sugestão; parâmetro de função é contrato. O
`field_validator` continua no código como rede de segurança.

### 5.6 Lote não sobrescreve parecer válido

A primeira versão do `rodar_lote` gravava por cima sem olhar o que havia. Uma reexecução
que falhou por quota apagou cinco pareceres bons. Corrigi: se o registro em disco tem
status `ok` e o novo falhou, o antigo permanece. Numa mesa real, perder análise já feita
por falha de infraestrutura é perda de trabalho, não de arquivo.

---

## 6. O confronto

### 6.1 O critério do enunciado produz conjunto vazio nesta base

O enunciado sugere como exemplo que "cliente sinalizado pelas duas regras deveria sair
como risco alto". Testei: **nenhum dos 30 clientes dispara as duas regras.**

Não é acaso. As regras procuram fenômenos opostos: fracionamento busca *muitos valores
médios concentrados num dia*; valor atípico busca *um valor destoante do histórico*. Um
cliente que dispara uma tende a não disparar a outra. São detectores de coisas diferentes,
e as populações são disjuntas por construção.

Com o conjunto vazio, precisei de outro critério:

| Sinal das regras | Risco esperado |
|---|---|
| Regra 1 acionada | alto |
| Regra 2 com 2+ operações atípicas | alto |
| Regra 2 com 1 operação atípica | médio |
| Nenhuma | baixo |

A assimetria é deliberada: fracionamento é **padrão de conduta** (exige coordenação
deliberada); valor atípico isolado é **evento** (pode ser venda de bem, rescisão, aporte,
comércio exterior). Tratar os dois como equivalentes confunde conduta com acontecimento.

### 6.2 Resultado: 0% de concordância exata, e por que isso é informativo

Sobre os 6 clientes com parecer concluído:

- Concordância exata: **0/6 (0%)**
- Concordância adjacente: **2/6 (33%)**
- Agente mais brando: **6**
- Agente mais severo: **0**

**A unidirecionalidade é o dado, não a taxa.** Se o agente estivesse errando ao acaso,
haveria casos em que ele é mais severo que a regra. Não há nenhum. Viés sistemático numa
única direção é evidência sobre o **critério**, não sobre o agente — minha régua está
calibrada severa demais.

**Não recalibrei depois de ver o resultado.** Ajustar a régua para melhorar a métrica
seria sobreajuste ao próprio experimento, e a métrica deixaria de medir qualquer coisa.
Reporto 0% e explico o que 0% significa.

### 6.3 As divergências, caso a caso

**`CLI-029` — regra diz alto, agente diz baixo. O agente está certo.**

A Regra 1 sinalizou o dia 2026-05-26: 4 operações, R\$ 71.297,68, nenhuma atingindo
R\$ 20.000. O agente investigou o dia e o histórico, e encontrou: um **saque**, dois
**depósitos** e uma **transferência recebida**, para **quatro contrapartes distintas**,
numa conta com 15 contrapartes diferentes em 16 operações e R\$ 191 mil de giro.

Isso não é fracionamento. Fracionamento é dividir *um* valor em pedaços na *mesma
direção*, tipicamente para destinos relacionados. Aqui há dinheiro entrando e saindo no
mesmo dia, pulverizado. É um dia movimentado de uma conta ativa.

**Três defeitos de desenho da Regra 1 ficam expostos:** ela não olha direção do fluxo, não
olha relação entre contrapartes, e não olha o porte do cliente. Três operações de R\$ 18
mil são rotina para quem movimenta R\$ 191 mil e excepcionais para quem movimenta R\$ 20
mil — a regra trata os dois igual.

**`CLI-014` — regra diz alto (3 sinalizações), agente diz baixo.**

A mediana de `CLI-014` é R\$ 2.308,41, então o limite da Regra 2 fica em R\$ 11.542,05.
Três operações passaram disso. Mas em 11 operações somando R\$ 80.630, três valores entre
R\$ 13 mil e R\$ 23 mil não são três anomalias — são a cauda normal de uma distribuição
assimétrica.

**A Regra 2 conta ocorrências como se fossem independentes, quando são o mesmo fenômeno
estatístico.** Um cliente com mediana baixa e cauda longa dispara a regra várias vezes
pelo mesmo motivo, e meu critério de ranking amplifica isso ao somar as ocorrências —
`CLI-014` lidera o top 10 justamente por esse artefato.

**`CLI-001` e `CLI-005` — regra diz alto, agente diz médio.**

Os dois casos adjacentes. O agente reconheceu elemento que merece observação mas não
encontrou nada conclusivo. É a fronteira onde dois analistas humanos também divergiriam —
e é por isso que a concordância adjacente é mais informativa que a exata aqui.

---

## 7. Limitações — onde isto quebra com dados reais

**Câmbio fixo.** A taxa 5,4 vem do arquivo e vale para toda a série. Converter uma
operação de março com taxa de maio distorce valores e pode criar ou apagar sinalização.
Numa base real, câmbio é série temporal e a conversão precisa ser feita na data da
operação.

**Regra 1 é dia-calendário.** Fracionar entre 23h e 1h burla a regra trivialmente.
Precisaria de janela móvel.

**Regra 1 ignora porte, direção e relação entre contrapartes.** Ver 6.3.

**Regra 2 é frágil com poucas operações.** Com 4 pontos a mediana não descreve padrão. E
com mediana baixa, qualquer valor moderado estoura 5×.

**Identidade de contraparte por string não sobrevive a cadastro real.** Sem CNPJ não dá
para detectar operações entre partes relacionadas — que é uma das tipologias mais
relevantes de PLD e que este projeto simplesmente não vê.

**A LLM não é determinística.** Duas execuções do mesmo cliente produziram justificativas
diferentes, uma delas com o erro de limiar invertido descrito em 5.2. Em contexto
regulatório isso exige `temperature=0`, versionamento de prompt e trilha de auditoria da
trajetória — que eu registro, mas não versiono.

**Não há *ground truth*.** Não sei se os alertas são verdadeiros; sei se são consistentes
com a regra. Toda a análise de divergências é argumentativa, não validada.

**Taxa de conclusão do agente: 6 de 10.** Duas falhas de validação e duas de quota. Um
sistema de produção precisaria de reprocessamento automático de fila.

**Acoplamento ao formato de erro do Groq** na recuperação (5.5).

---

## 8. O que eu faria com mais tempo

### 8.1 Fechar a lacuna que faz o modelo calcular

**A correção mais importante da lista**, porque ataca a causa e não o sintoma.

O modelo calcula porque precisa da razão `valor / mediana` e ela não está no insumo.
Incluir em `dossie_cliente()` e no retorno das ferramentas os valores **já comparados**:
`razao_sobre_mediana`, `limite_valor_atipico_brl`, `todas_abaixo_do_teto_20k`.

**Como eu validaria:** rodaria os 10 clientes antes e depois, e contaria quantos pareceres
contêm número ausente do insumo. A hipótese é que cai a zero — e se não cair, a hipótese
estava errada e o problema é outro.

### 8.2 Regras conscientes do perfil do cliente

Substituir limiar absoluto por relativo ao histórico: fracionamento passaria a exigir
desvio do padrão do próprio cliente, não só soma acima de R\$ 50 mil. E adicionar as três
dimensões que faltam: direção do fluxo, relação entre contrapartes, janela móvel de 3 dias.

**Como validaria:** as regras novas deveriam continuar capturando `CLI-A-1` (fracionamento
clássico) e deixar de capturar `CLI-029` (o falso positivo que o agente identificou). Sem
*ground truth*, esses dois casos funcionam como teste de regressão.

### 8.3 Deduplicar sinalizações da Regra 2

Contar *clientes com cauda anômala* em vez de *operações acima do limite*, ou exigir
dispersão mínima. Isso corrigiria o artefato que colocou `CLI-014` no topo do ranking.

### 8.4 Nível 3 — Trilha A (multiagente)

**Não implementei.** O que faria: encadear Triador (decide se o caso segue), Investigador
(usa as ferramentas do Nível 2) e Redator (produz o parecer), com estado compartilhado num
dataclass e condição de parada no Triador — caso arquivado não avança.

Escolheria a Trilha A por dois motivos: reaproveita `tools.py` sem alteração, e o Triador
resolve o problema de custo que medi — hoje todo cliente recebe investigação completa,
mesmo os que o próprio agente classifica como baixo em duas ferramentas.

**Como validaria:** comparando custo total e concordância contra o agente único. Se o
multiagente gastar mais tokens para chegar nos mesmos pareceres, a arquitetura não se
justifica — e eu diria isso.

### 8.5 Robustez operacional

Fila com backoff e retomada, orçamento de tokens por execução, `temperature=0` com prompt
versionado, e `outputs/` com timestamp em vez de sobrescrita.

---

## 9. Resumo das mudanças de rota

Registro para deixar visível que o projeto foi corrigido contra evidência, não planejado
de uma vez:

| Decisão original | O que a execução mostrou | O que ficou |
|---|---|---|
| `llama-3.3-70b` (do enunciado) | 404, modelo fora do catálogo | `gpt-oss-120b`, depois `20b` |
| `max_tokens=1200` | 79% dos tokens de saída eram raciocínio invisível | 3000, dimensionado por medição |
| `red_flags` com `min_length=1` | tornava "sem indícios" inexprimível | restrição condicionada ao risco alto |
| Retry para corrigir formato | falhou 2/2 com o mesmo erro | normalização no schema |
| Conclusão em texto livre | modelo emitia ferramenta inexistente | `emitir_parecer` como ferramenta |
| Lote sobrescreve sempre | apagou 5 pareceres válidos | preserva o melhor status |
| Critério do enunciado no confronto | conjunto vazio | critério graduado próprio |
