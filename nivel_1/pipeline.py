"""
Tratamento de dados e regras determinísticas de triagem PLD.

Vive em nivel_1/ porque é o Nível 1 que define o tratamento; o Nível 2
importa este mesmo módulo e roda sobre a base maior sem reescrever nada.

Princípio que atravessa o arquivo: tudo aqui é CÁLCULO (soma, mediana,
contagem, comparação com limite). Nenhuma função deste módulo fala com
LLM. A LLM entra depois, e só para interpretar e redigir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# Limiares das regras. Ficam aqui em cima, nomeados, porque são política
# de compliance e não constante de código — num sistema real viriam de
# configuração e mudariam sem deploy.
MIN_OPS_FRACIONAMENTO = 3
LIMITE_SOMA_FRACIONAMENTO = 50_000.0
TETO_OPERACAO_FRACIONAMENTO = 20_000.0

FATOR_VALOR_ATIPICO = 5
MIN_OPS_VALOR_ATIPICO = 4


# --------------------------------------------------------------------------
# Carga
# --------------------------------------------------------------------------

def carregar(caminho: str | Path) -> tuple[pd.DataFrame, float]:
    """Lê o JSON e devolve (operações cruas, taxa de câmbio do arquivo).

    A taxa vem de dentro do arquivo por exigência do enunciado — nada de
    consultar cotação externa.
    """
    with open(caminho, encoding="utf-8") as f:
        bruto = json.load(f)
    return pd.DataFrame(bruto["operacoes"]), float(bruto["taxa_cambio_usd_brl"])


# --------------------------------------------------------------------------
# Diagnóstico (antes de tratar, olhe)
# --------------------------------------------------------------------------

@dataclass
class Diagnostico:
    """Fotografia dos problemas de qualidade encontrados na base crua."""
    linhas: int
    clientes: int
    duplicatas_exatas: int
    ids_repetidos: list[str] = field(default_factory=list)
    ids_repetidos_com_conteudo_divergente: list[str] = field(default_factory=list)
    datas_nulas: int = 0
    moedas: dict = field(default_factory=dict)
    valores_nao_positivos: int = 0
    datas_fora_do_iso: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        linhas = [
            f"Linhas: {self.linhas} | Clientes: {self.clientes}",
            f"Duplicatas exatas (todos os campos iguais): {self.duplicatas_exatas}",
            f"IDs repetidos: {len(self.ids_repetidos)} -> {self.ids_repetidos}",
            f"  ...com conteúdo divergente: {self.ids_repetidos_com_conteudo_divergente or 'nenhum'}",
            f"Operações com data nula: {self.datas_nulas}",
            f"Moedas presentes: {self.moedas}",
            f"Valores <= 0: {self.valores_nao_positivos}",
            f"Datas fora do padrão ISO: {self.datas_fora_do_iso or 'nenhuma'}",
        ]
        return "\n".join(linhas)


def diagnosticar(df: pd.DataFrame) -> Diagnostico:
    """Inspeciona a base crua sem alterá-la.

    Separado de `limpar` de propósito: o notebook precisa mostrar o
    'antes' para justificar o 'depois'.

    A distinção entre ID repetido com conteúdo idêntico e ID repetido com
    conteúdo divergente é o que decide o tratamento: o primeiro é reenvio
    do sistema legado (some), o segundo seria conflito de integridade
    (não dá para escolher sozinho qual linha vale — vira exceção manual).
    """
    ids_repetidos = df["id"][df["id"].duplicated()].unique().tolist()

    divergentes = []
    for id_op in ids_repetidos:
        bloco = df[df["id"] == id_op]
        if len(bloco.drop_duplicates()) > 1:
            divergentes.append(id_op)

    datas = df["data"].dropna()
    fora_iso = datas[~datas.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}$")].unique().tolist()

    return Diagnostico(
        linhas=len(df),
        clientes=df["cliente_id"].nunique(),
        duplicatas_exatas=int(df.duplicated().sum()),
        ids_repetidos=ids_repetidos,
        ids_repetidos_com_conteudo_divergente=divergentes,
        datas_nulas=int(df["data"].isna().sum()),
        moedas=df["moeda"].value_counts().to_dict(),
        valores_nao_positivos=int((df["valor"] <= 0).sum()),
        datas_fora_do_iso=fora_iso,
    )


# --------------------------------------------------------------------------
# Limpeza
# --------------------------------------------------------------------------

def limpar(df: pd.DataFrame, taxa_usd_brl: float) -> pd.DataFrame:
    """Aplica o tratamento e devolve o DataFrame pronto para as regras.

    Ordem importa e não é arbitrária:

    1. Deduplicar PRIMEIRO. Se a mediana for calculada antes, a linha
       repetida entra duas vezes na distribuição e desloca o resultado
       da Regra 2 — isso muda o status de um cliente real na base grande.
    2. Converter para BRL DEPOIS. Comparar valor com limite em moedas
       diferentes é comparar coisas diferentes; sem isso a Regra 2 deixa
       de sinalizar operações que são exatamente as maiores da base.
    3. Datar por último, com errors='coerce'. As linhas sem data viram
       NaT e continuam no DataFrame — ver `NOTA` abaixo.

    NOTA sobre `data` nula: a linha é MANTIDA, não descartada.
    Descartar removeria operação legítima do volume do cliente e do
    denominador da Regra 2 (subnotifica risco). Imputar uma data
    fabricaria evidência de fracionamento que não existe — inaceitável
    em PLD. A saída é manter o registro e excluí-lo apenas das análises
    que dependem de data, o que a Regra 1 faz explicitamente.
    """
    limpo = df.drop_duplicates().copy()

    limpo["valor_brl"] = limpo["valor"].where(
        limpo["moeda"] != "USD",
        limpo["valor"] * taxa_usd_brl,
    )
    limpo["data_dt"] = pd.to_datetime(limpo["data"], errors="coerce")
    limpo["sem_data"] = limpo["data_dt"].isna()

    return limpo.reset_index(drop=True)


# --------------------------------------------------------------------------
# Agregações
# --------------------------------------------------------------------------

def volume_por_cliente(df: pd.DataFrame) -> pd.DataFrame:
    """Volume total transacionado por cliente, em BRL.

    Decisão: soma ABSOLUTA, sem sinal por direção do fluxo. Em PLD o que
    interessa é giro (quanto passou pela conta), não saldo líquido —
    entra 100k e sai 100k é justamente o padrão que se quer enxergar, e
    o líquido zeraria esse caso.
    """
    agg = (
        df.groupby("cliente_id")
        .agg(
            volume_brl=("valor_brl", "sum"),
            n_operacoes=("valor_brl", "size"),
            ticket_mediano=("valor_brl", "median"),
        )
        .sort_values("volume_brl", ascending=False)
    )
    return agg.round(2)


def operacoes_por_canal(df: pd.DataFrame) -> pd.DataFrame:
    """Quantidade de operações por canal, com volume junto."""
    agg = (
        df.groupby("canal")
        .agg(n_operacoes=("valor_brl", "size"), volume_brl=("valor_brl", "sum"))
        .sort_values("n_operacoes", ascending=False)
    )
    agg["pct_operacoes"] = (agg["n_operacoes"] / agg["n_operacoes"].sum() * 100).round(1)
    return agg.round(2)


# --------------------------------------------------------------------------
# Regra 1 — Fracionamento
# --------------------------------------------------------------------------

def grupos_cliente_data(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa por cliente+data e avalia cada condição da Regra 1 em coluna
    separada.

    Existe para a célula de validação: mostrar as três condições isoladas
    prova QUAL condição barrou cada caso quase-positivo, em vez de só
    mostrar um booleano final.
    """
    base = df[~df["sem_data"]]
    g = base.groupby(["cliente_id", "data_dt"]).agg(
        n_operacoes=("valor_brl", "size"),
        soma_brl=("valor_brl", "sum"),
        maior_operacao=("valor_brl", "max"),
    )

    # Fronteiras estritas, lidas do enunciado:
    #   "soma ULTRAPASSA 50.000"          -> > , não >=
    #   "nenhuma operação ATINGE 20.000"  -> < , não <=
    g["cond_3_ou_mais_ops"] = g["n_operacoes"] >= MIN_OPS_FRACIONAMENTO
    g["cond_soma_acima_50k"] = g["soma_brl"] > LIMITE_SOMA_FRACIONAMENTO
    g["cond_nenhuma_atinge_20k"] = g["maior_operacao"] < TETO_OPERACAO_FRACIONAMENTO
    g["fracionamento"] = (
        g["cond_3_ou_mais_ops"]
        & g["cond_soma_acima_50k"]
        & g["cond_nenhuma_atinge_20k"]
    )
    return g.round(2)


def regra_fracionamento(df: pd.DataFrame) -> pd.Series:
    """Flag por OPERAÇÃO: True se ela pertence a um par cliente+data
    que caracteriza fracionamento.

    A regra sinaliza o CLIENTE (é o que o enunciado pede), mas a flag
    volta no nível da operação para que o DataFrame continue sendo a
    fonte única e dê para rastrear quais lançamentos formaram o alerta.
    """
    g = grupos_cliente_data(df)
    pares_suspeitos = set(g[g["fracionamento"]].index)

    return pd.Series(
        [
            (not sem_data) and (cid, dt) in pares_suspeitos
            for cid, dt, sem_data in zip(df["cliente_id"], df["data_dt"], df["sem_data"])
        ],
        index=df.index,
        name="flag_fracionamento",
    )


# --------------------------------------------------------------------------
# Regra 2 — Valor atípico
# --------------------------------------------------------------------------

def regra_valor_atipico(df: pd.DataFrame) -> pd.Series:
    """Flag por OPERAÇÃO: valor em BRL acima de 5x a mediana do cliente.

    Só se aplica a clientes com 4+ operações — abaixo disso a mediana é
    instável demais para servir de referência. Note que a contagem é
    feita APÓS a limpeza: uma duplicata a mais podia empurrar um cliente
    de 3 para 4 operações e fazê-lo entrar na regra sem motivo.

    A mediana inclui a própria operação testada. É a leitura mais direta
    do enunciado ("a mediana dos valores daquele mesmo cliente") e a mais
    conservadora: incluir o outlier puxa a mediana para cima, então a
    regra dispara menos, não mais.

    Linhas sem data PERMANECEM no cálculo — a Regra 2 não é temporal e
    excluí-las mudaria a mediana e a contagem sem justificativa.
    """
    n_ops = df.groupby("cliente_id")["valor_brl"].transform("size")
    mediana = df.groupby("cliente_id")["valor_brl"].transform("median")

    flag = (n_ops >= MIN_OPS_VALOR_ATIPICO) & (
        df["valor_brl"] > FATOR_VALOR_ATIPICO * mediana
    )
    return flag.rename("flag_valor_atipico")


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def aplicar_regras(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona as duas flags ao DataFrame."""
    out = df.copy()
    out["flag_fracionamento"] = regra_fracionamento(out)
    out["flag_valor_atipico"] = regra_valor_atipico(out)
    out["flag_qualquer"] = out["flag_fracionamento"] | out["flag_valor_atipico"]
    return out


def ranking_clientes(df: pd.DataFrame, top: int | None = 10) -> pd.DataFrame:
    """Clientes ordenados por nº de sinalizações, desempate por volume.

    Definição de 'nº de sinalizações' (decisão nossa, o enunciado não
    fecha): dias distintos flagrados pela Regra 1 + operações flagradas
    pela Regra 2. Contar por dia na Regra 1 evita que um cliente com 6
    operações num único dia pareça 6x mais suspeito que um com 3 — é um
    evento de fracionamento, não seis.
    """
    dias_r1 = (
        df[df["flag_fracionamento"]]
        .groupby("cliente_id")["data_dt"]
        .nunique()
        .rename("flags_regra1")
    )
    ops_r2 = (
        df.groupby("cliente_id")["flag_valor_atipico"].sum().rename("flags_regra2")
    )
    volume = df.groupby("cliente_id")["valor_brl"].sum().rename("volume_brl")
    n_ops = df.groupby("cliente_id").size().rename("n_operacoes")

    rank = pd.concat([dias_r1, ops_r2, volume, n_ops], axis=1).fillna(0)
    rank["flags_regra1"] = rank["flags_regra1"].astype(int)
    rank["flags_regra2"] = rank["flags_regra2"].astype(int)
    rank["total_flags"] = rank["flags_regra1"] + rank["flags_regra2"]

    rank = rank.sort_values(["total_flags", "volume_brl"], ascending=[False, False])
    rank = rank.round(2)
    return rank.head(top) if top else rank


def processar(caminho: str | Path) -> tuple[pd.DataFrame, Diagnostico, float]:
    """Atalho do fluxo completo: carregar -> diagnosticar -> limpar -> regras.

    É esta função que o Nível 2 chama trocando só o caminho do arquivo.
    """
    bruto, taxa = carregar(caminho)
    diag = diagnosticar(bruto)
    limpo = limpar(bruto, taxa)
    return aplicar_regras(limpo), diag, taxa


# --------------------------------------------------------------------------
# Dossiê do cliente — insumo pré-calculado para a LLM
# --------------------------------------------------------------------------

def dossie_cliente(df: pd.DataFrame, cliente_id: str) -> dict:
    """Monta o pacote de números de um cliente para alimentar a LLM.

    TODO número que a LLM vai citar sai daqui, já calculado em pandas.
    É a fronteira do critério de 10 pontos do enunciado: a LLM recebe
    resultado pronto e só interpreta. Se ela precisar somar algo para
    responder, o dossiê está incompleto — não é a LLM que tem que somar.

    A mesma função é reaproveitada pela ferramenta `historico_cliente`
    do Nível 2.
    """
    ops = df[df["cliente_id"] == cliente_id]
    if ops.empty:
        raise ValueError(f"cliente {cliente_id} não encontrado")

    datas = ops.loc[~ops["sem_data"], "data_dt"]

    dias_fracionamento = (
        ops.loc[ops["flag_fracionamento"], "data_dt"].dt.strftime("%Y-%m-%d").unique().tolist()
        if "flag_fracionamento" in ops
        else []
    )
    ops_atipicas = (
        ops.loc[ops["flag_valor_atipico"], ["id", "data", "valor_brl", "canal", "tipo"]]
        .assign(valor_brl=lambda d: d["valor_brl"].round(2))
        .to_dict("records")
        if "flag_valor_atipico" in ops
        else []
    )

    return {
        "cliente_id": cliente_id,
        "n_operacoes": int(len(ops)),
        "volume_total_brl": round(float(ops["valor_brl"].sum()), 2),
        "ticket_medio_brl": round(float(ops["valor_brl"].mean()), 2),
        "ticket_mediano_brl": round(float(ops["valor_brl"].median()), 2),
        "maior_operacao_brl": round(float(ops["valor_brl"].max()), 2),
        "menor_operacao_brl": round(float(ops["valor_brl"].min()), 2),
        "periodo": {
            "inicio": datas.min().strftime("%Y-%m-%d") if len(datas) else None,
            "fim": datas.max().strftime("%Y-%m-%d") if len(datas) else None,
            "dias_distintos": int(datas.dt.date.nunique()) if len(datas) else 0,
        },
        "operacoes_sem_data": int(ops["sem_data"].sum()),
        "distribuicao_por_canal": ops["canal"].value_counts().to_dict(),
        "distribuicao_por_tipo": ops["tipo"].value_counts().to_dict(),
        "operacoes_em_moeda_estrangeira": int((ops["moeda"] != "BRL").sum()),
        "contrapartes_distintas": int(ops["contraparte"].nunique()),
        "contrapartes_recorrentes": (
            ops["contraparte"].value_counts().loc[lambda s: s > 1].to_dict()
        ),
        "regras_acionadas": {
            "fracionamento": {
                "acionada": bool(len(dias_fracionamento)),
                "dias": dias_fracionamento,
            },
            "valor_atipico": {
                "acionada": bool(len(ops_atipicas)),
                "operacoes": ops_atipicas,
                "limite_aplicado_brl": round(float(ops["valor_brl"].median()) * FATOR_VALOR_ATIPICO, 2),
            },
        },
    }
