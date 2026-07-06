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

CAMBIO (ronda de las 4 categorías, primera versión): generar_informe_texto()
pasó a recibir `categorias` en vez de `picks`, reutilizando
formatear_informe_texto() de value_engine.py (el bloque verboso con
checklist ⭐/☐ pensado para lectura humana en la UI de Streamlit).

CAMBIO (esta ronda): ese formato verboso, aplicado a un partido real
(53 mercados en verificación manual, 143 excluidos), producía un
informe enorme y quebradizo -- con tantas variantes de datos reales,
algún acceso directo con `[]` en vez de `.get()` provocó un TypeError
en producción (probablemente un mercado con `line=None`, tipo
"tarjeta amarilla" sin línea numérica, o algún pick sin una clave
esperada). Además, para que una IA lea el informe y decida qué
mercados de la categoría B merece la pena investigar más a fondo
(pidiendo cuotas/estadísticas adicionales), una línea compacta por
mercado es mucho más útil que un bloque de 15 líneas repetido 53
veces.

Ahora export_texto.py genera sus PROPIAS líneas compactas para B, C y
D (no reutiliza el formato verboso de value_engine.py, que se
mantiene tal cual para la UI de Streamlit, donde sí interesa el
checklist completo). Todo acceso a campos usa `.get()` con valores
por defecto seguros -- ningún dato faltante debe romper la generación
completa del informe.
"""

from typing import List, Optional


def _fmt_overs(overs: dict) -> str:
    if not overs:
        return ""
    return " ".join(f"{linea}:{datos.get('rate', '?')}%" for linea, datos in overs.items())


def _fmt_lineas_cuota(lineas: List[dict]) -> str:
    if not lineas:
        return ""
    partes = []
    for l in lineas:
        partes.append(
            f"L{l.get('line')}@{l.get('bookmaker')}={l.get('odds')} "
            f"(impl {l.get('prob_implicita')}%, consenso {l.get('prob_mercado_consenso')}%, "
            f"{l.get('n_casas_consenso')} casas, disp {l.get('dispersion_cv')})"
        )
    return " | cuotas: " + "; ".join(partes)


def _fmt_mercado(mercado: str, stats: dict) -> str:
    if "nota" in stats:
        return f"{mercado}: {stats.get('nota')} (media={stats.get('mean10')}, n={stats.get('n_partidos_validos')} partidos)"

    linea = (
        f"{mercado}: media5={stats.get('mean5')} media10={stats.get('mean10')} "
        f"desv={stats.get('stdev')} tend={stats.get('trend')} consist={stats.get('consistency')} "
        f"rival_factor={stats.get('factor_rival')} n_partidos={stats.get('n_partidos_validos')}"
    )
    overs_fmt = _fmt_overs(stats.get("overs", {}))
    if overs_fmt:
        linea += f" | overs: {overs_fmt}"
    linea += _fmt_lineas_cuota(stats.get("lineas_con_cuota", []))
    return linea


# ----------------------------------------------------------------------
# Formato compacto para el informe de IA: una línea por mercado en
# cada categoría, con .get() en todo para que ningún dato faltante
# rompa la generación completa.
# ----------------------------------------------------------------------

def _num(valor, fmt: str = "{:.1f}", default: str = "N/D") -> str:
    """Formatea un número de forma segura; si es None o no numérico, no rompe."""
    if valor is None:
        return default
    try:
        return fmt.format(valor)
    except (TypeError, ValueError):
        return str(valor)


def _linea_pick_recomendado(p: dict) -> str:
    return (
        f"- {p.get('player')} ({p.get('team')}, {p.get('position')}) | "
        f"{p.get('market')} Over {p.get('line')} @ {p.get('odds')} ({p.get('bookmaker')}) "
        f"| prob_modelo={_num(p.get('prob_modelo'))}% vs mercado={_num(p.get('prob_mercado_consenso'))}% "
        f"| edge={_num(p.get('edge'), '{:+.2f}')} ev={_num(p.get('ev'), '{:+.2f}')}% "
        f"quality={p.get('quality_score', 'N/D')} "
        f"| hist {_num(p.get('hit_rate'))}% ({p.get('hits', '?')}/{p.get('games', '?')}) tend={p.get('trend')} "
        f"| rival_factor={p.get('factor_rival')} casas={p.get('n_casas_consenso')} disp={p.get('dispersion_cv')}"
    )


def _linea_pick_verificar(p: dict) -> str:
    """
    Una línea por mercado de categoría B, con las cuotas/estadísticas
    completas (para que la IA -- o quien lea el informe -- pueda decidir
    si merece la pena pedir un dato más y confirmarlo) y el motivo
    principal + acción concreta, en vez del bloque de checklist entero.
    """
    motivo = p.get("motivo_verificacion") or {}
    faltantes = motivo.get("faltantes") or []
    motivo_txt = faltantes[0]["problema"] if faltantes else "cobertura insuficiente"
    tareas = motivo.get("tareas") or []
    accion_txt = tareas[0] if tareas else "revisar manualmente"

    return (
        f"- ⚠️ {p.get('player')} ({p.get('team')}, {p.get('position')}) | "
        f"{p.get('market')} Over {p.get('line')} @ {p.get('odds')} ({p.get('bookmaker')}) "
        f"| prob_modelo={_num(p.get('prob_modelo'))}% vs mercado={_num(p.get('prob_mercado_consenso'))}% "
        f"| edge={_num(p.get('edge'), '{:+.2f}')} ev={_num(p.get('ev'), '{:+.2f}')}% "
        f"prioridad={p.get('prioridad_manual', 'N/D')}⭐ "
        f"| hist {_num(p.get('hit_rate'))}% ({p.get('hits', '?')}/{p.get('games', '?')}) "
        f"| casas={p.get('n_casas_consenso')} disp={p.get('dispersion_cv')} "
        f"| MOTIVO: {motivo_txt} -> {accion_txt}"
    )


def _linea_pick_descartado(p: dict) -> str:
    motivo_descarte = p.get("motivo_descarte")
    if motivo_descarte == "hist_insuficiente":
        etiqueta = "histórico no respalda la apuesta"
    else:
        etiqueta = "EV negativo"

    return (
        f"- {p.get('player')} ({p.get('team')}) | {p.get('market')} Over {p.get('line')} "
        f"@ {p.get('odds')} ({p.get('bookmaker')}) "
        f"| edge={_num(p.get('edge'), '{:+.2f}')} ev={_num(p.get('ev'), '{:+.2f}')}% "
        f"| hist {_num(p.get('hit_rate'))}% ({p.get('hits', '?')}/{p.get('games', '?')}) "
        f"| DESCARTADO: {etiqueta}"
    )


def _linea_pick_sin_datos(p: dict) -> str:
    return (
        f"- {p.get('player')} ({p.get('team')}) | {p.get('market')} "
        f"| cuota vista @ {p.get('odds')} ({p.get('bookmaker')}) pero n={p.get('n_partidos_validos', p.get('games', 0))} "
        f"partidos -- sin datos suficientes para opinar"
    )


def _bloque_picks_compacto(categorias: dict) -> List[str]:
    recomendados = categorias.get("recomendados") or []
    verificar = categorias.get("verificar_manual") or []
    descartados = categorias.get("descartados") or []
    sin_datos = categorias.get("sin_datos") or []

    L = []

    L.append(f"=== 🟢 PICKS RECOMENDADOS ({len(recomendados)}, ordenados por quality_score) ===")
    if not recomendados:
        L.append("(ninguno esta ronda -- revisa la sección de verificación manual, puede haber mercados con edge positivo pendientes de un dato más)")
    for p in recomendados:
        L.append(_linea_pick_recomendado(p))
    L.append("")

    L.append(f"=== 🟡 VERIFICACIÓN MANUAL ({len(verificar)}, por prioridad -- edge positivo pero falta cobertura) ===")
    L.append("(cada línea trae cuotas/estadísticas completas: si decides investigar uno, pide la 2ª cuota o amplía la muestra según el MOTIVO)")
    if not verificar:
        L.append("(ninguno esta ronda)")
    for p in verificar:
        L.append(_linea_pick_verificar(p))
    L.append("")

    L.append(f"=== ⚪ DESCARTADOS ({len(descartados)}, EV negativo o histórico no respalda la apuesta) ===")
    if not descartados:
        L.append("(ninguno esta ronda)")
    for p in descartados:
        L.append(_linea_pick_descartado(p))
    L.append("")

    L.append(f"=== ⚫ SIN DATOS SUFICIENTES ({len(sin_datos)}) ===")
    if not sin_datos:
        L.append("(ninguno esta ronda)")
    for p in sin_datos:
        L.append(_linea_pick_sin_datos(p))

    return L


def generar_informe_texto(partido: dict, categorias: dict, contexto_jugadores: List[dict]) -> str:
    """
    `categorias` es el dict que devuelve mejores_picks_categorizado()
    en value_engine.py: {"recomendados": [...], "verificar_manual":
    [...], "descartados": [...], "sin_datos": [...]}.

    El bloque de picks usa un formato compacto (una línea por mercado
    en cada categoría), pensado para que una IA lea el informe y
    decida qué mercados de "verificación manual" merece la pena
    investigar más -- las cuotas y estadísticas ya están ahí mismo en
    la línea, no hace falta ir a buscarlas aparte.
    """
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

    L.extend(_bloque_picks_compacto(categorias))
    L.append("")

    L.append(f"=== CONTEXTO COMPLETO POR JUGADOR ({len(contexto_jugadores)}) ===")
    L.append("(incluye mercados sin cuota ofertada como contexto narrativo; los que sí tienen cuota real llevan 'cuotas:' con el precio)")
    for j in contexto_jugadores:
        L.append("")
        L.append(f"-- {j['player']} ({j['team']}, {j['position']}) --")
        for mercado, stats in j["stats"].items():
            L.append("  " + _fmt_mercado(mercado, stats))

    return "\n".join(L)
