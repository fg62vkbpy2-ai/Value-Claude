"""
team_market.py
Modelo de probabilidad real para mercados de EQUIPO (tiros, córners,
tiros a puerta, faltas, entradas, paradas de portero...), comparado
contra una cuota real introducida a mano (StatsHub no ofrece cuotas de
equipo, así que aquí no hay consenso automático de varias casas como
con los jugadores -- el usuario aporta la cuota que ve en su propia
casa de apuestas, p.ej. Betfair o Bet365).

Reutiliza la misma maquinaria que ya usamos para jugadores:
- stats_engine.calcular_summary_serie (mean5, mean10, stdev, trend,
  consistency, overs) -- es genérica sobre cualquier lista de números,
  no depende de que sean datos de jugador.
- value_engine.estimar_probabilidad (normal 60% + hit-rate histórico
  40%) -- igual de genérica, solo necesita el dict de summary y la
  línea.

Esto es intencional y no un atajo: la serie histórica de un equipo
(tiros por partido, últimos 20 partidos) es el mismo tipo de dato que
la serie histórica de un jugador, así que el mismo modelo tiene el
mismo fundamento estadístico aplicado aquí.

LO QUE ESTO NO HACE (para que quede explícito y no se use mal):
- No hay consenso de varias casas -> la "prob_mercado" depende de la
  cuota que introduzcas tú. Si solo das la cuota "Más de" (sin
  "Menos de"), se asume un margen fijo del 6% en vez de devigar de
  verdad -- menos preciso, pero mejor que nada.
- No hay factor_rival aplicado aquí: el equipo analizado es el propio
  sujeto del mercado, no tiene sentido ajustarlo por "su propio rival"
  dos veces (eso ya está implícito en su historial de partidos contra
  distintos rivales).
"""

from typing import List, Optional

from scraper import StatsHubClient, TEAM_STAT_KEYS
from stats_engine import calcular_summary_serie
from value_engine import estimar_probabilidad

ETIQUETAS_MERCADO = {
    "shots_total": "Tiros totales",
    "shots_on_target": "Tiros a puerta",
    "corners": "Córners",
    "fouls": "Faltas",
    "tackles": "Entradas",
    "saves_portero": "Paradas de portero",
}


def obtener_serie_equipo(
    client: StatsHubClient,
    team_id: int,
    stat_key: str,
    tournament_ids: Optional[str] = None,
    limit: int = 20,
) -> List[float]:
    """
    Serie histórica "a favor" del equipo para un statisticKey concreto,
    en el mismo orden que devuelve la API (más reciente primero, igual
    que el histórico de jugador).
    """
    filas = client.obtener_stat_equipo(team_id, stat_key, tournament_ids, limit)
    serie = []
    for fila in filas:
        home_id = fila.get("home_team_id")
        away_id = fila.get("away_team_id")

        if team_id == home_id:
            valor = fila.get("home_value")
        elif team_id == away_id:
            valor = fila.get("away_value")
        else:
            continue

        if valor is None:
            continue
        try:
            serie.append(float(valor))
        except (TypeError, ValueError):
            continue

    return serie


def resumen_mercado_equipo(
    client: StatsHubClient,
    team_id: int,
    mercado: str,
    linea: float,
    tournament_ids: Optional[str] = None,
    limit: int = 20,
) -> Optional[dict]:
    """
    Descarga la serie real del equipo para `mercado` (una clave de
    TEAM_STAT_KEYS: "shots_total", "shots_on_target", "corners",
    "fouls", "tackles", "saves_portero") y calcula el mismo resumen
    que usamos para jugadores. Devuelve None si el mercado no existe
    en TEAM_STAT_KEYS o no hay datos suficientes.
    """
    stat_key = TEAM_STAT_KEYS.get(mercado)
    if not stat_key:
        return None

    serie = obtener_serie_equipo(client, team_id, stat_key, tournament_ids, limit)
    if not serie:
        return None

    return calcular_summary_serie(serie, [linea])


def probabilidad_mercado_manual(
    odds_over: float,
    odds_under: Optional[float] = None,
    margen_asumido: float = 0.06,
) -> dict:
    """
    Probabilidad de mercado "limpia" a partir de la(s) cuota(s) real(es)
    que ves en tu casa de apuestas.

    Si das también `odds_under` (la cuota de "Menos de" para la misma
    línea), se hace un devig real de dos vías -- mucho más preciso.
    Si no, se asume un margen fijo del 6% (mismo criterio que
    value_engine usa para mercados de jugador sin "under").
    """
    prob_over_bruta = 1 / odds_over

    if odds_under:
        prob_under_bruta = 1 / odds_under
        total = prob_over_bruta + prob_under_bruta
        prob_limpia = prob_over_bruta / total if total > 0 else prob_over_bruta
        margen = total - 1
    else:
        prob_limpia = prob_over_bruta / (1 + margen_asumido)
        margen = margen_asumido

    return {"prob_mercado": round(prob_limpia * 100, 2), "margen": round(margen, 4)}


def analizar_pick_equipo(
    client: StatsHubClient,
    team_id: int,
    nombre_equipo: str,
    mercado: str,
    linea: float,
    odds_over: float,
    odds_under: Optional[float] = None,
    tournament_ids: Optional[str] = None,
    limit: int = 20,
) -> Optional[dict]:
    """
    Pipeline completo para un mercado de equipo: descarga la serie
    real, calcula prob_modelo (igual que para jugadores) y la
    probabilidad de mercado a partir de la cuota manual, y devuelve
    edge/ev. Devuelve None si no hay datos suficientes.
    """
    summary = resumen_mercado_equipo(client, team_id, mercado, linea, tournament_ids, limit)
    if summary is None:
        return None

    prob_modelo = estimar_probabilidad(summary, linea)
    if prob_modelo is None:
        return None

    mercado_info = probabilidad_mercado_manual(odds_over, odds_under)
    prob_mercado = mercado_info["prob_mercado"]

    edge = round(prob_modelo - prob_mercado, 2)
    ev = round((prob_modelo / 100 * odds_over - 1) * 100, 2)

    over_info = summary["overs"].get(str(linea), {})

    return {
        "equipo": nombre_equipo,
        "mercado": ETIQUETAS_MERCADO.get(mercado, mercado),
        "line": linea,
        "odds": odds_over,
        "prob_modelo": prob_modelo,
        "prob_mercado": prob_mercado,
        "margen_mercado_pct": round(mercado_info["margen"] * 100, 2),
        "edge": edge,
        "ev": ev,
        "hits": over_info.get("hits"),
        "games": over_info.get("games"),
        "hit_rate": over_info.get("rate"),
        "trend": summary["trend"],
        "consistency": summary["consistency"],
        "mean5": summary["mean5"],
        "mean10": summary["mean10"],
    }
