"""
value_engine.py
Aquí es donde se decide si una apuesta tiene "valor" (edge) o no.

Compara dos probabilidades:
- prob_modelo: la que calculamos con nuestro propio modelo a partir
  del histórico del jugador (distribución normal + hit-rate histórico).
- prob_mercado_consenso: la probabilidad implícita en las cuotas de
  varias casas, después de quitarles el margen (devig).

Si nuestra probabilidad es mayor que la del mercado, hay edge
potencial. Cuantas más casas coincidan en una cuota (n_casas_consenso
alto, dispersion_cv bajo), más fiable es esa probabilidad de mercado.

CAMBIOS respecto a la v1/v3 del notebook original:
- Se eliminaron las definiciones duplicadas (antes había dos versiones
  de estimar_probabilidad, es_mercado_valido, etc. y la segunda pisaba
  a la primera; aquí solo existe una versión de cada función).
- El quality_score ahora SÍ se usa para ordenar los picks por defecto
  (antes se calculaba pero no se aplicaba en el informe final).
"""

import math
from statistics import mean, pstdev
from typing import List, Optional

MIN_CASAS_CONSENSO = 3  # por debajo de esto, no hay consenso real de mercado
MARGEN_ASUMIDO_BASE = 0.06
DISPERSION_ALERTA = 0.35  # a partir de aquí, las casas no se ponen de acuerdo


# ----------------------------------------------------------------------
# Modelo de probabilidad propio
# ----------------------------------------------------------------------

def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def probabilidad_normal(media: float, desviacion: float, linea: float) -> float:
    if desviacion < 0.25:
        desviacion = 0.25
    z = (linea - media) / desviacion
    return (1 - normal_cdf(z)) * 100


def probabilidad_historica(summary: dict, linea: float) -> Optional[float]:
    over = summary.get("overs", {}).get(str(linea))
    if over is None:
        return None
    return over["rate"]


def estimar_probabilidad(summary: dict, linea: float) -> Optional[float]:
    """
    Combina la probabilidad de una distribución normal (60%) con el
    hit-rate histórico real (40%), y ajusta por tendencia y
    consistencia. Devuelve un porcentaje entre 1 y 99.
    """
    media = summary["mean10"]
    desviacion = summary["stdev"]

    p_normal = probabilidad_normal(media, desviacion, linea)
    p_hist = probabilidad_historica(summary, linea)

    if p_hist is None:
        return None

    probabilidad = p_normal * 0.60 + p_hist * 0.40

    tendencia = summary.get("trend")
    if tendencia == "UP":
        probabilidad += 3
    elif tendencia == "DOWN":
        probabilidad -= 3

    consistencia = summary.get("consistency", 50)
    probabilidad += (consistencia - 50) * 0.10

    return max(1, min(99, round(probabilidad, 1)))


# ----------------------------------------------------------------------
# Probabilidad de mercado (devig + consenso entre casas)
# ----------------------------------------------------------------------

def _a_float(valor):
    try:
        return float(valor) if valor not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def cuota_a_prob_implicita(cuota: Optional[float]) -> Optional[float]:
    if cuota is None or cuota <= 1.0:
        return None
    return 1 / cuota


def probabilidad_consenso(apuesta: dict, campo_under: str = "under") -> dict:
    """
    Probabilidad de mercado "limpia" (sin margen), promediando todas
    las casas disponibles. Si no hay "under" (caso habitual en
    mercados de jugador), usa un margen asumido.

    También calcula la dispersión (coeficiente de variación) entre
    las cuotas crudas de las distintas casas: cuánto se fían entre sí
    los bookmakers de esa línea.
    """
    casas = apuesta.get("odds", [])
    probs_limpias = []
    margenes = []
    cuotas_crudas = []

    for casa in casas:
        cuota_over = _a_float(casa.get("over"))
        cuota_under = _a_float(casa.get(campo_under))

        if cuota_over is None:
            continue

        cuotas_crudas.append(cuota_over)
        prob_over_bruta = cuota_a_prob_implicita(cuota_over)

        if cuota_under is not None:
            prob_under_bruta = cuota_a_prob_implicita(cuota_under)
            total = prob_over_bruta + prob_under_bruta
            prob_over_limpia = prob_over_bruta / total if total > 0 else None
            margen = total - 1 if total > 0 else MARGEN_ASUMIDO_BASE
        else:
            prob_over_limpia = prob_over_bruta / (1 + MARGEN_ASUMIDO_BASE)
            margen = MARGEN_ASUMIDO_BASE

        if prob_over_limpia is not None:
            probs_limpias.append(prob_over_limpia)
            margenes.append(margen)

    n_casas = len(probs_limpias)
    if n_casas == 0:
        return {
            "prob_over_consenso": None,
            "n_casas_validas": 0,
            "margen_medio": None,
            "dispersion_cv": None,
        }

    if len(cuotas_crudas) >= 2:
        media_cuotas = mean(cuotas_crudas)
        sd_cuotas = pstdev(cuotas_crudas)
        dispersion_cv = round(sd_cuotas / media_cuotas, 3) if media_cuotas > 0 else None
    else:
        dispersion_cv = None

    return {
        "prob_over_consenso": round(mean(probs_limpias), 4),
        "n_casas_validas": n_casas,
        "margen_medio": round(mean(margenes), 4),
        "dispersion_cv": dispersion_cv,
    }


def obtener_mejor_cuota(apuesta: dict):
    """Devuelve la mejor cuota ejecutable real (para calcular EV) y su casa."""
    mejor = None
    bookmaker_mejor = None
    for casa in apuesta.get("odds", []):
        cuota = _a_float(casa.get("over"))
        if cuota is None:
            continue
        if mejor is None or cuota > mejor:
            mejor = cuota
            bookmaker_mejor = casa.get("bookmaker")
    return mejor, bookmaker_mejor


# ----------------------------------------------------------------------
# Filtro de mercados válidos (evita picks absurdos)
# ----------------------------------------------------------------------

def es_mercado_valido(summary: dict, linea: float, cuota: float, n_casas_consenso: int) -> bool:
    if summary is None:
        return False

    over = summary["overs"].get(str(linea))
    if over is None:
        return False

    if over["games"] < 5:
        return False

    if cuota < 1.05 or cuota > 20:
        return False

    if over["rate"] < 10:
        return False

    if summary["mean10"] < linea * 0.40:
        return False

    if summary["consistency"] < 5:
        return False

    # Sin consenso real entre casas no hay forma fiable de saber si hay
    # valor de verdad -> se descarta el pick.
    if n_casas_consenso < MIN_CASAS_CONSENSO:
        return False

    return True


# ----------------------------------------------------------------------
# Quality score (0-100): ahora sí se usa para ordenar los picks finales
# ----------------------------------------------------------------------

def calcular_quality_score(pick: dict) -> float:
    """
    Puntuación global que prioriza apuestas estables y con alta
    probabilidad, sin dejar que una cuota enorme (y por tanto un EV
    inflado) domine el ranking.
    """
    score = 0.0

    consistency = pick.get("consistency", 0)
    cv = pick.get("dispersion_cv")
    cv = cv if cv is not None else 2
    trend = pick.get("trend", "STABLE")

    confidence = consistency
    if cv <= 0.20:
        confidence += 15
    elif cv <= 0.35:
        confidence += 10
    elif cv <= 0.50:
        confidence += 5

    if trend == "UP":
        confidence += 10
    elif trend == "STABLE":
        confidence += 5

    confidence = min(confidence, 100)
    score += confidence * 0.45

    probability = pick.get("prob_modelo", 0)
    score += probability * 0.25

    ev = pick.get("ev", 0)
    ev_score = 0 if ev <= 0 else min(ev, 35) * (100 / 35)
    score += ev_score * 0.15

    score += pick.get("consistency", 0) * 0.10

    if trend == "UP":
        score += 5
    elif trend == "STABLE":
        score += 3

    if probability < 45:
        score -= 10
    if pick.get("hit_rate", 0) < 40:
        score -= 10
    if cv > 0.60:
        score -= 8

    return round(max(0, min(score, 100)), 1)


# ----------------------------------------------------------------------
# Análisis de un mercado / jugador / partido completo
# ----------------------------------------------------------------------

def analizar_mercado(jugador: dict, mercado: str, apuesta: dict, summary: dict) -> Optional[dict]:
    linea = apuesta["line"]

    mejor_cuota, bookmaker = obtener_mejor_cuota(apuesta)
    if mejor_cuota is None:
        return None

    consenso = probabilidad_consenso(apuesta)

    if not es_mercado_valido(summary, linea, mejor_cuota, consenso["n_casas_validas"]):
        return None

    if consenso["prob_over_consenso"] is None:
        return None

    prob_modelo = estimar_probabilidad(summary, linea)
    if prob_modelo is None:
        return None

    prob_modelo_frac = prob_modelo / 100
    prob_mercado_frac = consenso["prob_over_consenso"]

    edge = round((prob_modelo_frac - prob_mercado_frac) * 100, 2)
    ev = round((prob_modelo_frac * mejor_cuota - 1) * 100, 2)

    if edge <= 0:
        return None

    dispersion = consenso["dispersion_cv"]
    alerta_dispersion = dispersion is not None and dispersion > DISPERSION_ALERTA

    over = summary["overs"].get(str(linea))

    pick = {
        "player": jugador["name"],
        "playerId": jugador["playerId"],
        "team": jugador.get("team"),
        "position": jugador.get("position"),
        "market": mercado,
        "line": linea,
        "bookmaker": bookmaker,
        "odds": mejor_cuota,
        "prob_modelo": prob_modelo,
        "prob_mercado_consenso": round(prob_mercado_frac * 100, 1),
        "n_casas_consenso": consenso["n_casas_validas"],
        "margen_medio_mercado": consenso["margen_medio"],
        "dispersion_cv": dispersion,
        "alerta_dispersion": alerta_dispersion,
        "edge": edge,
        "ev": ev,
        "hits": over["hits"],
        "games": over["games"],
        "hit_rate": over["rate"],
        "trend": summary["trend"],
        "consistency": summary["consistency"],
        "mean5": summary["mean5"],
        "mean10": summary["mean10"],
        "n_partidos_validos": summary.get("n_partidos_validos", over["games"]),
    }

    pick["quality_score"] = calcular_quality_score(pick)
    return pick


def analizar_jugador(jugador: dict) -> List[dict]:
    resultados = []
    mercados = jugador.get("markets", {})

    for mercado, apuestas in mercados.items():
        summary = jugador.get("summary", {}).get(mercado)
        if summary is None:
            continue

        for apuesta in apuestas:
            try:
                resultado = analizar_mercado(jugador, mercado, apuesta, summary)
                if resultado is not None:
                    resultados.append(resultado)
            except Exception as e:
                print(f"⚠️ {jugador.get('name')} - {mercado}: {e}")

    return resultados


def analizar_partido(partido: dict) -> List[dict]:
    picks = []
    for jugador in partido["players"]:
        picks.extend(analizar_jugador(jugador))
    return picks


def ordenar_picks(picks: List[dict], campo: str = "quality_score", descendente: bool = True) -> List[dict]:
    return sorted(picks, key=lambda x: x.get(campo, 0) or 0, reverse=descendente)


def mejores_picks(partido: dict, top: int = 20, ordenar_por: str = "quality_score") -> List[dict]:
    picks = analizar_partido(partido)
    picks = ordenar_picks(picks, campo=ordenar_por)
    return picks[:top]
