"""
team_context.py
Contexto de equipo rival, usando el endpoint REAL de StatsHub:

  /api/team/{id}/event-statistics?eventType=all&statisticKey=XXXX
      &eventHalf=ALL&tournamentIds=...&limit=20

Cada llamada devuelve una fila por partido con esta forma (verificado
contra la API real, no es una suposición):

  {
    "event_id": ...,
    "home_team_id": ...,
    "away_team_id": ...,
    "home_value": "...",
    "away_value": "...",
    ...
  }

NO es el formato "statistics"/"opponentStatistics" que asumía la v1 de
este archivo (ese formato no existe en la API real; era un error de
diseño basado en una suposición no verificada). Para saber si un valor
es "a favor" o "en contra" del equipo que nos interesa, comparamos
team_id contra home_team_id/away_team_id de cada fila.

Como no hay un statisticKey de "tiros totales" directo, se calcula
como shots_on_target + shots_off_target, cruzando ambas listas por
event_id (misma muestra de partidos).

IMPORTANTE - qué es dato real y qué es aproximación:
- Los promedios por equipo (a_favor / en_contra) SÍ son datos reales
  de StatsHub, calculados sobre los últimos partidos configurados.
- REFERENCIA_LIGA son valores típicos de fútbol de selecciones que se
  usan como "ancla" para saber si el rival está por encima o por
  debajo de lo normal, porque StatsHub no da fácilmente la media de
  toda la competición. Por eso el ajuste final está siempre acotado a
  un máximo de ±TOPE_AJUSTE: el contexto de equipo puede matizar la
  probabilidad del jugador, pero nunca puede dominar sobre su propio
  histórico real.

PENDIENTE (no se toca en esta ronda):
- tournament_ids está fijo por defecto (ver TOURNAMENT_IDS_DEFAULT).
  Es específico de qué competiciones ha jugado el equipo recientemente
  y debería idealmente derivarse del propio equipo en vez de venir
  hardcodeado.
- CAMPO_RIVAL / REFERENCIA_LIGA solo cubren los 5 mercados originales
  (shots, shots_on_target, fouls, was_fouled, tackles). Los mercados
  nuevos (goles, xG, tarjetas...) no tienen todavía un stat de equipo
  equivalente asignado, así que su factor_ajuste será 1.0 (sin ajuste)
  hasta que se decida qué corresponde a cada uno.
- 12 llamadas HTTP nuevas por partido (6 statisticKeys x 2 equipos).
  Si construir_partido tarda demasiado, esto es lo primero a
  paralelizar.
"""

from statistics import mean
from typing import Optional

from scraper import StatsHubClient, TEAM_STAT_KEYS

TOURNAMENT_IDS_DEFAULT = "16,246,308,851"  # ver nota: específico del equipo, revisar

TOPE_AJUSTE = 0.15  # el contexto de equipo nunca mueve la probabilidad más de un ±15%

# mercado de jugador -> (statisticKey de equipo, "a_favor" o "en_contra")
CAMPO_RIVAL = {
    "shots": ("shots_total", "en_contra"),
    "shots_on_target": ("shots_on_target", "en_contra"),
    "fouls": ("fouls", "en_contra"),
    "was_fouled": ("fouls", "a_favor"),
    "tackles": ("shots_on_target", "a_favor"),
}

REFERENCIA_LIGA = {
    "shots_total": 12.0,
    "shots_on_target": 4.5,
    "fouls": 12.0,
}


# ----------------------------------------------------------------------
# Descarga y resumen por equipo
# ----------------------------------------------------------------------

def _dividir_favor_contra(filas: list, team_id: int) -> dict:
    """
    Separa una lista de filas del endpoint event-statistics en valores
    "a favor" y "en contra" del team_id dado, según si aparecía como
    home o away en cada partido.
    """
    a_favor, en_contra = [], []
    for fila in filas:
        home_id = fila.get("home_team_id")
        away_id = fila.get("away_team_id")
        home_val = fila.get("home_value")
        away_val = fila.get("away_value")

        if home_val is None or away_val is None:
            continue

        try:
            home_val, away_val = float(home_val), float(away_val)
        except (TypeError, ValueError):
            continue

        if team_id == home_id:
            a_favor.append(home_val)
            en_contra.append(away_val)
        elif team_id == away_id:
            a_favor.append(away_val)
            en_contra.append(home_val)

    return {
        "a_favor": round(mean(a_favor), 2) if a_favor else None,
        "en_contra": round(mean(en_contra), 2) if en_contra else None,
        "n_partidos": len(a_favor) or len(en_contra),
    }


def construir_resumen_equipo(
    client: StatsHubClient,
    team_id: int,
    nombre_equipo: str = "",
    tournament_ids: str = TOURNAMENT_IDS_DEFAULT,
    limit: int = 20,
) -> dict:
    """
    Descarga y resume todas las stats de equipo (una llamada HTTP por
    statisticKey) y calcula además 'shots_total' como la suma de
    shots_on_target + shots_off_target, partido a partido (cruzando
    por event_id para no mezclar muestras distintas).

    Nunca lanza excepción por un statisticKey aislado que falle: ese
    campo queda como {a_favor: None, en_contra: None, n_partidos: 0}.
    """
    resumen = {"equipo": nombre_equipo}
    crudos = {}

    for etiqueta, stat_key in TEAM_STAT_KEYS.items():
        try:
            filas = client.obtener_stat_equipo(team_id, stat_key, tournament_ids, limit)
        except Exception as e:
            print(f"⚠️ {nombre_equipo} - {etiqueta}: {e}")
            filas = []
        crudos[etiqueta] = filas
        resumen[etiqueta] = _dividir_favor_contra(filas, team_id)

    # shots_total = on_target + off_target, cruzado por event_id.
    on_target_rows = crudos.get("shots_on_target", [])
    off_target_rows = crudos.get("shots_off_target", [])
    off_por_evento = {f.get("event_id"): f for f in off_target_rows}

    filas_total = []
    for fila_on in on_target_rows:
        fila_off = off_por_evento.get(fila_on.get("event_id"))
        if fila_off is None:
            continue
        try:
            filas_total.append({
                "home_team_id": fila_on["home_team_id"],
                "away_team_id": fila_on["away_team_id"],
                "home_value": float(fila_on["home_value"]) + float(fila_off["home_value"]),
                "away_value": float(fila_on["away_value"]) + float(fila_off["away_value"]),
            })
        except (TypeError, ValueError, KeyError):
            continue

    resumen["shots_total"] = _dividir_favor_contra(filas_total, team_id)
    resumen["n_partidos"] = resumen["shots_on_target"]["n_partidos"]

    return resumen


# ----------------------------------------------------------------------
# Factor de ajuste para el modelo del jugador
# ----------------------------------------------------------------------

def factor_ajuste(resumen_rival: dict, mercado: str, tope: float = TOPE_AJUSTE) -> float:
    """
    Convierte el resumen de equipo del rival en un factor multiplicador
    acotado entre (1 - tope) y (1 + tope). Si no hay dato del rival, no
    hay referencia para ese mercado, o el mercado aún no tiene un stat
    de equipo asignado en CAMPO_RIVAL, devuelve 1.0 (sin ajuste) en vez
    de inventar un número.
    """
    if not resumen_rival or mercado not in CAMPO_RIVAL:
        return 1.0

    stat_key, lado = CAMPO_RIVAL[mercado]
    referencia = REFERENCIA_LIGA.get(stat_key)
    valor_rival = resumen_rival.get(stat_key, {}).get(lado)

    if valor_rival is None or not referencia:
        return 1.0

    ratio = valor_rival / referencia
    # Se suaviza el ratio (x0.5) para que un rival "el doble de faltón"
    # no dispare el ajuste al doble, solo lo empuje de forma moderada.
    ajuste = (ratio - 1) * 0.5
    ajuste = max(-tope, min(tope, ajuste))
    return round(1 + ajuste, 4)
