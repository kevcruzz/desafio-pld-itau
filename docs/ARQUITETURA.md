# Arquitetura do fluxo multiagente (Nível 3 — Trilha A)

## O diagrama

```mermaid
flowchart TD
    A[Cliente sinalizado<br/>pelas regras determinísticas] --> B[Monta resumo do caso<br/>_resumo_do_caso]
    B --> C{{TRIADOR<br/>sem ferramentas}}

    C -->|arquivar| D[Encerra o caso<br/>risco = baixo<br/>PARADA]
    C -->|investigar<br/>alta ou normal| E{{INVESTIGADOR<br/>com ferramentas}}

    E --> F[historico_cliente]
    E --> G[operacoes_do_dia]
    E --> H[perfil_canal]
    F --> E
    G --> E
    H --> E

    E -->|registrar_evidencias| I{{REDATOR<br/>sem ferramentas}}
    E -->|bateu MAX_ITERACOES = 4| I

    I --> J[Parecer validado<br/>ParecerPLD]
    J --> K[(outputs/nivel_3/)]
    D --> K

    S[[EstadoCaso<br/>estado compartilhado]] -.escreve.- C
    S -.escreve.- E
    S -.escreve.- I

    style C fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style E fill:#e3f2fd,stroke:#0d47a1,stroke-width:2px
    style I fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    style D fill:#ffebee,stroke:#b71c1c,stroke-width:2px
    style S fill:#f5f5f5,stroke:#616161,stroke-dasharray: 4 4
```

---

## Os três papéis

### Triador

**O que faz:** olha o resumo do caso e decide se vale investigar ou se pode arquivar.

**Ferramentas:** nenhuma, de propósito. Se ele pudesse consultar a base, viraria um segundo
investigador e a economia sumiria. Ele decide com o que já está na mesa.

**Saída:** `{decisao, motivo, prioridade}`.

**Detalhe importante:** se a resposta dele não puder ser lida, o fluxo **investiga por
padrão** em vez de arquivar. Errar pro lado do falso positivo custa tempo de analista;
arquivar por causa de um erro de parsing custa um caso não visto. Os dois erros não têm o
mesmo preço, então o comportamento em caso de falha não é simétrico.

### Investigador

**O que faz:** usa as ferramentas do Nível 2 para levantar os fatos. Não atribui risco e não
escreve parecer.

**Ferramentas:** as três de `nivel_2/tools.py`, **sem nenhuma alteração**, mais
`registrar_evidencias` para encerrar.

**Detalhe importante:** o prompt pede explicitamente que ele registre tanto o que sustenta
quanto o que **enfraquece** a suspeita. Isso é resposta direta ao que eu vi no Nível 2 —
como as regras geram falso positivo de propósito, evidência que derruba a hipótese é mais
valiosa que evidência que confirma.

### Redator

**O que faz:** pega as evidências e escreve o parecer final.

**Ferramentas:** nenhuma. Ele não consulta a base — trabalha só com o que o investigador
apurou. Isso é proposital: se ele pudesse consultar, poderia trazer número que não passou
pela investigação, e a evidência do parecer deixaria de ser rastreável.

**Saída:** validada pelo mesmo `ParecerPLD` do Nível 1, com o mesmo vocabulário fechado e a
mesma regra de coerência entre risco alto e red flags.

---

## Estado compartilhado

`EstadoCaso` é uma dataclass que acompanha o caso do começo ao fim. Cada papel escreve o seu
pedaço:

| Papel | O que escreve |
|---|---|
| Triador | `triagem_decisao`, `triagem_motivo`, `triagem_prioridade` |
| Investigador | `evidencias`, `ferramentas_chamadas`, `iteracoes_investigacao` |
| Redator | `nivel_risco`, `tipologia_suspeita`, `red_flags`, `justificativa` |
| Todos | `tokens_por_papel`, `latencia_por_papel` |

**Nenhum papel lê a saída bruta do anterior — todos leem o estado.** Isso mantém o
acoplamento entre eles no formato do `EstadoCaso`, e não no formato da resposta de um
modelo. Se eu trocar o modelo do triador amanhã, o investigador não precisa saber.

A telemetria é registrada **por papel**, não só no total. É isso que permite medir onde o
custo está e se o triador está se pagando.

---

## Condição de parada

Três pontos onde o fluxo termina:

1. **Triador arquiva** — encerra ali, não chama mais ninguém. É a parada que dá economia.
2. **Investigador bate `MAX_ITERACOES = 4`** — segue para o redator com o que conseguiu
   levantar. Não trava em loop.
3. **Redator conclui** — fim natural.

Falha de API em qualquer papel também encerra, com o status registrado.

---

## Por que essa arquitetura, e não o agente único do Nível 2

Não é uma camada a mais por elegância. É resposta a um número que eu medi.

No Nível 2, **todo cliente recebe investigação completa**. Rodando o lote, 4 dos 6 casos
concluídos saíram como risco baixo — e cada um custou entre 5.900 e 11.100 tokens de
entrada. Ou seja, gastei o fluxo inteiro em casos que o próprio agente concluiu que não
sustentavam suspeita.

O triador ataca exatamente isso: um caso arquivado custa **uma chamada curta** em vez do
fluxo inteiro. O `fluxo.py` mede e imprime essa diferença ao final do lote.

**E se não se pagar, eu digo.** Se o multiagente gastar mais tokens para chegar nos mesmos
pareceres do agente único, a arquitetura não se justifica — a comparação está no
`DECISOES.md`, e reportar que não valeu é um resultado tão legítimo quanto o contrário.

---

## O trade-off que essa arquitetura cria

**Ganha:** custo menor nos casos arquivados, papéis com responsabilidade separada, e o
parecer fica rastreável — cada red flag vem de uma evidência registrada, que veio de uma
ferramenta.

**Perde:** mais pontos de falha. São três chamadas em vez de uma, e cada uma pode falhar por
quota, validação ou formato. Nos casos que vão até o fim, a latência é maior.

**E tem um risco de qualidade que vale registrar:** o triador pode arquivar um caso que
merecia investigação. No agente único isso não acontece, porque todo caso é investigado. É
uma troca deliberada de recall por custo — e numa mesa de PLD real, essa é uma decisão que
teria que ser aprovada pela área de compliance, não pela engenharia.

---

## Como rodar

```bash
cd nivel_3
python fluxo.py CLI-029      # um caso, com a trajetória impressa
python fluxo.py lote 5       # os 5 primeiros do ranking
```

Saídas em `outputs/nivel_3/`: um JSON por cliente com o `EstadoCaso` completo, e
`fluxo_multiagente.csv` com a tabela comparativa.
