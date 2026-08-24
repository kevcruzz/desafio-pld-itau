"""
Confronto entre o que as REGRAS deterministicas apontaram e o que o
AGENTE concluiu, cliente a cliente.

Nao chama LLM: le os pareceres ja salvos em outputs/pareceres/ e as
flags recalculadas pelo pipeline. Roda offline e e reproduzivel.

--------------------------------------------------------------------
O CRITERIO DE CORRESPONDENCIA, E POR QUE NAO USEI O DO ENUNCIADO
--------------------------------------------------------------------

O enunciado sugere, como exemplo, que "cliente sinalizado pelas duas
regras deveria sair como risco alto". Testei: nesta base esse conjunto e
VAZIO. Nenhum dos 30 clientes e capturado pelas duas regras ao mesmo
tempo, e isso nao e coincidencia — as regras procuram fenomenos opostos.
Fracionamento procura MUITOS valores medios concentrados num dia; valor
atipico procura UM valor destoante do historico. Um cliente que dispara
uma tende a nao disparar a outra.

Com o conjunto vazio, o criterio do exemplo nao compara nada. Precisei
de outro, e escolhi um graduado:

    Regra 1 acionada (fracionamento)     -> esperado ALTO
    Regra 2 com 2+ operacoes atipicas    -> esperado ALTO
    Regra 2 com 1 operacao atipica       -> esperado MEDIO
    Nenhuma regra acionada               -> esperado BAIXO

A assimetria e deliberada. Fracionamento e PADRAO DE CONDUTA: exige
varias operacoes coordenadas, deliberadamente abaixo de um limite. Valor
atipico isolado e EVENTO: pode ser venda de bem, rescisao, aporte,
comercio exterior. Tratar os dois como equivalentes confundiria conduta
com acontecimento, que e o erro conceitual que a triagem existe para
evitar.

Reporto duas metricas. A concordancia EXATA e a mais dura. A ADJACENTE
(errar por um nivel conta como acerto parcial) e mais informativa aqui,
porque a fronteira entre medio e alto e de julgamento, nao de fato —
dois analistas humanos divergiriam nela com frequencia.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nivel_1"))

import pipeline as p

RAIZ = Path(__file__).resolve().parent.parent
OUTPUTS = RAIZ / "outputs"
PARECERES = OUTPUTS / "pareceres"

ORDEM = {"baixo": 0, "medio": 1, "alto": 2}


def risco_esperado(flags_r1: int, flags_r2: int) -> str:
    """Traduz o sinal das regras no nivel de risco que elas implicam."""
    if flags_r1 > 0:
        return "alto"
    if flags_r2 >= 2:
        return "alto"
    if flags_r2 == 1:
        return "medio"
    return "baixo"


def carregar_pareceres() -> dict[str, dict]:
    """Le os pareceres salvos pelo lote, ignorando os que falharam."""
    if not PARECERES.exists():
        raise SystemExit(
            f"{PARECERES} nao existe. Rode primeiro: python agente.py lote"
        )

    pareceres = {}
    ignorados = []
    for arq in sorted(PARECERES.glob("*.json")):
        dado = json.loads(arq.read_text(encoding="utf-8"))
        if dado.get("status", "").startswith("ok") and dado.get("nivel_risco"):
            pareceres[dado["cliente_id"]] = dado
        else:
            ignorados.append((dado.get("cliente_id", arq.stem), dado.get("status")))

    if ignorados:
        print("Pareceres sem conclusao, fora do confronto:")
        for cid, st in ignorados:
            print(f"  - {cid}: {st}")
        print()
    return pareceres


def montar_confronto(caminho_base: str = "../dados/dados_nivel_2.json") -> pd.DataFrame:
    """Cruza regras e pareceres num DataFrame."""
    df, _, _ = p.processar(caminho_base)
    ranking = p.ranking_clientes(df, top=None)
    pareceres = carregar_pareceres()

    if not pareceres:
        raise SystemExit(
            "Nenhum parecer concluido em outputs/pareceres/. "
            "Rode o lote antes do confronto."
        )

    linhas = []
    for cid, par in pareceres.items():
        r1 = int(ranking.loc[cid, "flags_regra1"])
        r2 = int(ranking.loc[cid, "flags_regra2"])
        esperado = risco_esperado(r1, r2)
        atribuido = par["nivel_risco"]
        distancia = ORDEM[atribuido] - ORDEM[esperado]

        linhas.append(
            {
                "cliente_id": cid,
                "flags_regra1": r1,
                "flags_regra2": r2,
                "volume_brl": float(ranking.loc[cid, "volume_brl"]),
                "n_operacoes": int(ranking.loc[cid, "n_operacoes"]),
                "risco_esperado_pelas_regras": esperado,
                "risco_atribuido_pelo_agente": atribuido,
                "tipologia": par.get("tipologia_suspeita"),
                "n_red_flags": len(par.get("red_flags") or []),
                "concorda": esperado == atribuido,
                "distancia": distancia,
                "adjacente": abs(distancia) <= 1,
                "direcao": (
                    "igual" if distancia == 0
                    else "agente MAIS brando" if distancia < 0
                    else "agente MAIS severo"
                ),
                "ferramentas": " > ".join(par.get("ferramentas_chamadas") or []),
            }
        )

    return pd.DataFrame(linhas).sort_values(
        ["concorda", "distancia", "cliente_id"]
    ).reset_index(drop=True)


def matriz_confusao(conf: pd.DataFrame) -> pd.DataFrame:
    """Esperado (linhas) x atribuido (colunas)."""
    niveis = ["baixo", "medio", "alto"]
    m = pd.crosstab(
        conf["risco_esperado_pelas_regras"],
        conf["risco_atribuido_pelo_agente"],
    )
    return m.reindex(index=niveis, columns=niveis, fill_value=0)


def relatorio(conf: pd.DataFrame) -> str:
    """Monta o relatorio em markdown para salvar em outputs/."""
    n = len(conf)
    exata = conf["concorda"].sum()
    adj = conf["adjacente"].sum()
    brandos = conf[conf["distancia"] < 0]
    severos = conf[conf["distancia"] > 0]

    linhas = [
        "# Confronto — regras deterministicas x parecer do agente",
        "",
        f"Casos comparados: **{n}**",
        "",
        "## Criterio de correspondencia",
        "",
        "| Sinal das regras | Risco esperado |",
        "|---|---|",
        "| Regra 1 (fracionamento) acionada | alto |",
        "| Regra 2 com 2+ operacoes atipicas | alto |",
        "| Regra 2 com 1 operacao atipica | medio |",
        "| Nenhuma regra acionada | baixo |",
        "",
        "O criterio sugerido no enunciado (cliente pego pelas DUAS regras) produz "
        "conjunto vazio nesta base — nenhum dos 30 clientes dispara ambas. As regras "
        "procuram fenomenos opostos: fracionamento busca varios valores medios "
        "concentrados; valor atipico busca um valor destoante. Por isso adotei o "
        "criterio graduado acima, que trata fracionamento (padrao de conduta) como "
        "mais grave que valor atipico isolado (evento).",
        "",
        "## Metricas",
        "",
        f"- Concordancia exata: **{exata}/{n} ({exata / n * 100:.0f}%)**",
        f"- Concordancia adjacente (erro de ate um nivel): **{adj}/{n} ({adj / n * 100:.0f}%)**",
        f"- Agente mais brando que a regra: **{len(brandos)}**",
        f"- Agente mais severo que a regra: **{len(severos)}**",
        "",
        "## Matriz de confusao",
        "",
        "Linhas = esperado pelas regras, colunas = atribuido pelo agente.",
        "",
        matriz_confusao(conf).to_markdown(),
        "",
        "## Caso a caso",
        "",
        conf[
            [
                "cliente_id",
                "flags_regra1",
                "flags_regra2",
                "risco_esperado_pelas_regras",
                "risco_atribuido_pelo_agente",
                "direcao",
                "n_red_flags",
                "ferramentas",
            ]
        ].to_markdown(index=False),
        "",
        "## Divergencias",
        "",
    ]

    if brandos.empty and severos.empty:
        linhas.append("Nenhuma divergencia nesta execucao.")
    else:
        linhas.append(
            "A analise de cada divergencia esta em `docs/DECISOES.md`. "
            "O ponto de partida: as regras sao propositalmente simples e "
            "geram falsos positivos, entao divergencia nao e erro do agente "
            "por definicao — em varios casos e o agente que esta certo."
        )
        linhas.append("")
        for _, r in pd.concat([brandos, severos]).iterrows():
            par = json.loads(
                (PARECERES / f"{r.cliente_id}.json").read_text(encoding="utf-8")
            )
            linhas += [
                f"### {r.cliente_id} — regra diz *{r.risco_esperado_pelas_regras}*, "
                f"agente diz *{r.risco_atribuido_pelo_agente}* ({r.direcao})",
                "",
                f"- Sinalizacoes: Regra 1 = {r.flags_regra1}, Regra 2 = {r.flags_regra2}",
                f"- Volume: R$ {r.volume_brl:,.2f} em {r.n_operacoes} operacoes",
                f"- Ferramentas consultadas: {r.ferramentas or '(nenhuma)'}",
                f"- Tipologia atribuida: {r.tipologia}",
                "",
                f"> {par.get('justificativa', '')}",
                "",
            ]

    return "\n".join(linhas)


def main() -> None:
    conf = montar_confronto()

    print("=" * 78)
    print("CONFRONTO — REGRAS x AGENTE")
    print("=" * 78)
    print(
        conf[
            [
                "cliente_id",
                "flags_regra1",
                "flags_regra2",
                "risco_esperado_pelas_regras",
                "risco_atribuido_pelo_agente",
                "direcao",
            ]
        ].to_string(index=False)
    )

    n = len(conf)
    print()
    print(f"Concordancia exata     : {conf['concorda'].sum()}/{n} "
          f"({conf['concorda'].sum() / n * 100:.0f}%)")
    print(f"Concordancia adjacente : {conf['adjacente'].sum()}/{n} "
          f"({conf['adjacente'].sum() / n * 100:.0f}%)")
    print(f"Agente mais brando     : {(conf['distancia'] < 0).sum()}")
    print(f"Agente mais severo     : {(conf['distancia'] > 0).sum()}")
    print()
    print("Matriz de confusao (linha=regra, coluna=agente):")
    print(matriz_confusao(conf).to_string())

    OUTPUTS.mkdir(exist_ok=True)
    conf.to_csv(OUTPUTS / "confronto.csv", index=False, encoding="utf-8")
    (OUTPUTS / "confronto_analise.md").write_text(relatorio(conf), encoding="utf-8")
    print("\nSalvo em outputs/confronto.csv e outputs/confronto_analise.md")


if __name__ == "__main__":
    main()
