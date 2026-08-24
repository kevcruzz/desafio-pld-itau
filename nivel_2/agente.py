"""
Agente de triagem PLD com tool calling.

O que faz dele agente e nao script: o modelo recebe o caso e DECIDE
quais ferramentas consultar, em que ordem e com quais argumentos. Nao ha
sequencia fixa no codigo — o loop abaixo apenas executa o que o modelo
pediu e devolve o resultado, ate ele concluir ou bater o teto.

A variacao de trajetoria nao e acidente nem sorte: e resultado de duas
decisoes de desenho.

  1. O prompt informa QUAL regra sinalizou o cliente. Fracionamento e um
     evento datado e pede o recorte daquele dia; valor atipico e um
     desvio do historico e pede o contexto agregado. Sao perguntas
     diferentes, entao puxam ferramentas diferentes.

  2. As docstrings de tools.py dizem explicitamente quando cada
     ferramenta serve e o que ela NAO responde. Ferramenta mal descrita
     produz agente que chama tudo por seguranca.

Nao forcei a rota com if/else. Desenhei o contexto para que a escolha
certa fosse a obvia, e registrei a trajetoria de cada caso para poder
verificar depois se a decisao variou de fato.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nivel_1"))

from dotenv import load_dotenv
from groq import Groq

import llm
import pipeline as p
import tools

load_dotenv()

MODELO = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
MAX_ITERACOES = 5

SYSTEM = """Voce e analista de Prevencao a Lavagem de Dinheiro (PLD) de uma instituicao financeira brasileira, na mesa de triagem.

Um cliente foi sinalizado por regras deterministicas. Sua funcao e INVESTIGAR o caso usando as ferramentas disponiveis e depois redigir um parecer tecnico para o analista humano que vai decidir.

COMO INVESTIGAR
- Voce tem tres ferramentas. Consulte APENAS as que forem pertinentes ao caso na sua frente. Consultar tudo por precaucao gasta tempo de mesa e nao melhora o parecer.
- Leia a descricao de cada ferramenta antes de escolher: cada uma responde a um tipo de pergunta e diz o que NAO responde.
- Chame quantas precisar, uma de cada vez, ate ter elementos suficientes. Depois pare e produza o parecer.

REGRAS INEGOCIAVEIS
- Nao recalcule, nao estime e nao infira nenhum numero que nao esteja no retorno das ferramentas. Todos os valores foram apurados por rotina deterministica auditada.
- Toda red flag deve citar um dado que voce viu. Sem dado, sem flag.
- As regras que sinalizaram este cliente sao simples e geram falsos positivos de proposito. Se a investigacao NAO sustentar a suspeita, diga isso e atribua risco baixo. Discordar da regra com boa justificativa e o resultado esperado quando a evidencia aponta nessa direcao.
- Distinga PADRAO de EVENTO. Fracionamento e conduta coordenada (exige varias operacoes deliberadamente abaixo de um limite, tipicamente na mesma direcao e para destinos relacionados). Uma operacao isolada de valor alto e evento, e pode ter causa legitima.
- Considere o porte do cliente. Tres operacoes de R$ 18 mil sao rotina para quem movimenta R$ 190 mil e sao excepcionais para quem movimenta R$ 20 mil.

TIPOLOGIAS DE REFERENCIA (use a nomenclatura, ou 'Nao caracterizada'):
- Fracionamento (smurfing): divisao deliberada de valor em operacoes menores para evitar limite de reporte
- Uso intensivo de especie: concentracao em deposito/saque em dinheiro acima do tipico da base
- Remessa internacional atipica: operacao em moeda estrangeira sem lastro compativel com o perfil
- Interposicao de pessoa: conta usada como passagem, entrada e saida rapidas
- Incompatibilidade com o perfil: volume desalinhado do historico do proprio cliente

NIVEIS DE RISCO
- baixo: movimentacao compativel com o perfil; a sinalizacao da regra nao se sustenta na investigacao
- medio: merece observacao, sem elemento conclusivo
- alto: elemento objetivo que justifica analise humana prioritaria

COMO CONCLUIR
Quando tiver elementos suficientes, chame a ferramenta emitir_parecer com o resultado.
ATENCAO: emitir_parecer e uma FERRAMENTA como as outras, e a unica forma de encerrar o caso. Chame-a pelo nome exato 'emitir_parecer'. Nao escreva o parecer como texto livre e nao invente outra ferramenta para entrega-lo."""


@dataclass
class ResultadoAgente:
    """Parecer + trajetoria + telemetria de um caso."""

    cliente_id: str
    status: str  # ok | falha_validacao | erro_api | sem_conclusao
    nivel_risco: str | None = None
    tipologia_suspeita: str | None = None
    red_flags: list[str] = field(default_factory=list)
    justificativa: str | None = None
    ferramentas_chamadas: list[str] = field(default_factory=list)
    trajetoria: list[dict] = field(default_factory=list)
    n_iteracoes: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    latencia_s: float = 0.0
    custo_estimado_usd: float = 0.0
    modelo: str = MODELO
    erro: str | None = None

    def para_linha(self) -> dict:
        """Versao achatada, para virar linha de DataFrame."""
        return {
            "cliente_id": self.cliente_id,
            "status": self.status,
            "nivel_risco": self.nivel_risco,
            "tipologia_suspeita": self.tipologia_suspeita,
            "n_red_flags": len(self.red_flags),
            "ferramentas_chamadas": " > ".join(self.ferramentas_chamadas) or "(nenhuma)",
            "n_chamadas_ferramenta": len(self.ferramentas_chamadas),
            "n_iteracoes": self.n_iteracoes,
            "tokens_entrada": self.tokens_entrada,
            "tokens_saida": self.tokens_saida,
            "latencia_s": round(self.latencia_s, 2),
            "custo_estimado_usd": self.custo_estimado_usd,
            "modelo": self.modelo,
        }


def _recuperar_de_erro(texto_erro: str) -> str | None:
    """Extrai o objeto de argumentos de dentro de um erro tool_use_failed.

    O formato do erro traz: 'failed_generation': '{"name": "JSON",
    "arguments": {...o parecer...}}'. Queremos o conteudo de "arguments".
    """
    if "failed_generation" not in texto_erro:
        return None

    marcador = '"arguments":'
    pos = texto_erro.find(marcador)
    if pos == -1:
        return None

    trecho = texto_erro[pos + len(marcador):]
    inicio = trecho.find("{")
    if inicio == -1:
        return None

    # Percorre contando chaves para achar o fechamento correspondente,
    # ignorando chaves dentro de strings.
    profundidade, dentro_de_string, escapado = 0, False, False
    for i, ch in enumerate(trecho[inicio:], start=inicio):
        if escapado:
            escapado = False
            continue
        if ch == "\\":
            escapado = True
            continue
        if ch == '"':
            dentro_de_string = not dentro_de_string
            continue
        if dentro_de_string:
            continue
        if ch == "{":
            profundidade += 1
        elif ch == "}":
            profundidade -= 1
            if profundidade == 0:
                return trecho[inicio:i + 1].replace("\\n", " ").replace('\\"', '"')
    return None


def _contexto_do_caso(df, cliente_id: str) -> str:
    """Monta o enunciado do caso: quem e o cliente e por que foi sinalizado.

    Deliberadamente MINIMO. Entrego o gatilho e o porte, nao o dossie
    inteiro — se eu ja entregasse tudo, o modelo nao teria motivo para
    usar ferramenta nenhuma e o exercicio viraria uma chamada unica.
    E o gatilho e o que da ao modelo base para escolher POR ONDE comecar.
    """
    ops = df[df["cliente_id"] == cliente_id]
    dias_frac = (
        ops.loc[ops["flag_fracionamento"], "data_dt"].dt.strftime("%Y-%m-%d").unique().tolist()
    )
    n_atipicas = int(ops["flag_valor_atipico"].sum())

    gatilhos = []
    if dias_frac:
        gatilhos.append(
            f"REGRA DE FRACIONAMENTO acionada nos dias: {', '.join(dias_frac)}"
        )
    if n_atipicas:
        gatilhos.append(
            f"REGRA DE VALOR ATIPICO acionada em {n_atipicas} operacao(oes)"
        )

    return f"""CASO PARA TRIAGEM

Cliente: {cliente_id}
Total de operacoes no periodo: {len(ops)}
Volume total movimentado: R$ {ops['valor_brl'].sum():,.2f}

Sinalizacao recebida:
{chr(10).join('- ' + g for g in gatilhos)}

Investigue com as ferramentas o que for necessario para formar juizo sobre este caso e produza o parecer."""


def analisar_cliente(cliente_id: str, df=None, verbose: bool = True) -> ResultadoAgente:
    """Roda o agente sobre um cliente e devolve parecer + trajetoria.

    Nunca levanta excecao: no lote, um cliente problematico nao pode
    derrubar os outros nove. Falhas viram status registrado.
    """
    if df is None:
        df = tools.carregar_base()

    res = ResultadoAgente(cliente_id=cliente_id, status="sem_conclusao")
    cliente = Groq(api_key=os.getenv("GROQ_API_KEY"))

    mensagens = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _contexto_do_caso(df, cliente_id)},
    ]

    inicio = time.perf_counter()

    concluiu = False

    for iteracao in range(1, MAX_ITERACOES + 1):
        res.n_iteracoes = iteracao

        # Respeita o teto de tokens/minuto do free tier
        if iteracao > 1 or res.tokens_entrada:
            time.sleep(llm.INTERVALO_MINIMO_S)

        try:
            resp = cliente.chat.completions.create(
                model=MODELO,
                messages=mensagens,
                tools=tools.FERRAMENTAS_SCHEMA,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=3000,
            )
        except Exception as e:
            # O gpt-oss-120b as vezes emite o parecer como chamada de uma
            # ferramenta inexistente chamada "JSON" em vez de usar
            # emitir_parecer. O Groq rejeita com 400, MAS devolve o que o
            # modelo tentou gerar no campo failed_generation — e ali dentro
            # esta o parecer completo e bem formado.
            #
            # Recuperar dai nao e gambiarra: o modelo concluiu a analise
            # corretamente e so errou o envelope. Descartar o trabalho por
            # causa do envelope seria perder conteudo valido. A saida
            # recuperada passa pelo MESMO validador, entao o vocabulario
            # fechado e a regra de coerencia continuam valendo — nada entra
            # sem validacao.
            recuperado = _recuperar_de_erro(str(e))
            if recuperado:
                parecer, erro = llm.validar(recuperado, llm.ParecerPLD)
                if parecer:
                    res.status = "ok_recuperado"
                    res.nivel_risco = parecer.nivel_risco
                    res.tipologia_suspeita = parecer.tipologia_suspeita
                    res.red_flags = parecer.red_flags
                    res.justificativa = parecer.justificativa
                    res.erro = "parecer recuperado de failed_generation (tool_use_failed)"
                    break
            res.status = "erro_api"
            res.erro = str(e)[:300]
            break

        res.tokens_entrada += resp.usage.prompt_tokens
        res.tokens_saida += resp.usage.completion_tokens
        msg = resp.choices[0].message

        # O modelo pediu ferramenta: executa e devolve o resultado
        if msg.tool_calls:
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

                if verbose:
                    print(f"    [{iteracao}] {nome}({args if nome != 'emitir_parecer' else '...'})")

                # emitir_parecer nao consulta a base: encerra o caso.
                # Passa pelo mesmo validador do Nivel 1, entao o vocabulario
                # fechado e a regra de coerencia continuam valendo.
                if nome == "emitir_parecer":
                    parecer, erro = llm.validar(
                        json.dumps(args, ensure_ascii=False), llm.ParecerPLD
                    )
                    if parecer:
                        res.status = "ok"
                        res.nivel_risco = parecer.nivel_risco
                        res.tipologia_suspeita = parecer.tipologia_suspeita
                        res.red_flags = parecer.red_flags
                        res.justificativa = parecer.justificativa
                    else:
                        res.status = "falha_validacao"
                        res.erro = erro
                    res.trajetoria.append(
                        {"iteracao": iteracao, "ferramenta": nome, "argumentos": {}}
                    )
                    concluiu = True
                    break

                funcao = tools.REGISTRO.get(nome)
                if funcao is None:
                    saida = {"erro": f"ferramenta desconhecida: {nome}"}
                else:
                    try:
                        saida = funcao(**args)
                    except Exception as e:
                        saida = {"erro": str(e)[:200]}

                res.ferramentas_chamadas.append(nome)
                res.trajetoria.append(
                    {"iteracao": iteracao, "ferramenta": nome, "argumentos": args}
                )
                mensagens.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(saida, ensure_ascii=False, default=str),
                    }
                )

            if concluiu:
                break
            continue

        # Sem tool call: e a conclusao
        parecer, erro = llm.validar(msg.content or "", llm.ParecerPLD)
        if parecer:
            res.status = "ok"
            res.nivel_risco = parecer.nivel_risco
            res.tipologia_suspeita = parecer.tipologia_suspeita
            res.red_flags = parecer.red_flags
            res.justificativa = parecer.justificativa
        else:
            res.status = "falha_validacao"
            res.erro = erro
        break
    else:
        res.status = "sem_conclusao"
        res.erro = f"nao concluiu em {MAX_ITERACOES} iteracoes"

    res.latencia_s = time.perf_counter() - inicio
    res.custo_estimado_usd = round(
        res.tokens_entrada / 1e6 * llm.PRECO_USD_POR_MILHAO["entrada"]
        + res.tokens_saida / 1e6 * llm.PRECO_USD_POR_MILHAO["saida"],
        6,
    )
    return res


def rodar_lote(top: int = 10, verbose: bool = True) -> list[ResultadoAgente]:
    """Roda o agente sobre os N clientes mais sinalizados e salva em outputs/."""
    import pandas as pd

    df = tools.carregar_base()
    ranking = p.ranking_clientes(df, top=top)
    clientes = ranking.index.tolist()

    print(f"Lote sobre {len(clientes)} clientes: {', '.join(clientes)}\n")

    resultados = []
    for i, cid in enumerate(clientes, 1):
        print(f"[{i}/{len(clientes)}] {cid}")
        r = analisar_cliente(cid, df, verbose=verbose)
        print(
            f"    -> {r.status} | risco={r.nivel_risco} | "
            f"{len(r.ferramentas_chamadas)} chamada(s) | {r.latencia_s:.1f}s\n"
        )
        resultados.append(r)

    saida = Path(__file__).resolve().parent.parent / "outputs"
    saida.mkdir(exist_ok=True)
    (saida / "pareceres").mkdir(exist_ok=True)

    for r in resultados:
        destino = saida / "pareceres" / f"{r.cliente_id}.json"
        # Nao sobrescreve parecer valido com resultado pior. Se a execucao
        # anterior concluiu e esta falhou (rate limit, erro de envelope),
        # o registro bom permanece — perder analise ja feita por causa de
        # uma falha de infraestrutura seria desperdicio.
        if destino.exists() and not r.status.startswith("ok"):
            anterior = json.loads(destino.read_text(encoding="utf-8"))
            if str(anterior.get("status", "")).startswith("ok"):
                print(f"    (mantido parecer anterior de {r.cliente_id})")
                continue
        destino.write_text(
            json.dumps(asdict(r), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    tabela = pd.DataFrame([r.para_linha() for r in resultados])
    tabela.to_csv(saida / "lote.csv", index=False, encoding="utf-8")

    print("=" * 72)
    print(tabela.to_string(index=False))
    print("=" * 72)
    print(f"\nTokens totais : {tabela.tokens_entrada.sum():,} entrada + "
          f"{tabela.tokens_saida.sum():,} saida")
    print(f"Custo estimado: US$ {tabela.custo_estimado_usd.sum():.4f}")
    print(f"Latencia      : media {tabela.latencia_s.mean():.1f}s | "
          f"p95 {tabela.latencia_s.quantile(0.95):.1f}s | "
          f"total {tabela.latencia_s.sum():.0f}s")
    print(f"\nSalvo em outputs/lote.csv e outputs/pareceres/")

    return resultados


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "lote":
        r = analisar_cliente(sys.argv[1])
        print(json.dumps(asdict(r), indent=2, ensure_ascii=False))
    else:
        rodar_lote()
