"""
Camada de acesso à LLM (Groq) com saída estruturada, cache e telemetria.

Separado de `pipeline.py` de propósito: pipeline é cálculo determinístico e
não fala com rede; este módulo é o único ponto do projeto que chama LLM.
O Nível 2 importa daqui em vez de reimplementar.

Free tier do llama-3.3-70b-versatile: 30 req/min, 1.000 req/dia,
12.000 tokens/min, 100.000 tokens/dia. O gargalo real é TOKENS por
minuto, não requisições: com prompt de ~1.500 tokens, o TPM estoura em
~8 chamadas/min enquanto o RPM ainda permitiria 30. Daí o cache e o
intervalo mínimo entre chamadas.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Type, TypeVar

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

load_dotenv()

MODELO_PADRAO = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache_llm"

# Preço público do llama-3.3-70b-versatile (USD por milhão de tokens).
# Usamos camada gratuita, então isto é ESTIMATIVA do que custaria em
# produção — declarado como tal para não confundir com gasto real.
PRECO_USD_POR_MILHAO = {"entrada": 0.59, "saida": 0.79}

# Intervalo mínimo entre chamadas não cacheadas, para não bater no TPM.
INTERVALO_MINIMO_S = 6.0
_ultima_chamada = 0.0


# --------------------------------------------------------------------------
# Contrato de saída
# --------------------------------------------------------------------------

class ParecerPLD(BaseModel):
    """Estrutura obrigatória do parecer, conforme o enunciado.

    `Literal` no nível de risco é intencional: se a LLM responder
    'altíssimo', 'ALTO' ou 'muito alto', a validação REJEITA em vez de
    aceitar um valor que quebraria a comparação do confronto no Nível 2.
    Vocabulário fechado é o que torna a saída utilizável a jusante.

    `red_flags` aceita lista VAZIA — e essa foi uma correção, não o
    desenho original. A primeira versão exigia ao menos um item. Na
    primeira execução do agente sobre CLI-029, o modelo investigou,
    concluiu que a sinalização da regra não se sustentava e devolveu
    lista vazia: a validação rejeitou. Ou seja, o schema tornava
    "não encontrei indício" inexprimível e forçaria o modelo a fabricar
    suspeita para conseguir responder — exatamente o falso positivo que
    a triagem deveria evitar. Restrição de schema não é neutra: ela
    define quais conclusões o sistema consegue representar.

    O que sobrou é uma restrição com significado, no `_coerencia`
    abaixo: risco ALTO sem nenhum indício citado é incoerente, e aí sim
    vale rejeitar.
    """

    nivel_risco: Literal["baixo", "medio", "alto"]
    tipologia_suspeita: str = Field(min_length=3)
    red_flags: list[str] = Field(default_factory=list)
    justificativa: str = Field(min_length=20)

    @field_validator("red_flags", mode="before")
    @classmethod
    def _lista_de_string(cls, v):
        """Normaliza red_flags entregue como string delimitada.

        Falha observada em execucao real com gpt-oss-120b: o modelo achata
        a lista num unico texto separado por ponto e virgula, por exemplo
        "op_acima_do_limite; ticket_acima_da_mediana; moeda_estrangeira".

        A coercao e deliberadamente estreita. Ela trata DIFERENCA DE
        FORMATO onde o conteudo semantico esta integro e a separacao e
        inequivoca. Nao relaxa o vocabulario de `nivel_risco`, que
        continua rejeitando valor fora do Literal — la a diferenca seria
        de SIGNIFICADO, e aceitar "altissimo" quebraria a comparacao do
        confronto no Nivel 2 sem deixar rastro.

        Normalizar na fronteira custa uma funcao; gastar um retry a cada
        chamada custa quota, latencia e ainda pode falhar de novo.
        """
        if isinstance(v, str):
            partes = [p.strip() for p in re.split(r"[;\n]+", v) if p.strip()]
            return partes or [v.strip()]
        return v

    @model_validator(mode="after")
    def _coerencia(self):
        """Risco alto exige ao menos um indicio citado.

        Substitui o `min_length=1` incondicional. A diferenca importa:
        antes, QUALQUER parecer precisava de red flag, inclusive o que
        conclui pela ausencia de risco. Agora a exigencia acompanha a
        conclusao — quem afirma risco alto precisa dizer com base em que,
        e quem conclui risco baixo pode legitimamente nao ter indicio
        nenhum a apontar.
        """
        if self.nivel_risco == "alto" and not self.red_flags:
            raise ValueError(
                "nivel_risco 'alto' exige ao menos uma red flag; "
                "parecer de risco alto sem indicio citado nao e auditavel"
            )
        return self


T = TypeVar("T", bound=BaseModel)


@dataclass
class Telemetria:
    """Custo e latência de uma chamada. Vira linha de DataFrame depois."""

    modelo: str
    tokens_entrada: int
    tokens_saida: int
    latencia_s: float
    tentativas: int
    cacheado: bool
    status: str  # ok | falha_validacao | erro_api
    custo_estimado_usd: float = 0.0
    erro: str | None = None

    def __post_init__(self):
        self.custo_estimado_usd = round(
            self.tokens_entrada / 1e6 * PRECO_USD_POR_MILHAO["entrada"]
            + self.tokens_saida / 1e6 * PRECO_USD_POR_MILHAO["saida"],
            6,
        )


# --------------------------------------------------------------------------
# Cache em disco
# --------------------------------------------------------------------------

def _chave_cache(modelo: str, system: str, user: str) -> str:
    bruto = f"{modelo}||{system}||{user}".encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()[:20]


def _ler_cache(chave: str) -> dict | None:
    arq = CACHE_DIR / f"{chave}.json"
    if arq.exists():
        return json.loads(arq.read_text(encoding="utf-8"))
    return None


def _gravar_cache(chave: str, payload: dict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{chave}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Parsing tolerante
# --------------------------------------------------------------------------

def extrair_json(texto: str) -> str:
    """Recupera o objeto JSON de uma resposta que pode vir suja.

    Falhas reais que isso cobre: cerca de markdown (```json ... ```),
    frase de cortesia antes do JSON ('Claro! Aqui está:'), e texto
    depois do fechamento. Não conserta JSON sintaticamente inválido —
    para isso existe o retry.
    """
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)

    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio != -1 and fim > inicio:
        return texto[inicio : fim + 1]
    return texto


def validar(texto: str, schema: Type[T]) -> tuple[T | None, str | None]:
    """Tenta materializar o schema. Devolve (objeto, None) ou (None, erro)."""
    try:
        return schema.model_validate_json(extrair_json(texto)), None
    except ValidationError as e:
        return None, f"ValidationError: {e.errors()}"
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"JSON inválido: {e}"


# --------------------------------------------------------------------------
# Chamada
# --------------------------------------------------------------------------

def chamar(
    system: str,
    user: str,
    schema: Type[T] = ParecerPLD,
    modelo: str = MODELO_PADRAO,
    max_tentativas: int = 2,
    usar_cache: bool = True,
    temperatura: float = 0.2,
) -> tuple[T | None, Telemetria]:
    """Chama a LLM e devolve (objeto validado | None, telemetria).

    Estratégia de robustez em três camadas, porque o enunciado pede
    tratamento de resposta malformada:

      1. `response_format=json_object` — o modelo é forçado a emitir JSON
         sintaticamente válido. Não garante que os CAMPOS estejam certos.
      2. Validação Pydantic — pega campo faltando, tipo errado e valor
         fora do vocabulário permitido.
      3. Retry realimentado — o erro de validação volta no prompt como
         mensagem do usuário. Corrigir com o erro em mãos funciona muito
         melhor que só repetir a pergunta.

    Falhando as três, devolve None com status registrado. NUNCA levanta
    exceção: em execução de lote, um cliente problemático não pode
    derrubar os outros nove.
    """
    global _ultima_chamada

    chave = _chave_cache(modelo, system, user)
    if usar_cache and (hit := _ler_cache(chave)):
        obj, _ = validar(json.dumps(hit["conteudo"]), schema)
        tel = Telemetria(**{**hit["telemetria"], "cacheado": True, "latencia_s": 0.0})
        return obj, tel

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY não encontrada. Copie .env.example para .env e preencha."
        )

    cliente = Groq(api_key=api_key)
    mensagens = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    tokens_entrada = tokens_saida = 0
    inicio = time.perf_counter()

    for tentativa in range(1, max_tentativas + 1):
        espera = INTERVALO_MINIMO_S - (time.perf_counter() - _ultima_chamada)
        if _ultima_chamada and espera > 0:
            time.sleep(espera)

        try:
            resp = cliente.chat.completions.create(
                model=modelo,
                messages=mensagens,
                temperature=temperatura,
                response_format={"type": "json_object"},
                max_tokens=1200,
            )
            _ultima_chamada = time.perf_counter()
        except Exception as e:  # 429, timeout, indisponibilidade
            _ultima_chamada = time.perf_counter()
            if tentativa < max_tentativas:
                time.sleep(10 * tentativa)  # backoff linear
                continue
            return None, Telemetria(
                modelo=modelo,
                tokens_entrada=tokens_entrada,
                tokens_saida=tokens_saida,
                latencia_s=round(time.perf_counter() - inicio, 3),
                tentativas=tentativa,
                cacheado=False,
                status="erro_api",
                erro=str(e)[:300],
            )

        tokens_entrada += resp.usage.prompt_tokens
        tokens_saida += resp.usage.completion_tokens
        bruto = resp.choices[0].message.content

        obj, erro = validar(bruto, schema)
        if obj is not None:
            tel = Telemetria(
                modelo=modelo,
                tokens_entrada=tokens_entrada,
                tokens_saida=tokens_saida,
                latencia_s=round(time.perf_counter() - inicio, 3),
                tentativas=tentativa,
                cacheado=False,
                status="ok",
            )
            if usar_cache:
                _gravar_cache(
                    chave,
                    {"conteudo": obj.model_dump(), "telemetria": asdict(tel)},
                )
            return obj, tel

        # Realimenta o erro e tenta de novo.
        mensagens += [
            {"role": "assistant", "content": bruto},
            {
                "role": "user",
                "content": (
                    f"A resposta anterior não passou na validação: {erro}\n"
                    f"Reescreva APENAS o objeto JSON respeitando o schema. "
                    f"Atencao: red_flags deve ser um ARRAY JSON de strings, "
                    f'como ["indicio um", "indicio dois"], nunca um texto unico. '
                    f"Sem texto fora do JSON."
                ),
            },
        ]

    return None, Telemetria(
        modelo=modelo,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
        latencia_s=round(time.perf_counter() - inicio, 3),
        tentativas=max_tentativas,
        cacheado=False,
        status="falha_validacao",
        erro=erro,
    )
