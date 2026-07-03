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

Para saber si un valor es "a favor" o "en contra" del equipo que nos
interesa, comparamos team_id contra home_team_id/away_team_id de cada
fila.

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

FIX (esta ronda) - tournament_ids hardcodeado:
La versión anterior fijaba TOURNAMENT_IDS_DEFAULT = "16,246,308,851"
para todas las llamadas. Verificado con Colombia (team_id 4820): sin
pasar tournamentIds en absoluto, la API ya devuelve por sí sola
exactamente las mismas 4 competiciones que StatsHub marca activas por
defecto en su filtro (FIFA World Cup=16, Copa América=133, World Cup
Qualification CONMEBOL=295, Int. Friendly Games=851) y el promedio
resultante coincide al milímetro (12.90) con el que muestra la propia
app. La lista hardcodeada tenía dos problemas: incluía IDs que no
existen para Colombia (246, 308, en vez de 133, 295 que son los
reales), y aunque estuviera bien para una confederación, se rompería
en silencio para cualquier equipo de otra (AFC, CAF...). Por eso
`tournament_ids` ahora es None por defecto: se deja que la propia API
decida, en vez de mantener una lista por confederación imposible de
cubrir con precisión.

También se detectó (no achacable a nuestro código) que la vista
"Gráficos" de la app de StatsHub parece contar partidos sin dato como
si valieran 0 en el promedio, mientras que su vista "Tablas" los
excluye correctamente -- por eso una comparación inicial contra
"Gráficos" (9.93) no cuadraba, y contra "Tablas" (12.90, calculado a
mano sumando los 20 valores reales) sí cuadra con nuestro dato.

FIX (ronda anterior) - doble conteo en shots_total:
La versión anterior calculaba "shots_total" sumando manualmente
totalShotsOnGoal + shotsOffGoal, cruzando dos listas por event_id.
Verificado contra la API real (y contra las gráficas de la propia
StatsHub): "totalShotsOnGoal" YA ES el total de tiros (a puerta +
fuera + bloqueados) pese a su nombre engañoso -> sumarle shotsOffGoal
aparte contaba los tiros fuera dos veces. Ejemplo real: Australia daba
12.05 de "tiros totales" con el cálculo viejo, cuando el dato real
(confirmado en la app de StatsHub) es 7.6.

Ahora "shots_total" se descarga directo desde TEAM_STAT_KEYS igual que
el resto de campos, sin ningún cálculo manual. Y "shots_on_target" ya
no apunta a "totalShotsOnGoal" (que no es "a puerta", es el total),
sino a "shotsOnGoal" (el campo real de tiros a puerta, también
confirmado contra la API: 2.9 de media para Australia, coincide con
la pestaña "SOT" de StatsHub).

Se ha eliminado por tanto todo el cruce por event_id (crudos,
off_por_evento, filas_total) que existía en la versión anterior — ya
no hace falta, y además ahorra 1 llamada HTTP por equipo (ya no se
pide "shots_off_target" aparte).

PENDIENTE (no se toca en esta ronda):
- CAMPO_RIVAL / REFERENCIA_LIGA solo cubren los 5 mercados originales
  (shots, shots_on_target, fouls, was_fouled, tackles). Los mercados
  nuevos (goles, xG, tarjetas...) no tienen todavía un stat de equipo
  equivalente asignado, así que su factor_ajuste será 1.0 (sin ajuste)
  hasta que se decida qué corresponde a cada uno.
"""

from statistics import mean
from typing import Optional

from scraper import StatsHubClient, TEAM_STAT_KEYS

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
    tournament_ids: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Descarga y resume todas las stats de equipo (una llamada HTTP por
    statisticKey, incluido shots_total directamente -- ya no hace
    falta calcularlo a mano cruzando dos listas, ver nota del FIX
    arriba).

    Nunca lanza excepción por un statisticKey aislado que falle: ese
    campo queda como {a_favor: None, en_contra: None, n_partidos: 0}.
    """
    resumen = {"equipo": nombre_equipo}

    for etiqueta, stat_key in TEAM_STAT_KEYS.items():
        try:
            filas = client.obtener_stat_equipo(team_id, stat_key, tournament_ids, limit)
        except Exception as e:
            print(f"⚠️ {nombre_equipo} - {etiqueta}: {e}")
            filas = []
        resumen[etiqueta] = _dividir_favor_contra(filas, team_id)

    resumen["n_partidos"] = resumen.get("shots_total", {}).get("n_partidos", 0)

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
