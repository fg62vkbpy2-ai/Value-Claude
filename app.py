"""
app.py
Interfaz web (Streamlit) del ValueBet Engine.

Uso:
  1. Pega la URL del partido de StatsHub (la de la ficha del partido).
  2. Pulsa "Analizar partido".
  3. Revisa la tabla de picks, ordenada por quality_score (o edge/ev).
  4. Descarga el informe en JSON si quieres guardarlo.

Para desplegarla y usarla desde el iPhone, sigue el README.md.
"""

import json

import pandas as pd
import streamlit as st

from builder import construir_partido
from value_engine import mejores_picks

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

    resumen = partido["summary"]
    st.subheader(f"{resumen['home_team']} vs {resumen['away_team']}")
    st.caption(f"{resumen['date']} · event_id {resumen['event_id']}")

    picks = mejores_picks(partido, top=top_n, ordenar_por=orden, deduplicar=deduplicar)

    if not picks:
        st.warning("No se encontraron picks con edge positivo para este partido.")
    else:
        df = pd.DataFrame(picks)

        columnas = [
            "player", "team", "position", "market", "line",
            "bookmaker", "odds", "prob_modelo", "prob_mercado_consenso",
            "edge", "ev", "quality_score", "hit_rate", "trend",
            "n_casas_consenso", "dispersion_cv", "alerta_dispersion",
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

        informe = {"summary": resumen, "picks": picks}
        nombre_archivo = (
            f"{resumen['date'][:10]}_{resumen['home_team']}_vs_{resumen['away_team']}.json"
        ).replace(" ", "_")

        st.download_button(
            "📥 Descargar informe JSON",
            data=json.dumps(informe, indent=2, ensure_ascii=False),
            file_name=nombre_archivo,
            mime="application/json",
        )

st.divider()
with st.expander("ℹ️ Cómo leer la tabla"):
    st.markdown(
        """
- **prob_modelo**: probabilidad que calcula nuestro modelo a partir del histórico del jugador.
- **prob_mercado_consenso**: probabilidad implícita en las cuotas de varias casas, sin el margen de la casa.
- **edge**: diferencia entre las dos anteriores. Cuanto más alto, más "valor" aparente.
- **ev**: valor esperado en % si apostaras con la mejor cuota disponible.
- **quality_score**: puntuación 0-100 que combina consistencia, probabilidad, EV y tendencia — pensada para no dejarte deslumbrar por un edge alto en una muestra poco fiable.
- **n_casas_consenso** / **dispersion_cv**: cuántas casas coinciden en esa cuota y cuánto varían entre sí. Dispersión alta = las propias casas no tienen claro el número, así que el "edge" es menos fiable.
        """
    )
