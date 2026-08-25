# Triagem PLD — Desafio Técnico

Processo de triagem de Prevenção à Lavagem de Dinheiro que combina **regras
determinísticas** (o que é cálculo) com **um modelo de linguagem** (o que é
interpretação).

O princípio que organiza o projeto inteiro: soma, mediana, contagem e comparação com
limite acontecem em pandas. A LLM recebe os números já apurados e produz o parecer. Em
nenhum ponto o modelo é responsável por um número que importa.

---

## O que foi entregue

| Nível | Status |
|---|---|
| **Nível 1** — tratamento, regras, análise com LLM | completo |
| **Nível 2** — escala, ferramentas, agente, lote, confronto | completo, com lote parcial (6 de 10 pareceres concluídos) |
| **Nível 3** | não implementado; plano em `docs/DECISOES.md` §8.4 |

Detalhe item a item em `ENTREGA.yaml`.

---

## Como rodar

**Requisitos:** Python 3.11+ e uma chave da API do Groq
([console.groq.com/keys](https://console.groq.com/keys), camada gratuita).

```bash
git clone <url-do-repo>
cd <repo>

python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

cp .env.example .env             # Windows: copy .env.example .env
# preencha GROQ_API_KEY no .env
```

**Nível 1** — o notebook já está no repositório com todas as saídas executadas:

```bash
jupyter notebook nivel_1/nivel_1.ipynb
```

**Nível 2:**

```bash
cd nivel_2
python tools.py              # testa as ferramentas, sem usar a API
python agente.py CLI-029     # um cliente
python agente.py lote        # os 10 mais sinalizados → outputs/
python confronto.py          # compara regras x agente → outputs/
```

O lote leva 8 a 12 minutos: há intervalo entre chamadas para respeitar o teto de tokens
por minuto da camada gratuita.

---

## Estrutura

```
├── dados/                  dados_nivel_1.json, dados_nivel_2.json
├── nivel_1/
│   ├── nivel_1.ipynb       notebook com saídas executadas
│   ├── pipeline.py         tratamento e regras — reutilizado pelo Nível 2
│   └── llm.py              acesso à LLM: schema, cache, retry, telemetria
├── nivel_2/
│   ├── tools.py            ferramentas que o agente consulta
│   ├── agente.py           agente com tool calling + execução em lote
│   └── confronto.py        regras x parecer do agente
├── outputs/
│   ├── lote.csv            uma linha por cliente, com custo e trajetória
│   ├── pareceres/          um JSON por cliente, com trajetória completa
│   ├── confronto.csv
│   └── confronto_analise.md
└── docs/
    ├── DECISOES.md         trade-offs, limitações e plano
    └── USO_DE_IA.md
```

`pipeline.py` fica em `nivel_1/` porque é lá que o enunciado define o tratamento. O Nível
2 importa o mesmo módulo — **a Parte A não exigiu uma linha nova de código**, só trocar o
caminho do arquivo.

---

## O que eu concluí

### Sobre os dados

A base do Nível 2 tem 322 operações e 30 clientes, com três defeitos plantados:
5 duplicatas exatas, 7 operações sem data e 7 em moeda estrangeira. O Nível 1 traz os
mesmos três, um de cada.

**Dois deles alteram a triagem em direções opostas.** A duplicata de `OP-0007` fabrica um
alerta de fracionamento em `CLI-A-3` que desaparece assim que a linha fantasma sai. A
operação em USD não convertida esconde a única sinalização da Regra 2 no Nível 1, e quatro
clientes no Nível 2. Nenhuma das duas falhas se manifesta como erro — o código roda, as
regras executam, o resultado sai errado em silêncio.

A limpeza não é etapa preparatória da análise. É parte do controle.

### Sobre os clientes

As regras sinalizam 17 dos 30 clientes. **A Regra 1 pega 4 (`CLI-002`, `CLI-003`,
`CLI-017`, `CLI-029`) e a Regra 2 pega 13, sem nenhuma sobreposição** — elas procuram
fenômenos opostos, uma busca vários valores médios concentrados, a outra um valor
destoante.

O caso mais instrutivo é `CLI-029`. A Regra 1 o sinalizou por quatro operações no mesmo
dia somando R\$ 71 mil, nenhuma atingindo R\$ 20 mil. Investigando: um saque, dois
depósitos e uma transferência recebida, para quatro contrapartes distintas, numa conta com
15 contrapartes em 16 operações e R\$ 191 mil de giro. **Não é fracionamento** — é um dia
movimentado de uma conta ativa. A regra não olha direção do fluxo, não olha relação entre
contrapartes e não olha o porte do cliente.

### Sobre a divisão entre regra e modelo

Entrei achando que a fronteira era questão de precisão: a LLM erra conta, então o pandas
calcula. Saí com uma leitura diferente.

**Registrei quatro ocasiões em que o modelo calculou por conta própria** — incluindo sob
instrução explícita e em caixa alta para não fazê-lo. Numa delas apresentou o limiar da
regra (5×) como se fosse a magnitude medida (11,9×); noutra inverteu o sentido de um teto,
afirmando que valores entre R\$ 14 mil e R\$ 19 mil estavam *acima* do limite de R\$ 20
mil.

Prompt melhor corrigiu tipologia, eliminou red flag artificial e impediu uma inferência de
intenção que os dados não sustentavam. Não impediu o cálculo. **Instruir a LLM a não
calcular não é um controle confiável** — se a corretude de um número importa, ele precisa
ser calculado fora do modelo.

Em contrapartida, a interpretação é onde o modelo agregou valor real: nenhuma das duas
regras diria que `CLI-029` é falso positivo, e o agente disse, com três argumentos
independentes.

### Sobre o confronto

Sobre os 6 clientes com parecer concluído: **0% de concordância exata, 33% adjacente, e o
agente foi mais brando que a regra em 6 de 6 casos.**

A unidirecionalidade é o dado, não a taxa. Erro aleatório produziria casos nos dois
sentidos; viés numa única direção é evidência sobre o critério, não sobre o agente — minha
régua está severa demais. Não a recalibrei depois de ver o resultado, porque ajustar a
métrica ao próprio experimento a esvazia.

Análise caso a caso em `docs/DECISOES.md` §6.3.

---

## Modelo utilizado

`openai/gpt-oss-20b` via Groq, camada gratuita.

O modelo sugerido no enunciado (`llama-3.3-70b`) não está mais disponível no provedor —
retorna 404. Escolhi um substituto entre os modelos que a chave alcança, priorizando
capacidade de raciocínio e suporte a tool calling. A migração de `gpt-oss-120b` para
`20b` durante o trabalho foi imposta pelo teto de tokens diários; o lote foi
**reexecutado inteiro** no modelo novo, porque misturar modelos invalidaria a comparação.

Custo estimado da execução final do lote: **US\$ 0,0416** (49.993 tokens de entrada,
15.336 de saída). Latência média de 48,7s por cliente, p95 de 71s.
