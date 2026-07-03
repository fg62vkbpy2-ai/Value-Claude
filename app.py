"""
app.py
Interfaz web (Streamlit) del ValueBet Engine.

Uso:
  1. Pega la URL del partido de StatsHub (la de la ficha del partido).
  2. Pulsa "Analizar partido".
  3. Revisa la tabla de picks, ordenada por quality_score (o edge/ev).
  4. Descarga el informe en JSON si quieres guardarlo (incluye tanto
     los picks apostables como el contexto narrativo completo de cada
     jugador, tenga o no cuota ofertada, para pasárselo a una IA).

Para desplegarla y usarla desde el iPhone, sigue el README.md.
"""

import json

import pandas as pd
import streamlit as st

from builder import construir_partido
from value_engine import mejores_picks, construir_contexto_jugadores
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
            "se muestra la mejor de cada jugador+mercado."
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
        except Exception as e:
            st.error(f"Error al analizar el partido: {e}")
            st.stop()

    progreso_box.empty()

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

    picks = mejores_picks(partido, top=top_n, ordenar_por=orden, deduplicar=deduplicar)

    if not picks:
        st.warning(
            "No se encontraron picks con edge positivo para este partido "
            "(recuerda que sí puede haber contexto útil arriba aunque no haya picks)."
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

    # El informe SIEMPRE se genera y se puede descargar, haya o no picks,
    # porque el contexto narrativo por sí solo ya tiene valor para la IA.
    informe = {
        "summary": resumen,
        "team_context": team_summary,
        "player_context": contexto_jugadores,
        "picks": picks,
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
        informe_texto = generar_informe_texto(partido, picks, contexto_jugadores)
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
- **Contexto adicional (sección aparte)**: histórico de TODOS los mercados de cada jugador, tengan o no cuota ofertada hoy. No son picks — es información extra para que la IA pueda razonar sobre duelos, tendencias y probabilidades de faltas/tiros/etc. aunque no haya nada que apostar directamente ahora mismo.
        """
    )
