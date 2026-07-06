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

CONTEXTO NARRATIVO (ronda anterior):
- construir_contexto_jugadores() extrae, para CADA jugador y CADA
  mercado con histórico, un resumen legible (media, tendencia,
  consistencia, factor de rival), tenga o no cuota ofertada.

CUOTAS EN EL CONTEXTO (ronda anterior):
- construir_contexto_jugadores() también incluye, dentro de cada
  mercado, la lista de líneas con cuota real (mejor cuota, casa,
  probabilidad implícita, consenso), pasen o no el filtro interno.

CLASIFICACIÓN EN 4 CATEGORÍAS (esta ronda, tras auditar México vs
Inglaterra y detectar que Gordon y Quiñones tenían edge real pero
quedaron invisibles por baja cobertura de datos, no por falta de
valor):
- es_mercado_valido() servía de puerta única: todo lo que no la
  pasaba, desaparecía del informe sin dejar rastro. El motor SÍ había
  detectado la anomalía (edge positivo) pero nunca se lo comunicaba
  al usuario.
- Ahora analizar_mercado() ya NO descarta en silencio: clasifica cada
  mercado con cuota real en A (recomendado), B (verificar manual,
  con checklist de qué falta y prioridad de revisión), C (descartado
  por EV negativo) o D (sin datos suficientes ni para opinar). Ver
  clasificacion_mercados.py para el detalle de cada categoría y las
  fórmulas de prioridad_manual.
- mejores_picks() se mantiene para compatibilidad (sigue devolviendo
  solo la categoría A, como antes). Para el informe completo con las
  4 categorías, usar mejores_picks_categorizado().
"""

import math
from statistics import mean, pstdev
from typing import List, Optional

from team_context import factor_ajuste
from clasificacion_mercados import (
    CATEGORIA_RECOMENDADO,
    CATEGORIA_VERIFICAR,
    CATEGORIA_DESCARTADO,
    CATEGORIA_SIN_DATOS,
    clasificar_mercado,
    formatear_entrada_verificacion,
    formatear_mercado_excluido,
)

MIN_CASAS_CONSENSO = 3  # por debajo de esto, no hay consenso real de mercado
MARGEN_ASUMIDO_BASE = 0.06
DISPERSION_ALERTA = 0.35  # a partir de aquí, las casas no se ponen de acuerdo

# Umbrales para colapsar mercados sin señal real en el contexto narrativo
# (ver _mercado_es_irrelevante). Un mercado con cuota real NUNCA se
# colapsa, pase lo que pase con estos umbrales -- ahí puede haber
# dinero de por medio y la IA debe verlo completo siempre.
UMBRAL_MEAN_IRRELEVANTE = 0.3
UMBRAL_RATE_IRRELEVANTE = 15.0


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


def estimar_probabilidad(summary: dict, linea: float, factor_rival: float = 1.0) -> Optional[float]:
    """
    Combina la probabilidad de una distribución normal (60%) con el
    hit-rate histórico real (40%), y ajusta por tendencia y
    consistencia. Devuelve un porcentaje entre 1 y 99.

    `factor_rival` (opcional, por defecto 1.0 = sin efecto) multiplica
    la media histórica del jugador antes de calcular la parte normal,
    para reflejar que el rival concreto es más o menos permeable de lo
    habitual en ese mercado (ver team_context.py). El hit-rate
    histórico (p_hist) NO se toca, porque ya es un dato observado real
    y no tiene sentido "corregirlo" retroactivamente.
    """
    media = summary["mean10"] * factor_rival
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
# Filtro de mercados válidos (se mantiene para compatibilidad con
# mejores_picks(); internamente equivale a exigir categoría A)
# ----------------------------------------------------------------------

def es_mercado_valido(summary: dict, linea: float, cuota: float, n_casas_consenso: int) -> bool:
    if summary is None:
        return False

    over = summary["overs"].get(str(linea))
    if over is None:
        return False

    if over["games"] < 6:
        return False

    if cuota < 1.05 or cuota > 20:
        return False

    if over["rate"] < 10:
        return False

    if summary["mean10"] < linea * 0.40:
        return False

    if summary["consistency"] < 5:
        return False

    if n_casas_consenso < MIN_CASAS_CONSENSO:
        return False

    return True


# ----------------------------------------------------------------------
# Quality score (0-100)
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

def _construir_pick_base(jugador: dict, mercado: str, apuesta: dict, summary: dict,
                          mejor_cuota: float, bookmaker: str, prob_modelo: Optional[float],
                          consenso: dict, factor_rival: float) -> dict:
    """Campos comunes de un pick, se usen luego para categoría A o B."""
    linea = apuesta["line"]
    over = summary["overs"].get(str(linea)) if summary else None

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
        "n_casas_consenso": consenso["n_casas_validas"],
        "margen_medio_mercado": consenso["margen_medio"],
        "dispersion_cv": consenso["dispersion_cv"],
        "factor_rival": factor_rival,
    }

    if consenso["prob_over_consenso"] is not None:
        pick["prob_mercado_consenso"] = round(consenso["prob_over_consenso"] * 100, 1)

    if over is not None:
        pick.update({
            "hits": over["hits"],
            "games": over["games"],
            "hit_rate": over["rate"],
        })

    if summary is not None:
        pick.update({
            "trend": summary.get("trend"),
            "consistency": summary.get("consistency"),
            "mean5": summary.get("mean5"),
            "mean10": summary.get("mean10"),
            "n_partidos_validos": summary.get("n_partidos_validos", pick.get("games")),
        })

    return pick


def analizar_mercado(
    jugador: dict,
    mercado: str,
    apuesta: dict,
    summary: dict,
    factor_rival: float = 1.0,
) -> Optional[dict]:
    """
    Analiza un mercado con cuota real y devuelve SIEMPRE un pick con su
    categoría (A/B/C/D) -- salvo que ni siquiera haya una cuota
    ejecutable, en cuyo caso no hay nada que clasificar y se devuelve
    None (esto no cuenta como "descarte silencioso": simplemente no
    existe apuesta posible en ese mercado).

    Antes: devolvía None en cuanto fallaba es_mercado_valido() y ese
    mercado desaparecía del informe sin dejar rastro, aunque tuviera
    edge positivo (caso Gordon/Quiñones, México vs Inglaterra).

    Ahora: SIEMPRE se calcula edge/EV si hay datos suficientes para
    ello, y se clasifica en A/B/C/D. Solo la categoría A entra en el
    ranking de "picks recomendados"; B, C y D quedan documentadas para
    que el usuario (o la IA leyendo el informe) sepa qué hay ahí y por
    qué no se recomienda todavía.
    """
    linea = apuesta["line"]

    mejor_cuota, bookmaker = obtener_mejor_cuota(apuesta)
    if mejor_cuota is None:
        return None

    consenso = probabilidad_consenso(apuesta)
    prob_modelo = estimar_probabilidad(summary, linea, factor_rival=factor_rival) if summary else None

    pick = _construir_pick_base(
        jugador, mercado, apuesta, summary, mejor_cuota, bookmaker, prob_modelo, consenso, factor_rival
    )

    clasificacion = clasificar_mercado(
        summary=summary,
        linea=linea,
        mejor_cuota=mejor_cuota,
        prob_modelo=prob_modelo,
        prob_mercado_frac=consenso["prob_over_consenso"],
        n_casas_consenso=consenso["n_casas_validas"],
        dispersion_cv=consenso["dispersion_cv"],
    )

    pick["categoria"] = clasificacion["categoria"]
    pick["edge"] = clasificacion.get("edge")
    pick["ev"] = clasificacion.get("ev")

    if clasificacion["categoria"] == CATEGORIA_DESCARTADO:
        pick["motivo_descarte"] = clasificacion.get("motivo_descarte")

    if clasificacion["categoria"] == CATEGORIA_VERIFICAR:
        pick["prioridad_manual"] = clasificacion["prioridad_manual"]
        pick["motivo_verificacion"] = clasificacion["motivo"]

    if clasificacion["categoria"] == CATEGORIA_RECOMENDADO:
        pick["quality_score"] = calcular_quality_score(pick)

    return pick


def analizar_jugador(jugador: dict) -> List[dict]:
    resultados = []
    mercados = jugador.get("markets", {})
    contexto_rival = jugador.get("rival_context", {})

    for mercado, apuestas in mercados.items():
        summary = jugador.get("summary", {}).get(mercado)
        factor_rival = factor_ajuste(contexto_rival, mercado)

        for apuesta in apuestas:
            try:
                resultado = analizar_mercado(jugador, mercado, apuesta, summary, factor_rival=factor_rival)
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


def deduplicar_picks(picks: List[dict]) -> List[dict]:
    """
    Cuando un jugador tiene varias líneas del mismo mercado, esas
    apuestas están altamente correlacionadas -> no son oportunidades
    independientes. Nos quedamos solo con la mejor por cada
    combinación jugador+mercado, PERO ahora se deduplica dentro de
    cada categoría por separado: una línea A no debe tapar a una
    línea B del mismo mercado si son literalmente cuotas distintas
    (p.ej. 0.5 vale para pick, 2.5 del mismo jugador queda a revisar).
    """
    mejores = {}

    def _score_desempate(pick: dict) -> float:
        return pick.get("quality_score") or pick.get("prioridad_manual") or pick.get("edge") or 0

    for pick in picks:
        clave = (pick.get("playerId"), pick.get("market"), pick.get("categoria"))
        actual = mejores.get(clave)

        if actual is None or _score_desempate(pick) > _score_desempate(actual):
            mejores[clave] = pick

    return list(mejores.values())


def ordenar_picks(picks: List[dict], campo: str = "quality_score", descendente: bool = True) -> List[dict]:
    return sorted(picks, key=lambda x: x.get(campo, 0) or 0, reverse=descendente)


def mejores_picks(
    partido: dict,
    top: int = 20,
    ordenar_por: str = "quality_score",
    deduplicar: bool = True,
) -> List[dict]:
    """
    Compatibilidad con el comportamiento anterior: solo categoría A,
    ordenados por quality_score. Para el informe completo con las 4
    categorías, usar mejores_picks_categorizado().
    """
    picks = [p for p in analizar_partido(partido) if p.get("categoria") == CATEGORIA_RECOMENDADO]

    if deduplicar:
        picks = deduplicar_picks(picks)

    picks = ordenar_picks(picks, campo=ordenar_por)
    return picks[:top]


def mejores_picks_categorizado(partido: dict, deduplicar: bool = True) -> dict:
    """
    Informe completo en 4 categorías, tal como se decidió tras la
    auditoría de México vs Inglaterra:

    {
        "recomendados": [...],   # categoría A, ordenados por quality_score
        "verificar_manual": [...],  # categoría B, ordenados por prioridad_manual
        "descartados": [...],    # categoría C
        "sin_datos": [...],      # categoría D
    }

    Cada pick en "verificar_manual" ya trae "prioridad_manual" y
    "motivo_verificacion" (con la lista de tareas ☐ concretas), listos
    para formatear_entrada_verificacion().
    """
    todos = analizar_partido(partido)

    if deduplicar:
        todos = deduplicar_picks(todos)

    recomendados = [p for p in todos if p.get("categoria") == CATEGORIA_RECOMENDADO]
    verificar = [p for p in todos if p.get("categoria") == CATEGORIA_VERIFICAR]
    descartados = [p for p in todos if p.get("categoria") == CATEGORIA_DESCARTADO]
    sin_datos = [p for p in todos if p.get("categoria") == CATEGORIA_SIN_DATOS]

    recomendados = ordenar_picks(recomendados, campo="quality_score")
    verificar = ordenar_picks(verificar, campo="prioridad_manual")

    return {
        "recomendados": recomendados,
        "verificar_manual": verificar,
        "descartados": descartados,
        "sin_datos": sin_datos,
    }


def formatear_informe_texto(categorias: dict) -> str:
    """
    Vuelca mejores_picks_categorizado() a texto plano, listo para
    pegar en el informe: primero los recomendados, luego la cola de
    verificación manual (con checklist), y al final el resumen corto
    de mercados excluidos (C y D) para responder de antemano a
    "¿por qué no sale X?".
    """
    bloques = []

    bloques.append("=== 🟢 PICKS RECOMENDADOS ===")
    if categorias["recomendados"]:
        for p in categorias["recomendados"]:
            bloques.append(
                f"- {p['player']} ({p['team']}) | {p['market']} Over {p['line']} @ {p['odds']} ({p['bookmaker']}) "
                f"| edge={p['edge']:+.1f} ev={p['ev']:+.1f}% quality={p['quality_score']}"
            )
    else:
        bloques.append("(ninguno esta ronda)")

    bloques.append("")
    bloques.append("=== 🟡 VERIFICACIÓN MANUAL (por prioridad) ===")
    if categorias["verificar_manual"]:
        for p in categorias["verificar_manual"]:
            bloques.append(formatear_entrada_verificacion(p, {
                "hit_rate": p.get("hit_rate"),
                "n_partidos": p.get("n_partidos_validos", p.get("games")),
                "prioridad_manual": p["prioridad_manual"],
                "motivo": p["motivo_verificacion"],
            }))
            bloques.append("")
    else:
        bloques.append("(ninguno esta ronda)")

    bloques.append("=== ⚪ MERCADOS EXCLUIDOS (resumen) ===")
    excluidos = categorias["descartados"] + categorias["sin_datos"]
    if excluidos:
        for p in excluidos:
            clasificacion_min = {
                "categoria": p["categoria"],
                "edge": p.get("edge"),
                "hit_rate": p.get("hit_rate"),
                "motivo_descarte": p.get("motivo_descarte"),
                "n_partidos": p.get("n_partidos_validos", p.get("games", 0)),
                "motivo": p.get("motivo_verificacion", {"faltantes": [], "tareas": []}),
            }
            texto = formatear_mercado_excluido(p, clasificacion_min)
            if texto:
                bloques.append(texto)
                bloques.append("")
    else:
        bloques.append("(ninguno esta ronda)")

    return "\n".join(bloques)


# ----------------------------------------------------------------------
# Contexto narrativo: TODOS los mercados con histórico, tengan o no
# cuota ofertada. Esto NO son picks, es información adicional pensada
# para dársela a una IA como contexto de análisis.
# ----------------------------------------------------------------------

def _lineas_con_cuota(apuestas: List[dict]) -> List[dict]:
    """
    Para un mercado con cuota ofertada, devuelve una entrada por línea
    con la mejor cuota, casa, probabilidad implícita y consenso entre
    casas -- SIN aplicar es_mercado_valido. La decisión de si eso es
    "value" o no la hace quien lea el informe, no este filtro.
    """
    lineas = []
    for apuesta in apuestas:
        mejor_cuota, bookmaker = obtener_mejor_cuota(apuesta)
        if mejor_cuota is None:
            continue

        consenso = probabilidad_consenso(apuesta)
        prob_consenso = consenso["prob_over_consenso"]

        lineas.append({
            "line": apuesta.get("line"),
            "bookmaker": bookmaker,
            "odds": mejor_cuota,
            "prob_implicita": round(cuota_a_prob_implicita(mejor_cuota) * 100, 1),
            "prob_mercado_consenso": round(prob_consenso * 100, 1) if prob_consenso is not None else None,
            "n_casas_consenso": consenso["n_casas_validas"],
            "dispersion_cv": consenso["dispersion_cv"],
        })
    return lineas


def _mercado_es_irrelevante(summary: dict, lineas_con_cuota: List[dict]) -> bool:
    """
    Decide si un mercado aporta tan poca señal que conviene colapsarlo
    a una nota de una línea en vez del bloque completo (mean/stdev/overs).

    Un mercado con cuota real NUNCA se considera irrelevante.
    """
    if lineas_con_cuota:
        return False

    if summary["mean10"] > UMBRAL_MEAN_IRRELEVANTE:
        return False

    tasas = [o["rate"] for o in summary.get("overs", {}).values()]
    max_rate = max(tasas) if tasas else 0

    return max_rate <= UMBRAL_RATE_IRRELEVANTE


def construir_contexto_jugadores(partido: dict) -> List[dict]:
    """
    Para cada jugador, resume TODOS los mercados de los que hay
    histórico (jugador["summary"]), e incluye las cuotas reales de
    cada línea ofertada en ese mercado (si las hay), tenga o no ese
    mercado un "pick" aprobado.
    """
    contexto = []

    for jugador in partido.get("players", []):
        summary_jugador = jugador.get("summary", {})
        if not summary_jugador:
            continue

        contexto_rival = jugador.get("rival_context", {})
        mercados_con_cuota = jugador.get("markets", {})

        stats_por_mercado = {}
        for mercado, summary in summary_jugador.items():
            if summary is None:
                continue

            lineas_con_cuota = _lineas_con_cuota(mercados_con_cuota.get(mercado, []))

            if _mercado_es_irrelevante(summary, lineas_con_cuota):
                stats_por_mercado[mercado] = {
                    "mean10": summary["mean10"],
                    "n_partidos_validos": summary["n_partidos_validos"],
                    "nota": "casi nunca ocurre (histórico irrelevante, sin cuota ofertada)",
                }
                continue

            stats_por_mercado[mercado] = {
                "mean5": summary["mean5"],
                "mean10": summary["mean10"],
                "stdev": summary["stdev"],
                "trend": summary["trend"],
                "consistency": summary["consistency"],
                "n_partidos_validos": summary["n_partidos_validos"],
                "overs": summary["overs"],
                "factor_rival": factor_ajuste(contexto_rival, mercado),
                "tiene_cuota_actualmente": len(lineas_con_cuota) > 0,
                "lineas_con_cuota": lineas_con_cuota,
            }

        if stats_por_mercado:
            contexto.append({
                "player": jugador["name"],
                "playerId": jugador["playerId"],
                "team": jugador.get("team"),
                "position": jugador.get("position"),
                "stats": stats_por_mercado,
            })

    return contexto
