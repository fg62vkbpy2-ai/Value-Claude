"""
team_context.py
Contexto de equipo rival: usa el histórico del equipo (/team/{id}/performance,
mismo patrón que el histórico de jugador) para saber si un rival concreto
es más o menos "permeable" de lo normal en cada mercado, y ajustar la
probabilidad del jugador en consecuencia.

Cada partido del histórico de un equipo trae dos bloques:
- statistics: lo que hizo ESE equipo (fouls cometidas, tiros generados...)
- opponentStatistics: lo que le hizo SU RIVAL en ese partido (tiros que
  encajó, faltas que le sacaron...)

MAPEO usado para cada mercado de jugador (el rival aquí es el equipo
CONTRARIO al del jugador que estamos analizando):

- shots            -> tiros que el rival concede    (opponentStatistics.totalShotsOnGoal)
- shots_on_target   -> tiros a puerta que concede el rival (opponentStatistics.shotsOnGoal)
- fouls (comete)    -> faltas que el rival provoca en sus oponentes (opponentStatistics.fouls)
- was_fouled        -> faltas que comete el rival habitualmente (statistics.fouls)
- tackles           -> volumen ofensivo del rival, proxy de cuánto hay que
                        defender contra él (statistics.totalShotsOnGoal)

IMPORTANTE - qué es dato real y qué es aproximación:
- Los promedios por equipo (valor_rival) SÍ son datos reales de StatsHub.
- REFERENCIA_LIGA son valores típicos de fútbol de selecciones que uso
  como "ancla" para saber si el rival está por encima o por debajo de lo
  normal, porque StatsHub no nos da fácilmente la media de toda la
  competición. Por eso el ajuste final está siempre acotado a un máximo
  de ±TOPE_AJUSTE: el contexto de equipo puede matizar la probabilidad
  del jugador, pero nunca puede dominar sobre su propio histórico real.
"""

from statistics import mean
from typing import Optional

REFERENCIA_LIGA = {
    "shots": 12.0,            # tiros totales que concede un equipo medio
    "shots_on_target": 4.5,   # tiros a puerta que concede un equipo medio
    "fouls": 12.0,            # faltas que provoca / comete un equipo medio
    "was_fouled": 12.0,
    "tackles": 12.0,          # proxy: tiros generados por el rival
}

TOPE_AJUSTE = 0.15  # el contexto de equipo nunca mueve la probabilidad más de un ±15%

CAMPO_RIVAL = {
    # mercado -> (bloque, campo) dentro de cada partido del histórico del rival
    "shots": ("opponentStatistics", "totalShotsOnGoal"),
    "shots_on_target": ("opponentStatistics", "shotsOnGoal"),
    "fouls": ("opponentStatistics", "fouls"),
    "was_fouled": ("statistics", "fouls"),
    "tackles": ("statistics", "totalShotsOnGoal"),
}


def obtener_historial_equipo(client, team_id: int) -> list:
    """Descarga el histórico de partidos de un equipo."""
    url = f"https://www.statshub.com/api/team/{team_id}/performance"
    data = client.get_json(url)
    return data.get("data", [])


def calcular_promedio_equipo(historial: list, bloque: str, campo: str, n_partidos: int = 10) -> Optional[float]:
    """Media de un campo concreto (statistics/opponentStatistics) en los últimos N partidos."""
    valores = []
    for partido in historial[:n_partidos]:
        valor = partido.get(bloque, {}).get(campo)
        if valor is not None:
            try:
                valores.append(float(valor))
            except (TypeError, ValueError):
                continue
    if not valores:
        return None
    return round(mean(valores), 2)


def construir_contexto_rival(client, team_id: int) -> dict:
    """
    Construye el contexto de un equipo: promedio de cada mercado según
    el mapeo de CAMPO_RIVAL, más el nº de partidos usados (para saber
    cuánto fiarse del dato).
    """
    try:
        historial = obtener_historial_equipo(client, team_id)
    except Exception as e:
        print(f"⚠️ Error descargando contexto del equipo {team_id}: {e}")
        historial = []

    contexto = {}
    for mercado, (bloque, campo) in CAMPO_RIVAL.items():
        contexto[mercado] = calcular_promedio_equipo(historial, bloque, campo)

    contexto["n_partidos"] = len(historial)
    return contexto


def factor_ajuste(valor_rival: Optional[float], mercado: str, tope: float = TOPE_AJUSTE) -> float:
    """
    Convierte el valor medio del rival en un factor multiplicador acotado
    entre (1 - tope) y (1 + tope). Si no hay dato del rival o no hay
    referencia para ese mercado, devuelve 1.0 (sin ajuste) en vez de
    inventar un número.
    """
    referencia = REFERENCIA_LIGA.get(mercado)
    if valor_rival is None or not referencia:
        return 1.0

    ratio = valor_rival / referencia
    # Se suaviza el ratio (x0.5) para que un rival "el doble de faltón"
    # no dispare el ajuste al doble, solo lo empuje de forma moderada.
    ajuste = (ratio - 1) * 0.5
    ajuste = max(-tope, min(tope, ajuste))
    return round(1 + ajuste, 4)
