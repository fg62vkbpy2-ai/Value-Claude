"""
builder.py
Orquesta la descarga completa de un partido: evento, alineaciones,
mercados (cuotas) y rendimiento histórico + resumen de cada jugador,
más el contexto de equipo (rival) usado para ajustar las probabilidades.
"""

import time
from typing import Callable, Optional

from scraper import StatsHubClient
from stats_engine import completar_jugador
from team_context import construir_resumen_equipo


def generar_match_summary(evento: dict) -> dict:
    return {
        "home_team": evento["homeTeam"],
        "away_team": evento["awayTeam"],
        "event_id": evento["eventId"],
        "date": evento["fecha"],
        "status": evento["status"],
        "competition": evento.get("uniqueTournamentId"),
        "season": evento.get("seasonId"),
    }


def construir_partido(
    url: str,
    debug: bool = False,
    progreso: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Construye el objeto completo del partido: evento + jugadores (con
    mercados, histórico y resumen estadístico ya calculado).

    `progreso` es un callback opcional (por ejemplo st.write) para
    mostrar el avance en la interfaz mientras se descarga.
    """

    def log(msg: str):
        if debug:
            print(msg)
        if progreso:
            progreso(msg)

    inicio = time.time()
    client = StatsHubClient(debug=debug)

    log("📥 Descargando partido...")
    evento = client.obtener_evento(url)
    log(f"✅ Partido: {evento['homeTeam']} vs {evento['awayTeam']}")

    log("📋 Descargando alineaciones...")
    alineaciones = client.obtener_alineaciones(evento["eventId"])
    jugadores = client.extraer_jugadores_probables(alineaciones)
    log(f"👥 {len(jugadores)} jugadores encontrados")

    log("🎯 Descargando mercados...")
    mercados_local = client.obtener_todos_los_mercados(evento["eventId"], evento["homeTeamId"])
    mercados_visitante = client.obtener_todos_los_mercados(evento["eventId"], evento["awayTeamId"])

    todos_los_mercados = {**mercados_local, **mercados_visitante}
    mercados_ok = sum(1 for m in todos_los_mercados.values() if m.get("playerOddsMap"))
    mercados_total = len(mercados_local) + len(mercados_visitante)
    log(f"📊 Mercados con datos: {mercados_ok}/{mercados_total}")

    indice = client.construir_indice_mercados(mercados_local)
    indice.update(client.construir_indice_mercados(mercados_visitante))

    log("🛡️ Descargando contexto de equipo (rival)...")
    try:
        resumen_equipo_home = construir_resumen_equipo(client, evento["homeTeamId"], evento["homeTeam"])
    except Exception as e:
        log(f"⚠️ Error descargando contexto del equipo local: {e}")
        resumen_equipo_home = {}
    try:
        resumen_equipo_away = construir_resumen_equipo(client, evento["awayTeamId"], evento["awayTeam"])
    except Exception as e:
        log(f"⚠️ Error descargando contexto del equipo visitante: {e}")
        resumen_equipo_away = {}

    # El "rival" de un jugador del equipo local es el equipo visitante, y viceversa.
    contexto_por_equipo = {
        "homeTeam": resumen_equipo_away,
        "awayTeam": resumen_equipo_home,
    }

    # Traduce team ("homeTeam"/"awayTeam") al nombre real del equipo,
    # para que el informe final (y lo que se le pase a la IA) diga
    # "Switzerland"/"Algeria" en vez del literal interno.
    nombre_equipo_por_lado = {
        "homeTeam": evento["homeTeam"],
        "awayTeam": evento["awayTeam"],
    }

    log("📊 Completando jugadores (histórico + resumen)...")
    jugadores_final = []
    total = len(jugadores)
    for i, jugador in enumerate(jugadores, start=1):
        log(f"   [{i}/{total}] {jugador.get('name', 'Jugador')}")
        lado = jugador.get("team")
        jugador["rival_context"] = contexto_por_equipo.get(lado, {})
        jugador["team"] = nombre_equipo_por_lado.get(lado, lado)
        jugador = completar_jugador(jugador, indice, client)
        jugadores_final.append(jugador)

    tiempo = round(time.time() - inicio, 2)
    log(f"✅ Partido construido en {tiempo}s")

    return {
        "version": "2.2",
        "summary": generar_match_summary(evento),
        "event": evento,
        "players": jugadores_final,
        "team_summary": {
            "home": resumen_equipo_home,
            "away": resumen_equipo_away,
        },
    }
