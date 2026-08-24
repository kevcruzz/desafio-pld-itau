"""
Ferramentas que o agente pode consultar sobre a base de operacoes.

Tres decisoes de desenho que valem explicar:

1. **As docstrings sao parte da interface, nao documentacao.** Em tool
   calling, o texto abaixo de cada funcao e literalmente o que o modelo
   le para decidir se aquela ferramenta serve para o caso na mesa.
   Docstring vaga produz agente que chama tudo — e chamar tudo sempre e
   script, nao agente. Por isso cada uma diz explicitamente QUANDO usar
   e o que NAO responde.

2. **Retornam dict serializavel, nunca DataFrame.** O retorno vai virar
   JSON dentro da conversa com o modelo.

3. **Nenhuma ferramenta calcula na hora da chamada.** Tudo sai do
   DataFrame ja tratado pelo pipeline do Nivel 1. A ferramenta recorta e
   agrega; a regra ja rodou antes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nivel_1'))

import pipeline as p

_BASE: pd.DataFrame | None = None


def carregar_base(caminho: str | Path = "../dados/dados_nivel_2.json") -> pd.DataFrame:
    """Carrega e trata a base uma unica vez, reaproveitando entre chamadas.

    Cache de modulo: o lote roda 10 clientes e o agente faz varias
    chamadas por cliente. Reprocessar 322 linhas a cada consulta seria
    desperdicio, e — mais importante — arriscaria inconsistencia se o
    tratamento variasse entre chamadas dentro do mesmo caso.
    """
    global _BASE
    if _BASE is None:
        _BASE, _, _ = p.processar(caminho)
    return _BASE


def historico_cliente(cliente_id: str) -> dict:
    """Resumo agregado de TODAS as operacoes do cliente: volume total,
    quantidade, ticket medio e mediano, maior e menor operacao, periodo
    coberto, distribuicao por tipo, contrapartes recorrentes e quais
    regras foram acionadas.

    USE quando precisar entender o perfil geral do cliente ou avaliar se
    uma operacao especifica destoa do padrao dele. E a ferramenta certa
    para casos de valor atipico, porque so o historico diz se o valor e
    anomalo PARA AQUELE CLIENTE.

    NAO responde o que aconteceu num dia especifico — para isso use
    operacoes_do_dia.
    """
    df = carregar_base()
    return p.dossie_cliente(df, cliente_id)


def operacoes_do_dia(cliente_id: str, data: str) -> dict:
    """Recorte detalhado de UM dia: lista de cada operacao daquela data
    com valor, canal, tipo e contraparte, mais soma do dia, maior
    operacao e canais utilizados.

    USE quando o caso envolver concentracao temporal — fracionamento,
    varias operacoes na mesma data, suspeita de estruturacao. E a
    ferramenta certa para inspecionar um dia que a regra de fracionamento
    sinalizou, porque revela se as operacoes foram para a mesma
    contraparte ou pulverizadas.

    A data deve vir no formato YYYY-MM-DD. As datas relevantes de um
    cliente aparecem em historico_cliente, no campo
    regras_acionadas.fracionamento.dias.
    """
    df = carregar_base()
    ops = df[(df["cliente_id"] == cliente_id) & (df["data"] == data)]

    if ops.empty:
        return {
            "cliente_id": cliente_id,
            "data": data,
            "n_operacoes": 0,
            "aviso": "nenhuma operacao encontrada para este cliente nesta data",
        }

    return {
        "cliente_id": cliente_id,
        "data": data,
        "n_operacoes": int(len(ops)),
        "soma_do_dia_brl": round(float(ops["valor_brl"].sum()), 2),
        "maior_operacao_brl": round(float(ops["valor_brl"].max()), 2),
        "menor_operacao_brl": round(float(ops["valor_brl"].min()), 2),
        "canais_utilizados": sorted(ops["canal"].unique().tolist()),
        "contrapartes_distintas": int(ops["contraparte"].nunique()),
        "operacoes": [
            {
                "id": r["id"],
                "valor_brl": round(float(r["valor_brl"]), 2),
                "moeda_original": r["moeda"],
                "canal": r["canal"],
                "tipo": r["tipo"],
                "contraparte": r["contraparte"],
            }
            for _, r in ops.sort_values("valor_brl", ascending=False).iterrows()
        ],
    }


def perfil_canal(cliente_id: str) -> dict:
    """Distribuicao das operacoes do cliente por canal (pix, ted, boleto,
    cartao, especie), em quantidade e em volume, com percentual de cada
    um e comparacao contra a media da base inteira.

    USE quando desconfiar de concentracao em canal de maior risco —
    especialmente especie, que e o canal classico de insercao de recursos
    de origem nao rastreavel. O percentual do cliente sozinho diz pouco;
    o que informa e o desvio em relacao ao comportamento tipico da base,
    e este retorno ja traz a comparacao pronta.

    NAO responde sobre valores individuais nem sobre datas.
    """
    df = carregar_base()
    ops = df[df["cliente_id"] == cliente_id]

    if ops.empty:
        return {"cliente_id": cliente_id, "aviso": "cliente nao encontrado"}

    total_ops = len(ops)
    total_vol = float(ops["valor_brl"].sum())
    base_pct = (df["canal"].value_counts(normalize=True) * 100).round(1)

    distribuicao = {}
    for canal in sorted(ops["canal"].unique()):
        recorte = ops[ops["canal"] == canal]
        pct_cliente = round(len(recorte) / total_ops * 100, 1)
        pct_base = float(base_pct.get(canal, 0.0))
        distribuicao[canal] = {
            "n_operacoes": int(len(recorte)),
            "pct_das_operacoes": pct_cliente,
            "volume_brl": round(float(recorte["valor_brl"].sum()), 2),
            "pct_do_volume": round(float(recorte["valor_brl"].sum()) / total_vol * 100, 1),
            "pct_medio_na_base": pct_base,
            "desvio_pp_vs_base": round(pct_cliente - pct_base, 1),
        }

    return {
        "cliente_id": cliente_id,
        "n_operacoes": total_ops,
        "canais_utilizados": len(distribuicao),
        "distribuicao": distribuicao,
        "nota": (
            "desvio_pp_vs_base e a diferenca em pontos percentuais entre o uso "
            "do canal por este cliente e a media da base"
        ),
    }


# Esquema no formato OpenAI/Groq. Gerado a partir das docstrings acima —
# quem edita a docstring precisa editar aqui tambem, e vice-versa.
FERRAMENTAS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "historico_cliente",
            "description": historico_cliente.__doc__,
            "parameters": {
                "type": "object",
                "properties": {
                    "cliente_id": {
                        "type": "string",
                        "description": "Identificador do cliente, ex: CLI-014",
                    }
                },
                "required": ["cliente_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "operacoes_do_dia",
            "description": operacoes_do_dia.__doc__,
            "parameters": {
                "type": "object",
                "properties": {
                    "cliente_id": {
                        "type": "string",
                        "description": "Identificador do cliente, ex: CLI-029",
                    },
                    "data": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD, ex: 2026-05-26",
                    },
                },
                "required": ["cliente_id", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "perfil_canal",
            "description": perfil_canal.__doc__,
            "parameters": {
                "type": "object",
                "properties": {
                    "cliente_id": {
                        "type": "string",
                        "description": "Identificador do cliente, ex: CLI-005",
                    }
                },
                "required": ["cliente_id"],
            },
        },
    },
]

REGISTRO = {
    "historico_cliente": historico_cliente,
    "operacoes_do_dia": operacoes_do_dia,
    "perfil_canal": perfil_canal,
}


if __name__ == "__main__":
    import json

    print("=== historico_cliente(CLI-029) ===")
    print(json.dumps(historico_cliente("CLI-029"), indent=2, ensure_ascii=False)[:800])
    print("\n=== operacoes_do_dia(CLI-029, 2026-05-26) ===")
    print(json.dumps(operacoes_do_dia("CLI-029", "2026-05-26"), indent=2, ensure_ascii=False))
    print("\n=== perfil_canal(CLI-014) ===")
    print(json.dumps(perfil_canal("CLI-014"), indent=2, ensure_ascii=False))
