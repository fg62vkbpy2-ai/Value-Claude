"""
stats_engine.py
Convierte el histórico crudo de partidos de un jugador en estadísticas
resumidas: medias, tendencia, consistencia y tasas de acierto (overs)
para cada línea de apuesta disponible.

MEJORA respecto a la v1 del notebook original:
- Se descartan partidos donde el jugador jugó muy pocos minutos
  (por defecto < MINUTOS_MINIMOS) a la hora de calcular medias,
  desviación y hit-rates. Antes, un cameo de 5 minutos con 0 tiros
  contaba exactamente igual que un partido completo, lo que
  distorsionaba la media y hacía parecer "poco fiables" a jugadores
  que en realidad sí lo eran cuando jugaban de titular.

MERCADOS NUEVOS (ronda de ampliación):
Se añadieron goles, asistencias, gol o asistencia, xG, xA, xG+xA,
tiros creados, participación en faltas, pases, centros, posesión
perdida, desposesión, intercepciones ganadas, amarillas, fueras de
juego y paradas. El campo de origen de cada uno viene de la misma
"player_statistics_event" que ya se usaba, así que no hace falta
tocar extraer_serie ni calcular_summary_serie: solo el mapeo.

DIAGNÓSTICO (esta ronda):
Los errores de completar_performance/completar_jugador se enrutaban
solo con print(), que en Streamlit Cloud va a los logs del servidor,
no a la interfaz. Si algo falla (rate-limit, timeout...) el usuario
nunca lo veía, solo notaba que player_context/picks salían vacíos sin
explicación. Ahora, si se pasa un callback `log`, los errores van ahí
también (además de seguir imprimiéndose si debug=True en el cliente).

PENDIENTE (no se toca en esta ronda): mercados discretos y con pocos
eventos por partido (goals, assists, yellow_cards...) no encajan bien
con el modelo de distribución normal que usa value_engine.py para la
parte "prob_normal". Habría que revisar ese punto antes de fiarse
demasiado de esos picks.
"""

from statistics import mean, pstdev
from typing import Callable, List, Optional

MAPA_CAMPOS = {
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

CAMPO_MINUTOS = "minutesPlayed"
MINUTOS_MINIMOS = 20  # partidos con menos minutos no cuentan para el histórico


def extraer_serie(
    performance: List[dict],
    campo: str,
    minutos_minimos: int = MINUTOS_MINIMOS,
) -> List[float]:
    """
    Extrae la serie histórica de un campo, descartando partidos donde
    el jugador apenas jugó minutos (cameos que no son representativos
    de su rendimiento habitual).
    """
    serie = []
    for partido in performance:
        stats = partido.get("player_statistics_event", {})
        valor = stats.get(campo)
        minutos = stats.get(CAMPO_MINUTOS)

        if valor is None:
            continue

        # Si tenemos el dato de minutos, filtramos los cameos.
        # Si no lo tenemos (dato ausente), no descartamos el partido
        # para no perder muestra por un campo que a veces falta.
        if minutos is not None and minutos < minutos_minimos:
            continue

        serie.append(valor)

    return serie


def obtener_lineas_mercado(jugador: dict, mercado: str) -> List[float]:
    lineas = []
    for linea in jugador.get("markets", {}).get(mercado, []):
        valor = linea.get("line")
        if valor is not None:
            lineas.append(valor)
    return sorted(set(lineas))


def calcular_overs(serie: List[float], lineas: List[float]) -> dict:
    overs = {}
    if not serie:
        return overs
    for linea in lineas:
        hits = sum(v > linea for v in serie)
        overs[str(linea)] = {
            "hits": hits,
            "games": len(serie),
            "rate": round(hits / len(serie) * 100, 1),
        }
    return overs


def calcular_consistency(serie: List[float]) -> float:
    if len(serie) < 2:
        return 100
    media = mean(serie)
    if media == 0:
        return 100
    cv = pstdev(serie) / media
    return max(0, min(100, round((1 - cv) * 100)))


def calcular_trend(last3: List[float], last10: List[float]) -> str:
    if not last3 or not last10:
        return "STABLE"
    media3 = mean(last3)
    media10 = mean(last10)
    if media10 == 0:
        return "STABLE"
    if media3 > media10 * 1.15:
        return "UP"
    if media3 < media10 * 0.85:
        return "DOWN"
    return "STABLE"


def calcular_summary_serie(serie: List[float], lineas: List[float]) -> Optional[dict]:
    if not serie:
        return None

    last10 = serie[:10]
    last5 = last10[:5]
    last3 = last10[:3]

    media10 = mean(last10)
    desviacion = pstdev(last10)

    return {
        "last3": last3,
        "last5": last5,
        "last10": last10,
        "mean5": round(mean(last5), 2),
        "mean10": round(media10, 2),
        "stdev": round(desviacion, 2),
        "trend": calcular_trend(last3, last10),
        "consistency": calcular_consistency(last10),
        "overs": calcular_overs(last10, lineas),
        # Nº de partidos que quedaron tras filtrar cameos irrelevantes.
        "n_partidos_validos": len(serie),
    }


def generar_summary_jugador(jugador: dict) -> dict:
    performance = jugador.get("performance", [])
    summary = {}
    for mercado, campo in MAPA_CAMPOS.items():
        serie = extraer_serie(performance, campo)
        if serie:
            lineas = obtener_lineas_mercado(jugador, mercado)
            summary[mercado] = calcular_summary_serie(serie, lineas)
    return summary


def completar_performance(
    jugador: dict,
    client,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Añade el histórico de partidos al jugador usando el StatsHubClient.
    Nunca lanza excepción; si falla, deja una lista vacía.

    Si se pasa `log`, el error también se envía ahí (visible en la UI),
    no solo por print() (que en Streamlit Cloud va a los logs del
    servidor, invisibles para el usuario).
    """
    try:
        jugador["performance"] = client.obtener_performance(jugador["playerId"])
    except Exception as e:
        mensaje = f"⚠️ Error descargando performance de {jugador.get('name', 'Jugador')}: {e}"
        print(mensaje)
        if log:
            log(f"      ❌ {mensaje}")
        jugador["performance"] = []
    return jugador


def completar_jugador(
    jugador: dict,
    indice_mercados: dict,
    client,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Enriquece un jugador con sus mercados, histórico y resumen estadístico.
    Nunca interrumpe la generación del partido por un fallo aislado.
    """
    jugador["markets"] = indice_mercados.get(jugador["playerId"], {})

    try:
        jugador = completar_performance(jugador, client, log=log)
    except Exception as e:
        mensaje = f"⚠️ {jugador.get('name', 'Jugador')} -> Error en performance: {e}"
        print(mensaje)
        if log:
            log(f"      ❌ {mensaje}")

    try:
        jugador["summary"] = generar_summary_jugador(jugador)
    except Exception as e:
        mensaje = f"⚠️ {jugador.get('name', 'Jugador')} -> Error en summary: {e}"
        print(mensaje)
        if log:
            log(f"      ❌ {mensaje}")
        jugador["summary"] = {}

    if log:
        n_partidos = len(jugador.get("performance", []))
        n_mercados_con_historial = len(jugador.get("summary", {}))
        log(f"      → {n_partidos} partidos de histórico, {n_mercados_con_historial} mercados con datos")

    return jugador
