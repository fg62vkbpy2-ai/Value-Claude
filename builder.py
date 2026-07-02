"""
builder.py
Orquesta la descarga completa de un partido: evento, alineaciones,
mercados (cuotas) y rendimiento histórico + resumen de cada jugador.
"""

import time
from typing import Callable, Optional

from scraper import StatsHubClient
from stats_engine import completar_jugador


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

    indice = client.construir_indice_mercados(mercados_local)
    indice.update(client.construir_indice_mercados(mercados_visitante))

    log("📊 Completando jugadores (histórico + resumen)...")
    jugadores_final = []
    total = len(jugadores)
    for i, jugador in enumerate(jugadores, start=1):
        log(f"   [{i}/{total}] {jugador.get('name', 'Jugador')}")
        jugador = completar_jugador(jugador, indice, client)
        jugadores_final.append(jugador)

    tiempo = round(time.time() - inicio, 2)
    log(f"✅ Partido construido en {tiempo}s")

    return {
        "version": "2.1",
        "summary": generar_match_summary(evento),
        "event": evento,
        "players": jugadores_final,
    }
