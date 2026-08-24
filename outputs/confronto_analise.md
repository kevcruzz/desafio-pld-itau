# Confronto — regras deterministicas x parecer do agente

Casos comparados: **6**

## Criterio de correspondencia

| Sinal das regras | Risco esperado |
|---|---|
| Regra 1 (fracionamento) acionada | alto |
| Regra 2 com 2+ operacoes atipicas | alto |
| Regra 2 com 1 operacao atipica | medio |
| Nenhuma regra acionada | baixo |

O criterio sugerido no enunciado (cliente pego pelas DUAS regras) produz conjunto vazio nesta base — nenhum dos 30 clientes dispara ambas. As regras procuram fenomenos opostos: fracionamento busca varios valores medios concentrados; valor atipico busca um valor destoante. Por isso adotei o criterio graduado acima, que trata fracionamento (padrao de conduta) como mais grave que valor atipico isolado (evento).

## Metricas

- Concordancia exata: **0/6 (0%)**
- Concordancia adjacente (erro de ate um nivel): **2/6 (33%)**
- Agente mais brando que a regra: **6**
- Agente mais severo que a regra: **0**

## Matriz de confusao

Linhas = esperado pelas regras, colunas = atribuido pelo agente.

| risco_esperado_pelas_regras   |   baixo |   medio |   alto |
|:------------------------------|--------:|--------:|-------:|
| baixo                         |       0 |       0 |      0 |
| medio                         |       0 |       0 |      0 |
| alto                          |       4 |       2 |      0 |

## Caso a caso

| cliente_id   |   flags_regra1 |   flags_regra2 | risco_esperado_pelas_regras   | risco_atribuido_pelo_agente   | direcao            |   n_red_flags | ferramentas                                                                |
|:-------------|---------------:|---------------:|:------------------------------|:------------------------------|:-------------------|--------------:|:---------------------------------------------------------------------------|
| CLI-014      |              0 |              3 | alto                          | baixo                         | agente MAIS brando |             0 | historico_cliente > operacoes_do_dia > operacoes_do_dia > operacoes_do_dia |
| CLI-026      |              0 |              2 | alto                          | baixo                         | agente MAIS brando |             0 | historico_cliente > operacoes_do_dia                                       |
| CLI-028      |              0 |              2 | alto                          | baixo                         | agente MAIS brando |             0 | historico_cliente > operacoes_do_dia                                       |
| CLI-029      |              1 |              0 | alto                          | baixo                         | agente MAIS brando |             0 | operacoes_do_dia > historico_cliente                                       |
| CLI-001      |              0 |              2 | alto                          | medio                         | agente MAIS brando |             0 | historico_cliente > perfil_canal                                           |
| CLI-005      |              0 |              2 | alto                          | medio                         | agente MAIS brando |             0 | historico_cliente                                                          |

## Divergencias

A analise de cada divergencia esta em `docs/DECISOES.md`. O ponto de partida: as regras sao propositalmente simples e geram falsos positivos, entao divergencia nao e erro do agente por definicao — em varios casos e o agente que esta certo.

### CLI-014 — regra diz *alto*, agente diz *baixo* (agente MAIS brando)

- Sinalizacoes: Regra 1 = 0, Regra 2 = 3
- Volume: R$ 80,629.99 em 11 operacoes
- Ferramentas consultadas: historico_cliente > operacoes_do_dia > operacoes_do_dia > operacoes_do_dia
- Tipologia atribuida: Nao caracterizada

> O cliente realizou três operações acima do limite de valor atípico, porém cada uma ocorreu em dias distintos, para contrapartes diferentes, em canais e tipos distintos, sem sinais de fracionamento ou estruturação coordenada. Não há evidências de padrão de movimentação que sugira tentativa de lavagem de dinheiro. Assim, a sinalização parece ser um falso positivo gerado pela regra de valor atípico.

### CLI-026 — regra diz *alto*, agente diz *baixo* (agente MAIS brando)

- Sinalizacoes: Regra 1 = 0, Regra 2 = 2
- Volume: R$ 54,729.28 em 12 operacoes
- Ferramentas consultadas: historico_cliente > operacoes_do_dia
- Tipologia atribuida: Nao caracterizada

> Os dois valores que excederam o limite de R$10.164,65 foram isolados, com datas e contrapartes distintas, e não indicam fracionamento nem padrão de movimentação atípico. O cliente possui 12 operações no período, com ticket médio de R$4.560,77, o que coloca esses valores em 4,7 e 2,7 vezes o ticket médio, mas ainda dentro de um intervalo que pode ser legítimo. Assim, a regra de valor atípico não se sustenta como indicativo de PLD.

### CLI-028 — regra diz *alto*, agente diz *baixo* (agente MAIS brando)

- Sinalizacoes: Regra 1 = 0, Regra 2 = 2
- Volume: R$ 88,750.80 em 12 operacoes
- Ferramentas consultadas: historico_cliente > operacoes_do_dia
- Tipologia atribuida: Nao caracterizada

> Os valores das duas operações (R$ 27.715,48 e R$ 24.875,39) excedem o limite de R$ 22.151,25, mas são isoladas e não indicam fracionamento, uso intensivo de espécie, remessa internacional atípica, interposição de pessoa ou incompatibilidade com o perfil. O histórico mostra apenas 12 operações, com ticket médio de R$ 7.395,90, e as duas operações de alto valor são únicas e não repetidas. Assim, a sinalização não se sustenta.

### CLI-029 — regra diz *alto*, agente diz *baixo* (agente MAIS brando)

- Sinalizacoes: Regra 1 = 1, Regra 2 = 0
- Volume: R$ 191,385.77 em 16 operacoes
- Ferramentas consultadas: operacoes_do_dia > historico_cliente
- Tipologia atribuida: Nao caracterizada

> O cliente realizou 4 operações no dia 2026-05-26, totalizando R$ 71.297,68, com valores entre R$ 14.326,29 e R$ 19.418,96. Esses valores estão dentro da média de ticket do cliente (R$ 11.961,61) e não ultrapassam o maior valor histórico (R$ 48.045,64). As operações foram para 4 contrapartes distintas e não há evidência de estruturação coordenada ou fracionamento deliberado. A regra de fracionamento foi acionada apenas neste dia, sem ocorrências semelhantes em outros dias do período analisado. Assim, a sinalização não se sustenta na investigação.

### CLI-001 — regra diz *alto*, agente diz *medio* (agente MAIS brando)

- Sinalizacoes: Regra 1 = 0, Regra 2 = 2
- Volume: R$ 47,947.81 em 10 operacoes
- Ferramentas consultadas: historico_cliente > perfil_canal
- Tipologia atribuida: Uso intensivo de espécie

> O cliente realizou um depósito em espécie de R$ 25.110,15, representando 52,4 % do volume total e 5,2 vezes o ticket médio, além de um saque via TED de R$ 9.490,55, 2,0 vezes o ticket médio; ambos excedem o limite de R$ 8.045,77 definido pela regra de valor atípico. Esses valores são anômalos em relação ao histórico do cliente, que possui apenas 10 operações com ticket médio de R$ 4.794,78.

### CLI-005 — regra diz *alto*, agente diz *medio* (agente MAIS brando)

- Sinalizacoes: Regra 1 = 0, Regra 2 = 2
- Volume: R$ 64,742.66 em 11 operacoes
- Ferramentas consultadas: historico_cliente
- Tipologia atribuida: Incompatibilidade com o perfil

> O cliente CLI-005 possui 11 operações no período, com ticket médio de R$5.885,7. Duas operações foram sinalizadas pela regra de valor atípico: OP-00049 (R$11.988,17 via espécie) e OP-00043 (R$30.743,97 via cartão). O valor de OP-00043 excede em 5,7 vezes o ticket médio e é o maior valor do histórico, enquanto OP-00049 está apenas 1,1 vezes acima do limite de R$10.720,9. Não há evidências de fracionamento, uso intensivo de espécie em múltiplas transações ou outras anomalias. Assim, a sinalização parece ser um falso positivo, mas a operação de R$30.743,97 pode indicar incompatibilidade com o perfil habitual do cliente. Recomenda-se observar a origem e finalidade da transação de pagamento e a natureza do recebimento em espécie.
