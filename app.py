"""
app.py
Interfaz web (Streamlit) del ValueBet Engine.

Uso:
  1. Pega la URL del partido de StatsHub (la de la ficha del partido).
  2. Pulsa "Analizar partido".
  3. Revisa la tabla de picks recomendados (categoría A), ordenada por
     quality_score (o edge/ev), y las secciones de verificación manual
     (categoría B) y mercados excluidos (C/D) más abajo.
  4. Descarga el informe en JSON o TXT si quieres guardarlo/pasárselo
     a una IA (incluye picks recomendados, cola de verificación manual,
     mercados descartados y el contexto narrativo completo).

Para desplegarla y usarla desde el iPhone, sigue el README.md.

CAMBIO (ronda de las 4 categorías): antes se llamaba a mejores_picks(),
que solo devuelve la categoría A (pick recomendado) -- las categorías
B/C/D que añadimos en value_engine.py/clasificacion_mercados.py nunca
llegaban a la interfaz ni al informe descargable, aunque el motor ya
las calculaba. Ahora se usa mejores_picks_categorizado(), que devuelve
las 4, y se muestran todas: recomendados en la tabla principal (como
antes), verificación manual y mercados excluidos en secciones nuevas.
"""

import json

import pandas as pd
import streamlit as st

from builder import construir_partido
from value_engine import mejores_picks_categorizado, construir_contexto_jugadores, ordenar_picks
from clasificacion_mercados import formatear_entrada_verificacion, formatear_mercado_excluido
from export_texto import generar_informe_texto

st.set_page_config(page_title="ValueBet Engine", page_icon="⚽", layout="wide")

st.title("⚽ ValueBet Engine")
st.caption("Análisis de valor en mercados de jugador a partir de StatsHub")

with st.form("analizar"):
    url = st.text_input(
        "URL del partido en StatsHub",
        placeholder="https://www.statshub.com/fixture/spain-vs-austria-mr2za6/307833",
    )
    col1, col2 = st.columns(2)
    with col1:
        top_n = st.slider("Nº máximo de picks a mostrar", 5, 50, 20)
    with col2:
        orden = st.selectbox(
            "Ordenar por",
            ["quality_score", "edge", "ev", "prob_modelo"],
            index=0,
        )
    deduplicar = st.checkbox(
        "Mostrar solo la mejor línea por jugador + mercado",
        value=True,
        help=(
            "Si un jugador tiene varias líneas del mismo mercado "
            "(p. ej. tackles 0.5, 1.5, 2.5...), son apuestas muy "
            "correlacionadas entre sí. Con esta opción activada solo "
            "se muestra la mejor de cada jugador+mercado (por categoría)."
        ),
    )
    enviado = st.form_submit_button("Analizar partido")

if enviado:
    if not url.strip():
        st.error("Pega antes la URL del partido.")
        st.stop()

    progreso_box = st.empty()
    log_lines = []

    def progreso(msg):
        log_lines.append(msg)
        progreso_box.code("\n".join(log_lines[-8:]))

    with st.spinner("Descargando y analizando el partido... (puede tardar ~20-30s)"):
        try:
            partido = construir_partido(url.strip(), debug=False, progreso=progreso)
            # Guardamos en session_state: sin esto, en cuanto se toque
            # cualquier widget fuera del formulario (la calculadora de
            # equipo, por ejemplo), Streamlit vuelve a ejecutar el
            # script desde arriba, "enviado" vuelve a ser False, y todo
            # este bloque desaparecería como si no hubiera pasado nada.
            st.session_state["partido"] = partido
            st.session_state["log_lines"] = log_lines
        except Exception as e:
            st.error(f"Error al analizar el partido: {e}")
            st.stop()

    progreso_box.empty()

if "partido" in st.session_state:
    partido = st.session_state["partido"]
    log_lines = st.session_state.get("log_lines", [])

    with st.expander("🔍 Log de descarga (para diagnóstico)"):
        st.code("\n".join(log_lines))

    resumen = partido["summary"]
    st.subheader(f"{resumen['home_team']} vs {resumen['away_team']}")
    st.caption(f"{resumen['date']} · event_id {resumen['event_id']}")

    team_summary = partido.get("team_summary", {})
    if team_summary:
        with st.expander("📊 Contexto de equipo (a favor / en contra, últimos partidos)"):
            st.caption(
                "No hay cuotas de equipo en StatsHub, así que esto no genera picks — "
                "es contexto verificado para leer tú mismo o pegárselo a un LLM en vez "
                "de que se lo invente."
            )
            filas = []
            etiquetas_bonitas = {
                "shots_total": "Tiros",
                "shots_on_target": "Tiros a puerta",
                "corners": "Córners",
                "fouls": "Faltas",
                "tackles": "Entradas",
                "saves_portero": "Paradas portero",
            }
            for lado, datos in [("home", team_summary.get("home", {})), ("away", team_summary.get("away", {}))]:
                for clave, etiqueta in etiquetas_bonitas.items():
                    valores = datos.get(clave, {})
                    filas.append({
                        "equipo": datos.get("equipo", lado),
                        "métrica": etiqueta,
                        "a favor (media)": valores.get("a_favor"),
                        "en contra (media)": valores.get("en_contra"),
                    })
            df_equipos = pd.DataFrame(filas)
            st.dataframe(df_equipos, use_container_width=True, hide_index=True)
            st.caption(
                f"Basado en los últimos {team_summary.get('home', {}).get('n_partidos', '?')} "
                f"partidos de {team_summary.get('home', {}).get('equipo', 'local')} y "
                f"{team_summary.get('away', {}).get('n_partidos', '?')} de "
                f"{team_summary.get('away', {}).get('equipo', 'visitante')}."
            )

    # Calculadora de mercados de EQUIPO con cuota manual real (ver
    # team_market.py). StatsHub no da cuotas de equipo, así que esto
    # nunca aparece en la tabla de picks automática -- aquí introduces
    # tú la cuota real que ves en tu casa de apuestas, y se calcula
    # prob_modelo con el mismo motor que usamos para jugadores, sobre
    # el histórico real del equipo (no una estimación de un LLM).
    with st.expander("🧮 Calculadora de mercados de equipo (cuota real, sin StatsHub)"):
        st.caption(
            "Para tiros, tiros a puerta, córners, faltas, entradas o paradas de "
            "portero de un EQUIPO. Introduce la cuota real de tu casa de apuestas "
            "(idealmente 'Más de' y 'Menos de' para devigar de verdad; si solo das "
            "una, se asume un margen del 6%)."
        )
        import team_market

        col_eq, col_mkt, col_line = st.columns(3)
        with col_eq:
            lado_calc = st.radio(
                "Equipo", ["Local", "Visitante"], horizontal=True, key="lado_calc"
            )
        with col_mkt:
            mercado_calc = st.selectbox(
                "Mercado",
                list(team_market.ETIQUETAS_MERCADO.keys()),
                format_func=lambda k: team_market.ETIQUETAS_MERCADO[k],
                key="mercado_calc",
            )
        with col_line:
            linea_calc = st.number_input(
                "Línea", min_value=0.5, value=13.5, step=0.5, key="linea_calc"
            )

        col_over, col_under = st.columns(2)
        with col_over:
            odds_over_calc = st.number_input(
                "Cuota 'Más de'", min_value=1.01, value=1.90, step=0.01, key="odds_over_calc"
            )
        with col_under:
            odds_under_calc = st.number_input(
                "Cuota 'Menos de' (opcional, mejora la precisión)",
                min_value=0.0, value=0.0, step=0.01, key="odds_under_calc",
            )

        if st.button("Calcular", key="btn_calc_equipo"):
            evento = partido["event"]
            team_id_calc = evento["homeTeamId"] if lado_calc == "Local" else evento["awayTeamId"]
            nombre_calc = resumen["home_team"] if lado_calc == "Local" else resumen["away_team"]

            from scraper import StatsHubClient
            client_calc = StatsHubClient()
            resultado_calc = team_market.analizar_pick_equipo(
                client_calc, team_id_calc, nombre_calc, mercado_calc, linea_calc,
                odds_over_calc, odds_under_calc if odds_under_calc > 0 else None,
            )

            if resultado_calc is None:
                st.error("No hay datos suficientes de StatsHub para este mercado/equipo.")
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("prob_modelo", f"{resultado_calc['prob_modelo']}%")
                c2.metric("prob_mercado", f"{resultado_calc['prob_mercado']}%")
                c3.metric("edge", f"{resultado_calc['edge']:+.2f}")
                c4.metric("EV", f"{resultado_calc['ev']:+.2f}%")
                st.caption(
                    f"Histórico: {resultado_calc['hit_rate']}% "
                    f"({resultado_calc['hits']}/{resultado_calc['games']}) · "
                    f"tendencia {resultado_calc['trend']} · "
                    f"media10={resultado_calc['mean10']} · "
                    f"margen mercado estimado {resultado_calc['margen_mercado_pct']}%"
                    + (
                        " (sin 'Menos de', margen asumido 6%)"
                        if odds_under_calc == 0
                        else " (devig real con las dos cuotas)"
                    )
                )

    # Contexto narrativo: TODOS los mercados con histórico, tengan o no
    # cuota ofertada ahora mismo. No son picks, son datos para que la
    # IA razone (duelos, tendencias, faltas probables...) aunque no
    # haya nada que apostar directamente sobre ese dato en concreto.
    contexto_jugadores = construir_contexto_jugadores(partido)

    if contexto_jugadores:
        with st.expander(
            f"📎 Contexto adicional de jugadores ({len(contexto_jugadores)} jugadores, "
            f"incluye mercados sin cuota ofertada)"
        ):
            st.caption(
                "Esto no son picks (no todos tienen cuota real para calcular EV), "
                "es el histórico completo de cada jugador en todos los mercados "
                "disponibles. Se incluye igualmente en el informe descargable para "
                "dar más contexto a la IA."
            )
            filas_contexto = []
            for j in contexto_jugadores:
                for mercado, stats in j["stats"].items():
                    filas_contexto.append({
                        "player": j["player"],
                        "team": j["team"],
                        "position": j["position"],
                        "market": mercado,
                        "mean10": stats.get("mean10"),
                        "trend": stats.get("trend", "-"),
                        "consistency": stats.get("consistency"),
                        "factor_rival": stats.get("factor_rival"),
                        "n_partidos": stats.get("n_partidos_validos"),
                        "con_cuota_hoy": stats.get("tiene_cuota_actualmente", False),
                        "nota": stats.get("nota", ""),
                    })
            st.dataframe(pd.DataFrame(filas_contexto), use_container_width=True, height=400, hide_index=True)

    # Motor con las 4 categorías (A/B/C/D). "picks" (variable de abajo)
    # sigue siendo solo la categoría A, para no tocar el resto de la UI
    # ni el formato de la tabla/descargas -- pero ahora viene de
    # categorias["recomendados"], no de mejores_picks() directamente.
    categorias = mejores_picks_categorizado(partido, deduplicar=deduplicar)
    picks = ordenar_picks(categorias["recomendados"], campo=orden)[:top_n]

    if not picks:
        st.warning(
            "No hay picks en categoría A (recomendados) para este partido. "
            "Revisa la sección '🟡 Verificación manual' más abajo -- puede haber "
            "mercados con edge positivo que solo necesitan un dato más para "
            "confirmarse (otra casa, ampliar muestra...)."
        )
    else:
        df = pd.DataFrame(picks)

        columnas = [
            "player", "team", "position", "market", "line",
            "bookmaker", "odds", "prob_modelo", "prob_mercado_consenso",
            "edge", "ev", "quality_score", "hit_rate", "games", "trend",
            "factor_rival", "n_casas_consenso", "dispersion_cv", "alerta_dispersion",
        ]
        columnas = [c for c in columnas if c in df.columns]

        st.dataframe(
            df[columnas].style.format({
                "odds": "{:.2f}",
                "prob_modelo": "{:.1f}%",
                "prob_mercado_consenso": "{:.1f}%",
                "edge": "{:+.2f}",
                "ev": "{:+.2f}%",
                "hit_rate": "{:.1f}%",
                "dispersion_cv": "{:.3f}",
            }),
            use_container_width=True,
            height=600,
        )

    # 🟡 Verificación manual (categoría B): edge positivo pero falta
    # algo de cobertura (pocas casas, muestra corta, dispersión alta).
    # Ordenados por prioridad_manual: primero los que menos esfuerzo
    # cuestan de confirmar para el EV que ofrecen.
    verificar = categorias["verificar_manual"]
    with st.expander(f"🟡 Verificación manual ({len(verificar)} mercados, por prioridad)", expanded=bool(verificar)):
        if not verificar:
            st.caption("Ninguno esta ronda.")
        else:
            st.caption(
                "Mercados con valor aparente que el motor no puede confirmar solo "
                "todavía -- cada uno lleva su checklist de qué falta y qué haría "
                "falta para pasar a la tabla de recomendados."
            )
            for p in verificar:
                st.markdown(
                    formatear_entrada_verificacion(p, {
                        "hit_rate": p.get("hit_rate"),
                        "n_partidos": p.get("n_partidos_validos", p.get("games")),
                        "prioridad_manual": p["prioridad_manual"],
                        "motivo": p["motivo_verificacion"],
                    }).replace("\n", "  \n")
                )
                st.divider()

    # ⚪ Mercados excluidos (C: descartados por EV negativo, D: sin
    # datos suficientes). Resumen corto -- responde de antemano a
    # "¿por qué no sale X?" sin que haya que preguntarlo después.
    excluidos = categorias["descartados"] + categorias["sin_datos"]
    with st.expander(f"⚪ Mercados excluidos ({len(excluidos)})"):
        if not excluidos:
            st.caption("Ninguno esta ronda.")
        else:
            for p in excluidos:
                clasificacion_min = {
                    "categoria": p["categoria"],
                    "edge": p.get("edge"),
                    "hit_rate": p.get("hit_rate"),
                    "motivo_descarte": p.get("motivo_descarte"),
                    "n_partidos": p.get("n_partidos_validos", p.get("games", 0)),
                    "motivo": p.get("motivo_verificacion", {"faltantes": [], "tareas": []}),
                }
                texto = formatear_mercado_excluido(p, clasificacion_min)
                if texto:
                    st.text(texto)
                    st.divider()

    # El informe SIEMPRE se genera y se puede descargar, haya o no picks,
    # porque el contexto narrativo por sí solo ya tiene valor para la IA.
    informe = {
        "summary": resumen,
        "team_context": team_summary,
        "player_context": contexto_jugadores,
        "picks": picks,  # compatibilidad: solo categoría A, como antes
        "categorias": {
            "recomendados": picks,
            "verificar_manual": categorias["verificar_manual"],
            "descartados": categorias["descartados"],
            "sin_datos": categorias["sin_datos"],
        },
    }
    nombre_sugerido = (
        f"{resumen['date'][:10]}_{resumen['home_team']}_vs_{resumen['away_team']}"
    ).replace(" ", "_")

    st.divider()
    nombre_base = st.text_input(
        "Nombre del archivo (sin extensión, se usa para ambas descargas)",
        value=nombre_sugerido,
    )
    nombre_base = nombre_base.strip() or nombre_sugerido

    col_json, col_texto = st.columns(2)
    with col_json:
        st.download_button(
            "📥 Descargar informe JSON (picks + contexto completo)",
            data=json.dumps(informe, indent=2, ensure_ascii=False),
            file_name=f"{nombre_base}.json",
            mime="application/json",
            use_container_width=True,
        )
    with col_texto:
        informe_texto = generar_informe_texto(partido, categorias, contexto_jugadores)
        st.download_button(
            "📝 Descargar informe compacto (.txt, para pegar en la IA)",
            data=informe_texto,
            file_name=f"{nombre_base}.txt",
            mime="text/plain",
            use_container_width=True,
        )

    with st.expander("👁️ Vista previa del informe compacto (con botón de copiar)"):
        st.caption(
            "Mismo contenido que el JSON, en texto plano sin la sobrecarga "
            "estructural de claves repetidas -- pensado para pegar directo "
            "en un chat de IA. Pasa el ratón por encima para ver el icono "
            "de copiar."
        )
        st.code(informe_texto, language=None)

st.divider()
with st.expander("ℹ️ Cómo leer la tabla"):
    st.markdown(
        """
- **prob_modelo**: probabilidad que calcula nuestro modelo a partir del histórico del jugador.
- **prob_mercado_consenso**: probabilidad implícita en las cuotas de varias casas, sin el margen de la casa.
- **edge**: diferencia entre las dos anteriores. Cuanto más alto, más "valor" aparente.
- **ev**: valor esperado en % si apostaras con la mejor cuota disponible.
- **quality_score**: puntuación 0-100 que combina consistencia, probabilidad, EV y tendencia — pensada para no dejarte deslumbrar por un edge alto en una muestra poco fiable.
- **hit_rate** / **games**: cuántas veces superó la línea de las últimas partidos "válidas" (excluyendo cameos de pocos minutos). Con `games` bajo (6-7) el hit_rate es menos fiable que con 9-10 — el `quality_score` ya lo penaliza, pero conviene mirarlo directamente si vas a apostar fuerte.
- **factor_rival**: ajuste (±15% máx.) aplicado a la media del jugador según cómo de permeable es el rival en ese mercado (p. ej. un rival que concede muchos tiros sube ligeramente la probabilidad de "shots"). 1.00 = sin ajuste, no había suficiente dato del rival.
- **n_casas_consenso** / **dispersion_cv**: cuántas casas coinciden en esa cuota y cuánto varían entre sí. Dispersión alta = las propias casas no tienen claro el número, así que el "edge" es menos fiable.
- **🟡 Verificación manual**: mercados con edge positivo que el motor no puede confirmar solo todavía (pocas casas, muestra corta, dispersión alta). Cada uno trae un checklist de qué falta -- si se resuelve, el mercado pasaría a la tabla de recomendados.
- **⚪ Mercados excluidos**: mercados con EV negativo (el modelo dice que no hay valor) o sin datos suficientes ni para opinar. Se listan para responder de antemano a "¿por qué no sale X?".
- **Contexto adicional (sección aparte)**: histórico de TODOS los mercados de cada jugador, tengan o no cuota ofertada hoy. No son picks — es información extra para que la IA pueda razonar sobre duelos, tendencias y probabilidades de faltas/tiros/etc. aunque no haya nada que apostar directamente ahora mismo.
- **🧮 Calculadora de mercados de equipo**: como StatsHub no da cuotas de equipo, aquí introduces tú la cuota real y se calcula todo con el mismo motor que los jugadores, sobre el histórico real del equipo — no es una estimación de una IA.
        """
    )
