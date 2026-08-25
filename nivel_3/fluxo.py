"""
Nivel 3 — Trilha A: fluxo multiagente.

Tres papeis encadeados, com estado compartilhado e condicao de parada:

    Triador      -> olha o resumo do caso e decide se vale investigar.
                    Nao usa ferramenta nenhuma. Chamada barata.
    Investigador -> usa as ferramentas do Nivel 2 para juntar evidencia.
                    So roda se o Triador deixar passar.
    Redator      -> pega a evidencia e escreve o parecer final validado.

--------------------------------------------------------------------
POR QUE ESCOLHI A TRILHA A
--------------------------------------------------------------------

Duas razoes, e a segunda e a que realmente pesa.

A primeira e reuso: o Investigador usa `nivel_2/tools.py` sem nenhuma
alteracao, e o Redator usa o mesmo `ParecerPLD` do Nivel 1. O fluxo novo
nao duplicou logica nenhuma.

A segunda e um problema de custo que eu MEDI no Nivel 2. La, todo cliente
recebe investigacao completa — inclusive os que o proprio agente conclui
como risco baixo depois de duas ferramentas. Rodando o lote, 4 dos 6
casos concluidos sairam como risco baixo, e cada um deles custou entre
5.900 e 11.100 tokens de entrada.

O Triador ataca exatamente isso: um caso arquivado custa uma chamada
curta em vez do fluxo inteiro. Nao e uma camada a mais por elegancia
arquitetural — e uma resposta a um numero que eu vi.

--------------------------------------------------------------------
ESTADO COMPARTILHADO E PARADA
--------------------------------------------------------------------

`EstadoCaso` acompanha o caso do inicio ao fim e cada papel escreve o seu
pedaco. Nenhum papel le a saida bruta do anterior — todos leem o estado.
Isso mantem o acoplamento entre eles no formato do estado, e nao no
formato da resposta de um modelo.

A parada acontece em tres pontos:
  1. Triador decide arquivar          -> encerra, nao chama mais ninguem
  2. Investigador bate MAX_ITERACOES  -> segue para o Redator com o que tem
  3. Redator conclui                  -> fim natural

Rode com:
    python fluxo.py CLI-029        # um caso
    python fluxo.py lote 5         # os N primeiros do ranking
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "nivel_1"))
sys.path.insert(0, str(RAIZ / "nivel_2"))

from dotenv import load_dotenv
from groq import Groq

import llm
import pipeline as p
import tools

load_dotenv()

MODELO = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
MAX_ITERACOES_INVESTIGADOR = 4
OUTPUTS = RAIZ / "outputs"


# ==========================================================================
# Estado compartilhado
# ==========================================================================

@dataclass
class EstadoCaso:
    """Carrega o caso pelos tres papeis. Cada um escreve o seu pedaco."""

    cliente_id: str

    # preenchido antes do fluxo
    resumo_inicial: str = ""

    # Triador
    triagem_decisao: str | None = None       # investigar | arquivar
    triagem_motivo: str | None = None
    triagem_prioridade: str | None = None    # alta | normal

    # Investigador
    evidencias: list[str] = field(default_factory=list)
    ferramentas_chamadas: list[str] = field(default_factory=list)
    iteracoes_investigacao: int = 0

    # Redator
    nivel_risco: str | None = None
    tipologia_suspeita: str | None = None
    red_flags: list[str] = field(default_factory=list)
    justificativa: str | None = None

    # controle
    encerrado_em: str | None = None          # triador | investigador | redator
    status: str = "iniciado"
    erro: str | None = None

    # telemetria por papel
    tokens_por_papel: dict = field(default_factory=dict)
    latencia_por_papel: dict = field(default_factory=dict)

    def tokens_totais(self) -> tuple[int, int]:
        e = sum(v[0] for v in self.tokens_por_papel.values())
        s = sum(v[1] for v in self.tokens_por_papel.values())
        return e, s

    def custo_estimado(self) -> float:
        e, s = self.tokens_totais()
        return round(
            e / 1e6 * llm.PRECO_USD_POR_MILHAO["entrada"]
            + s / 1e6 * llm.PRECO_USD_POR_MILHAO["saida"],
            6,
        )

    def para_linha(self) -> dict:
        e, s = self.tokens_totais()
        return {
            "cliente_id": self.cliente_id,
            "status": self.status,
            "encerrado_em": self.encerrado_em,
            "triagem": self.triagem_decisao,
            "prioridade": self.triagem_prioridade,
            "nivel_risco": self.nivel_risco,
            "tipologia": self.tipologia_suspeita,
            "n_evidencias": len(self.evidencias),
            "n_red_flags": len(self.red_flags),
            "ferramentas": " > ".join(self.ferramentas_chamadas) or "(nenhuma)",
            "tokens_entrada": e,
            "tokens_saida": s,
            "latencia_s": round(sum(self.latencia_por_papel.values()), 2),
            "custo_estimado_usd": self.custo_estimado(),
            "modelo": MODELO,
        }


def _cliente_groq() -> Groq:
    chave = os.getenv("GROQ_API_KEY")
    if not chave:
        raise RuntimeError("GROQ_API_KEY nao encontrada. Preencha o .env.")
    return Groq(api_key=chave)


def _pausa():
    time.sleep(llm.INTERVALO_MINIMO_S)


# ==========================================================================
# Papel 1 — Triador
# ==========================================================================

SYSTEM_TRIADOR = """Voce e o triador da mesa de PLD. Sua unica funcao e decidir se um caso merece investigacao ou pode ser arquivado.

Voce NAO investiga e NAO produz parecer. Voce olha o resumo e decide.

CRITERIO
- INVESTIGAR quando ha elemento que so pode ser esclarecido olhando as operacoes: concentracao num dia, valor destoante, uso atipico de canal, volume incompativel com o porte.
- ARQUIVAR quando o resumo ja mostra que a sinalizacao provavelmente nao se sustenta: cliente de alto volume onde os valores sinalizados sao proporcionais ao giro, ou sinalizacao unica de baixa materialidade.

Arquivar e uma decisao legitima e esperada. As regras que geram estes casos sao simples e produzem falso positivo de proposito. Investigar tudo por precaucao gasta tempo de mesa e nao melhora a triagem.

PRIORIDADE (so quando decidir investigar)
- alta: fracionamento, ou mais de uma regra acionada
- normal: os demais

Responda EXCLUSIVAMENTE com um objeto JSON:
{"decisao": "investigar" | "arquivar", "motivo": "<uma frase objetiva>", "prioridade": "alta" | "normal"}"""


def triador(estado: EstadoCaso, verbose: bool = True) -> EstadoCaso:
    inicio = time.perf_counter()
    cliente = _cliente_groq()

    try:
        resp = cliente.chat.completions.create(
            model=MODELO,
            messages=[
                {"role": "system", "content": SYSTEM_TRIADOR},
                {"role": "user", "content": estado.resumo_inicial},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=1500,
        )
    except Exception as e:
        estado.status = "erro_api"
        estado.erro = f"triador: {str(e)[:200]}"
        estado.encerrado_em = "triador"
        return estado

    estado.tokens_por_papel["triador"] = (
        resp.usage.prompt_tokens,
        resp.usage.completion_tokens,
    )
    estado.latencia_por_papel["triador"] = time.perf_counter() - inicio

    try:
        d = json.loads(llm.extrair_json(resp.choices[0].message.content or ""))
        estado.triagem_decisao = d.get("decisao")
        estado.triagem_motivo = d.get("motivo")
        estado.triagem_prioridade = d.get("prioridade", "normal")
    except (json.JSONDecodeError, ValueError) as e:
        # Falha na triagem nao pode arquivar caso por acidente: na duvida,
        # investiga. Errar para o lado do falso positivo e mais barato que
        # arquivar sem ter lido.
        estado.triagem_decisao = "investigar"
        estado.triagem_motivo = f"falha ao ler decisao do triador ({e}); seguindo por precaucao"
        estado.triagem_prioridade = "normal"

    if verbose:
        print(f"    [triador] {estado.triagem_decisao} ({estado.triagem_prioridade}) — {estado.triagem_motivo}")

    if estado.triagem_decisao == "arquivar":
        estado.status = "arquivado"
        estado.encerrado_em = "triador"
        estado.nivel_risco = "baixo"
        estado.tipologia_suspeita = "Nao caracterizada"
        estado.justificativa = (
            f"Caso arquivado na triagem sem investigacao detalhada. "
            f"Motivo: {estado.triagem_motivo}"
        )

    return estado


# ==========================================================================
# Papel 2 — Investigador
# ==========================================================================

SYSTEM_INVESTIGADOR = """Voce e o investigador da mesa de PLD. Sua funcao e reunir evidencia sobre um caso que a triagem liberou.

Voce NAO escreve o parecer final e NAO atribui nivel de risco. Isso e do redator. Voce levanta os fatos.

COMO INVESTIGAR
- Use apenas as ferramentas pertinentes ao caso. Consultar tudo por precaucao nao melhora a evidencia.
- Leia a descricao de cada ferramenta antes de escolher: cada uma responde um tipo de pergunta e diz o que NAO responde.
- Pare quando tiver o suficiente e chame registrar_evidencias.

REGRAS INEGOCIAVEIS
- Nao recalcule, nao estime e nao infira nenhum numero que nao esteja no retorno das ferramentas.
- Cada evidencia deve citar um dado que voce viu, com o numero exato como veio.
- Registre tanto o que SUSTENTA quanto o que ENFRAQUECE a suspeita. Evidencia que derruba a hipotese e tao util quanto a que confirma — na verdade e mais, porque as regras aqui geram falso positivo de proposito.

Ao terminar, chame registrar_evidencias com a lista de fatos apurados."""

FERRAMENTA_EVIDENCIAS = {
    "type": "function",
    "function": {
        "name": "registrar_evidencias",
        "description": (
            "Encerra a investigacao e registra os fatos apurados. Chame quando "
            "ja tiver consultado o necessario. E a unica forma de concluir a "
            "investigacao — nao escreva as evidencias como texto livre."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "evidencias": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fatos apurados, cada um citando um numero visto nas "
                        "ferramentas. Inclua tanto o que sustenta quanto o que "
                        "enfraquece a suspeita."
                    ),
                }
            },
            "required": ["evidencias"],
        },
    },
}


def investigador(estado: EstadoCaso, verbose: bool = True) -> EstadoCaso:
    inicio = time.perf_counter()
    cliente = _cliente_groq()
    esquema = tools.FERRAMENTAS_SCHEMA[:3] + [FERRAMENTA_EVIDENCIAS]

    mensagens = [
        {"role": "system", "content": SYSTEM_INVESTIGADOR},
        {"role": "user", "content": estado.resumo_inicial},
    ]

    ent = sai = 0
    concluiu = False

    for iteracao in range(1, MAX_ITERACOES_INVESTIGADOR + 1):
        estado.iteracoes_investigacao = iteracao
        if iteracao > 1:
            _pausa()

        try:
            resp = cliente.chat.completions.create(
                model=MODELO,
                messages=mensagens,
                tools=esquema,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=3000,
            )
        except Exception as e:
            estado.status = "erro_api"
            estado.erro = f"investigador: {str(e)[:200]}"
            estado.encerrado_em = "investigador"
            break

        ent += resp.usage.prompt_tokens
        sai += resp.usage.completion_tokens
        msg = resp.choices[0].message

        if not msg.tool_calls:
            break

        mensagens.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            nome = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            if nome == "registrar_evidencias":
                estado.evidencias = args.get("evidencias") or []
                if verbose:
                    print(f"    [investigador] {len(estado.evidencias)} evidencia(s) registrada(s)")
                concluiu = True
                break

            if verbose:
                print(f"    [investigador] {nome}({args})")

            funcao = tools.REGISTRO.get(nome)
            saida = funcao(**args) if funcao else {"erro": f"ferramenta desconhecida: {nome}"}
            estado.ferramentas_chamadas.append(nome)
            mensagens.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(saida, ensure_ascii=False, default=str),
                }
            )

        if concluiu:
            break

    estado.tokens_por_papel["investigador"] = (ent, sai)
    estado.latencia_por_papel["investigador"] = time.perf_counter() - inicio

    if not estado.evidencias and estado.status != "erro_api":
        estado.evidencias = [
            "Investigacao encerrada sem registro estruturado de evidencias "
            f"apos {estado.iteracoes_investigacao} iteracoes."
        ]

    return estado


# ==========================================================================
# Papel 3 — Redator
# ==========================================================================

SYSTEM_REDATOR = """Voce e o redator da mesa de PLD. Recebe as evidencias que o investigador apurou e escreve o parecer final para o analista humano que vai decidir.

Voce NAO investiga e NAO consulta a base. Trabalha apenas com o que esta no dossie de evidencias.

REGRAS INEGOCIAVEIS
- Nao invente numero. Se um valor nao esta nas evidencias, ele nao entra no parecer.
- Toda red flag deve corresponder a uma evidencia recebida.
- Se as evidencias NAO sustentam suspeita, atribua risco baixo e deixe red_flags como lista VAZIA. Lista vazia e resposta valida e esperada — nao invente indicio para preencher.
- Distinga PADRAO de EVENTO. Fracionamento e conduta coordenada. Operacao isolada de valor alto e evento, e pode ter causa legitima.

TIPOLOGIAS (use a nomenclatura, ou 'Nao caracterizada'):
Fracionamento (smurfing) | Uso intensivo de especie | Remessa internacional atipica | Interposicao de pessoa | Incompatibilidade com o perfil

NIVEIS
- baixo: compativel com o perfil; a sinalizacao da regra nao se sustentou
- medio: merece observacao, sem elemento conclusivo
- alto: elemento objetivo que justifica analise humana prioritaria. Exige ao menos uma red flag.

Responda EXCLUSIVAMENTE com um objeto JSON:
{"nivel_risco": "...", "tipologia_suspeita": "...", "red_flags": [...], "justificativa": "<3 a 5 frases>"}
O campo red_flags deve ser um ARRAY JSON de strings."""


def redator(estado: EstadoCaso, verbose: bool = True) -> EstadoCaso:
    inicio = time.perf_counter()

    dossie = f"""{estado.resumo_inicial}

DECISAO DA TRIAGEM
{estado.triagem_decisao} ({estado.triagem_prioridade}) — {estado.triagem_motivo}

EVIDENCIAS APURADAS PELO INVESTIGADOR
""" + "\n".join(f"- {e}" for e in estado.evidencias)

    parecer, tel = llm.chamar(
        SYSTEM_REDATOR, dossie, modelo=MODELO, usar_cache=False
    )

    estado.tokens_por_papel["redator"] = (tel.tokens_entrada, tel.tokens_saida)
    estado.latencia_por_papel["redator"] = time.perf_counter() - inicio

    if parecer:
        estado.nivel_risco = parecer.nivel_risco
        estado.tipologia_suspeita = parecer.tipologia_suspeita
        estado.red_flags = parecer.red_flags
        estado.justificativa = parecer.justificativa
        estado.status = "ok"
    else:
        estado.status = tel.status
        estado.erro = f"redator: {tel.erro}"

    estado.encerrado_em = "redator"
    if verbose:
        print(f"    [redator] risco={estado.nivel_risco} | {len(estado.red_flags)} red flag(s)")
    return estado


# ==========================================================================
# Orquestracao
# ==========================================================================

def _resumo_do_caso(df, cliente_id: str) -> str:
    """Resumo minimo do caso — o mesmo insumo para os tres papeis."""
    ops = df[df["cliente_id"] == cliente_id]
    dias = ops.loc[ops["flag_fracionamento"], "data_dt"].dt.strftime("%Y-%m-%d").unique().tolist()
    n_atip = int(ops["flag_valor_atipico"].sum())

    gatilhos = []
    if dias:
        gatilhos.append(f"REGRA DE FRACIONAMENTO acionada em: {', '.join(dias)}")
    if n_atip:
        gatilhos.append(f"REGRA DE VALOR ATIPICO acionada em {n_atip} operacao(oes)")

    return f"""CASO PARA TRIAGEM

Cliente: {cliente_id}
Operacoes no periodo: {len(ops)}
Volume total: R$ {ops['valor_brl'].sum():,.2f}
Ticket mediano: R$ {ops['valor_brl'].median():,.2f}

Sinalizacao recebida:
{chr(10).join('- ' + g for g in gatilhos)}"""


def processar_caso(cliente_id: str, df=None, verbose: bool = True) -> EstadoCaso:
    """Roda o fluxo completo sobre um cliente, respeitando a parada."""
    if df is None:
        df = tools.carregar_base()

    estado = EstadoCaso(cliente_id=cliente_id)
    estado.resumo_inicial = _resumo_do_caso(df, cliente_id)

    estado = triador(estado, verbose)
    if estado.status in ("arquivado", "erro_api"):
        return estado                      # <-- condicao de parada

    _pausa()
    estado = investigador(estado, verbose)
    if estado.status == "erro_api":
        return estado

    _pausa()
    return redator(estado, verbose)


def rodar_lote(top: int = 5, verbose: bool = True) -> list[EstadoCaso]:
    import pandas as pd

    df = tools.carregar_base()
    clientes = p.ranking_clientes(df, top=top).index.tolist()
    print(f"Fluxo multiagente sobre {len(clientes)} clientes: {', '.join(clientes)}\n")

    resultados = []
    for i, cid in enumerate(clientes, 1):
        print(f"[{i}/{len(clientes)}] {cid}")
        est = processar_caso(cid, df, verbose)
        e, s = est.tokens_totais()
        print(f"    -> {est.status} | encerrado em: {est.encerrado_em} | "
              f"{e + s} tokens | {sum(est.latencia_por_papel.values()):.1f}s\n")
        resultados.append(est)
        if i < len(clientes):
            _pausa()

    (OUTPUTS / "nivel_3").mkdir(parents=True, exist_ok=True)
    for est in resultados:
        (OUTPUTS / "nivel_3" / f"{est.cliente_id}.json").write_text(
            json.dumps(asdict(est), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    tabela = pd.DataFrame([e.para_linha() for e in resultados])
    tabela.to_csv(OUTPUTS / "nivel_3" / "fluxo_multiagente.csv", index=False, encoding="utf-8")

    print("=" * 78)
    print(tabela.to_string(index=False))
    print("=" * 78)

    arquivados = tabela[tabela.encerrado_em == "triador"]
    completos = tabela[tabela.encerrado_em == "redator"]
    print(f"\nArquivados na triagem : {len(arquivados)}")
    print(f"Fluxo completo        : {len(completos)}")
    if len(arquivados) and len(completos):
        m_arq = arquivados.tokens_entrada.add(arquivados.tokens_saida).mean()
        m_com = completos.tokens_entrada.add(completos.tokens_saida).mean()
        print(f"\nTokens medios — arquivado: {m_arq:,.0f} | completo: {m_com:,.0f}")
        print(f"Economia por caso arquivado: {(1 - m_arq / m_com) * 100:.0f}%")
    print(f"\nCusto total estimado: US$ {tabela.custo_estimado_usd.sum():.4f}")
    print(f"Salvo em outputs/nivel_3/")
    return resultados


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "lote":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        rodar_lote(n)
    elif len(sys.argv) > 1:
        est = processar_caso(sys.argv[1])
        print(json.dumps(asdict(est), indent=2, ensure_ascii=False))
    else:
        print("uso: python fluxo.py CLI-029   |   python fluxo.py lote 5")
