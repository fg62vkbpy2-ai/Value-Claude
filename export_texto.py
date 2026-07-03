"""
export_texto.py
Convierte el informe (partido + picks + contexto de jugadores) a texto
plano compacto, en vez de JSON anidado. Mismo contenido, mucho menos
peso: el JSON repite claves como "mean10", "trend", "overs"... una vez
por cada uno de los ~20 jugadores x ~21 mercados. En texto plano cada
mercado es una sola línea legible, sin la sobrecarga estructural del
JSON.

Pensado para copiar/pegar (o subir como .txt) directamente en un chat
de IA (Claude.ai, ChatGPT web...), donde lo que importa no es que el
archivo "parsee" como JSON, sino que la IA pueda leerlo y razonar
sobre él con el menor ruido posible.
"""

from typing import List


def _fmt_overs(overs: dict) -> str:
    if not overs:
        return ""
    return " ".join(f"{linea}:{datos['rate']}%" for linea, datos in overs.items())


def _fmt_lineas_cuota(lineas: List[dict]) -> str:
    if not lineas:
        return ""
    partes = []
    for l in lineas:
        partes.append(
            f"L{l['line']}@{l['bookmaker']}={l['odds']} "
            f"(impl {l['prob_implicita']}%, consenso {l['prob_mercado_consenso']}%, "
            f"{l['n_casas_consenso']} casas, disp {l['dispersion_cv']})"
        )
    return " | cuotas: " + "; ".join(partes)


def _fmt_mercado(mercado: str, stats: dict) -> str:
    if "nota" in stats:
        return f"{mercado}: {stats['nota']} (media={stats['mean10']}, n={stats['n_partidos_validos']} partidos)"

    linea = (
        f"{mercado}: media5={stats['mean5']} media10={stats['mean10']} "
        f"desv={stats['stdev']} tend={stats['trend']} consist={stats['consistency']} "
        f"rival_factor={stats['factor_rival']} n_partidos={stats['n_partidos_validos']}"
    )
    overs_fmt = _fmt_overs(stats.get("overs", {}))
    if overs_fmt:
        linea += f" | overs: {overs_fmt}"
    linea += _fmt_lineas_cuota(stats.get("lineas_con_cuota", []))
    return linea


def generar_informe_texto(partido: dict, picks: List[dict], contexto_jugadores: List[dict]) -> str:
    resumen = partido["summary"]
    team_summary = partido.get("team_summary", {})

    L = []
    L.append(f"PARTIDO: {resumen['home_team']} vs {resumen['away_team']}")
    L.append(f"Fecha: {resumen['date']}  |  event_id {resumen['event_id']}  |  estado: {resumen['status']}")
    L.append("")

    L.append("=== CONTEXTO DE EQUIPO (medias, últimos partidos) ===")
    etiquetas = {
        "shots_total": "Tiros",
        "shots_on_target": "Tiros a puerta",
        "corners": "Córners",
        "fouls": "Faltas",
        "tackles": "Entradas",
        "saves_portero": "Paradas portero",
    }
    for lado, datos in [("Local", team_summary.get("home", {})), ("Visitante", team_summary.get("away", {}))]:
        equipo = datos.get("equipo", lado)
        n_partidos = datos.get("n_partidos", "?")
        L.append(f"-- {equipo} ({lado}, últimos {n_partidos} partidos) --")
        for clave, etiqueta in etiquetas.items():
            v = datos.get(clave, {})
            L.append(f"  {etiqueta}: a favor {v.get('a_favor')}  |  en contra {v.get('en_contra')}")
    L.append("")

    L.append(f"=== PICKS CON EDGE POSITIVO ({len(picks)}, ordenados por quality_score) ===")
    if not picks:
        L.append("(ninguno con edge positivo según el filtro interno; revisa el contexto de jugadores más abajo, ahí sí aparecen mercados con cuota que no pasaron el filtro)")
    for p in picks:
        L.append(
            f"- {p['player']} ({p['team']}, {p['position']}) | {p['market']} Over {p['line']} "
            f"@ {p['odds']} ({p['bookmaker']}) "
            f"| prob_modelo={p['prob_modelo']}% vs mercado={p['prob_mercado_consenso']}% "
            f"| edge={p['edge']:+.2f}  ev={p['ev']:+.2f}%  quality={p['quality_score']} "
            f"| hist {p['hit_rate']}% ({p['hits']}/{p['games']})  tend={p['trend']} "
            f"| rival_factor={p['factor_rival']}  casas={p['n_casas_consenso']}  disp={p['dispersion_cv']}"
        )
    L.append("")

    L.append(f"=== CONTEXTO COMPLETO POR JUGADOR ({len(contexto_jugadores)}) ===")
    L.append("(incluye mercados sin cuota ofertada como contexto narrativo; los que sí tienen cuota real llevan 'cuotas:' con el precio)")
    for j in contexto_jugadores:
        L.append("")
        L.append(f"-- {j['player']} ({j['team']}, {j['position']}) --")
        for mercado, stats in j["stats"].items():
            L.append("  " + _fmt_mercado(mercado, stats))

    return "\n".join(L)
