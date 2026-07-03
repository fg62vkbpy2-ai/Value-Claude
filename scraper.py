"""
scraper.py
Descarga los datos crudos de StatsHub para un partido:
- Info del evento (equipos, fecha, ids)
- Alineaciones probables
- Cuotas por jugador (mercados: tiros, tiros a puerta, faltas, faltas
  recibidas, entradas, goles, asistencias, xG, xA, pases, centros,
  posesión perdida, desposesión, intercepciones, amarillas, fueras
  de juego, paradas...)
- Histórico de rendimiento de cada jugador
- Stats de equipo (corners, tiros, faltas, entradas, paradas...) vía
  /team/{id}/event-statistics

Toda la lógica viene del notebook original de Colab, reorganizada en
una clase reutilizable (StatsHubClient) en vez de funciones sueltas.
"""

import json
import re
from datetime import datetime, UTC
from typing import Any, Dict, List

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0 Safari/537.36"
    )
}

TIMEOUT = 30

# Mercados de jugador que se descargan. Añadir aquí una entrada nueva
# es suficiente para que se descargue automáticamente (nombre interno
# que usamos nosotros -> statType real de StatsHub).
STAT_TYPES = {
    "shots": "shots",
    "shots_on_target": "onTargetScoringAttempt",
    "fouls": "fouls",
    "was_fouled": "wasFouled",
    "tackles": "totalTackle",
    "goals": "goals",
    "assists": "goalAssist",
    "goal_or_assist": "scoredOrAssisted",
    "xg": "expectedGoals",
    "xa": "expectedAssists",
    "xgxa": "xGxA",
    "shots_created": "keyPass",
    "foul_involvements": "foulInvolvements",
    "passes": "totalPass",
    "crosses": "totalCross",
    "possession_lost": "possessionLostCtrl",
    "dispossessed": "dispossessed",
    "interceptions_won": "interceptionWon",
    "yellow_cards": "yellowCard",
    "offsides": "totalOffside",
    "saves": "saves",
}

# Stats de EQUIPO (endpoint distinto: /team/{id}/event-statistics).
# Nombre interno -> statisticKey real de StatsHub.
TEAM_STAT_KEYS = {
    "corners": "cornerKicks",
    "shots_on_target": "totalShotsOnGoal",
    "shots_off_target": "shotsOffGoal",
    "fouls": "fouls",
    "tackles": "totalTackle",
    "saves_portero": "goalkeeperSaves",
}


class StatsHubClient:
    """Cliente HTTP reutilizable para hablar con StatsHub."""

    def __init__(self, debug: bool = False):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.debug = debug

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, url: str) -> requests.Response:
        if self.debug:
            print(f"GET -> {url}")
        try:
            response = self.session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            raise Exception(f"HTTP {response.status_code}\n{url}") from e
        except requests.exceptions.RequestException as e:
            raise Exception(f"No se pudo conectar con StatsHub.\n{e}") from e

    def get_html(self, url: str) -> str:
        return self._get(url).text

    def get_json(self, url: str) -> Any:
        return self._get(url).json()

    # ------------------------------------------------------------------
    # Parseo de la página del partido (__NEXT_DATA__)
    # ------------------------------------------------------------------

    def obtener_evento(self, url: str) -> Dict[str, Any]:
        html = self.get_html(url)
        next_data = self._extraer_next_data(html)
        fixture = self._obtener_fixture(next_data)
        evento_raw = fixture["events"]

        fecha = datetime.fromtimestamp(
            evento_raw["timeStartTimestamp"], UTC
        ).strftime("%Y-%m-%d %H:%M")

        return {
            "eventId": evento_raw["id"],
            "fecha": fecha,
            "homeTeam": fixture["homeTeam"]["name"],
            "awayTeam": fixture["awayTeam"]["name"],
            "homeTeamId": fixture["homeTeam"]["id"],
            "awayTeamId": fixture["awayTeam"]["id"],
            "seasonId": evento_raw["seasonId"],
            "tournamentId": evento_raw["tournamentId"],
            "uniqueTournamentId": evento_raw["uniqueTournamentId"],
            "status": evento_raw["status"],
        }

    @staticmethod
    def _extraer_next_data(html: str) -> dict:
        patron = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
        resultado = re.search(patron, html, re.DOTALL)
        if resultado is None:
            raise Exception("No se encontró __NEXT_DATA__ en la página.")
        return json.loads(resultado.group(1))

    @staticmethod
    def _obtener_fixture(next_data: dict) -> dict:
        try:
            return next_data["props"]["pageProps"]["fixture"]
        except KeyError:
            raise Exception("No existe 'fixture' dentro de __NEXT_DATA__.")

    # ------------------------------------------------------------------
    # Alineaciones
    # ------------------------------------------------------------------

    def obtener_alineaciones(self, event_id: int) -> dict:
        url = f"https://www.statshub.com/api/event/{event_id}/predicted-teams-lineup"
        return self.get_json(url)

    @staticmethod
    def extraer_jugadores_probables(alineaciones: dict) -> List[dict]:
        jugadores = {}
        for equipo in ("homeTeam", "awayTeam"):
            for j in alineaciones[equipo]["data"]:
                jugadores[j["playerId"]] = {
                    "playerId": j["playerId"],
                    "internalId": j["playerInternalId"],
                    "name": j["name"],
                    "team": equipo,
                    "position": j["position"],
                    "markets": {},
                    "performance": [],
                    "summary": {},
                }
        return list(jugadores.values())

    # ------------------------------------------------------------------
    # Mercados (cuotas por jugador)
    # ------------------------------------------------------------------

    def obtener_mercado(self, event_id: int, team_id: int, stat_type: str) -> dict:
        url = (
            f"https://www.statshub.com/api/event/{event_id}/player-odds"
            f"?statType={stat_type}&teamId={team_id}"
        )
        return self.get_json(url)["data"]

    def obtener_todos_los_mercados(self, event_id: int, team_id: int) -> dict:
        mercados = {}
        for nombre, stat in STAT_TYPES.items():
            if self.debug:
                print(f"Descargando {nombre}...")
            try:
                mercados[nombre] = self.obtener_mercado(event_id, team_id, stat)
            except Exception as e:
                if self.debug:
                    print(f"❌ Error en {nombre}: {e}")
                mercados[nombre] = {}
        return mercados

    @staticmethod
    def construir_indice_mercados(mercados: dict) -> dict:
        """playerId -> {mercado: [lineas...]}"""
        indice = {}
        for nombre_mercado, datos in mercados.items():
            player_map = datos.get("playerOddsMap", {})
            for player_id, lineas in player_map.items():
                player_id = int(player_id)
                indice.setdefault(player_id, {})[nombre_mercado] = lineas
        return indice

    # ------------------------------------------------------------------
    # Rendimiento histórico (jugador)
    # ------------------------------------------------------------------

    def obtener_performance(self, player_id: int) -> List[dict]:
        url = f"https://www.statshub.com/api/player/{player_id}/performance"
        data = self.get_json(url)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Stats de EQUIPO (endpoint distinto, una llamada por statisticKey)
    # ------------------------------------------------------------------

    def obtener_stat_equipo(
        self,
        team_id: int,
        statistic_key: str,
        tournament_ids: str,
        limit: int = 20,
    ) -> List[dict]:
        """
        Devuelve una fila por partido: home_team_id, away_team_id,
        home_value, away_value, event_id, etc. Para saber si el valor
        es "a favor" o "en contra" del equipo que nos interesa, hay
        que comparar team_id contra home_team_id/away_team_id de cada
        fila (esto se hace en team_context.py).
        """
        url = (
            f"https://www.statshub.com/api/team/{team_id}/event-statistics"
            f"?eventType=all&statisticKey={statistic_key}&eventHalf=ALL"
            f"&tournamentIds={tournament_ids}&limit={limit}"
        )
        data = self.get_json(url)
        return data.get("data", [])
