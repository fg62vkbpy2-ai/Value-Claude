"""
clasificacion_mercados.py
Añade a value_engine.py el sistema de 4 categorías propuesto por Dani
tras la auditoría del México vs Inglaterra (Gordon/Quiñones descartados
por baja cobertura de datos, no por falta de valor real).

Antes: es_mercado_valido() devolvía True/False y lo que no pasaba el
filtro desaparecía del informe sin dejar rastro.

Ahora: cada mercado con cuota real se clasifica en una de 4 categorías,
y las que necesitan ojo humano llevan explicado el motivo exacto y qué
haría falta para "graduarse" a pick recomendado.

Categorías:
  A) RECOMENDADO       - pasa todos los filtros, EV positivo
  B) VERIFICAR_MANUAL  - EV positivo o histórico fuerte, pero falla
                          algún criterio de cobertura (pocas casas,
                          muestra corta, dispersión alta)
  C) DESCARTADO        - EV negativo o cuota/línea sin valor real
  D) SIN_DATOS         - no hay histórico suficiente para opinar nada

Uso: se importa y se llama a clasificar_mercado() en vez de (o además
de) es_mercado_valido(), dentro de analizar_mercado().
"""

from typing import Optional

# Umbrales ya existentes en value_engine.py (se repiten aquí para que
# este módulo sea autocontenido; en la integración real se importan
# desde value_engine para no duplicar constantes)
MIN_CASAS_CONSENSO = 3
DISPERSION_ALERTA = 0.35
MIN_PARTIDOS_MUESTRA = 6          # por debajo de esto, muestra "corta"
MIN_PARTIDOS_MUESTRA_DURA = 3     # por debajo de esto, ya ni cuenta como dato narrativo fiable

CATEGORIA_RECOMENDADO = "A_RECOMENDADO"
CATEGORIA_VERIFICAR = "B_VERIFICAR_MANUAL"
CATEGORIA_DESCARTADO = "C_DESCARTADO"
CATEGORIA_SIN_DATOS = "D_SIN_DATOS"


COSTE_POR_CASA_FALTANTE = 0.08     # buscar 1 cuota más en otra casa: tarea rápida
COSTE_POR_PARTIDO_FALTANTE = 0.15  # cada partido que falta, además de...
COSTE_FIJO_MUESTRA_CORTA = 0.20    # ...un coste fijo por el propio hecho de tener
                                    # que ir a rastrear histórico a mano (puede
                                    # que ni exista más, a diferencia de una cuota
                                    # que casi siempre se puede encontrar en otro sitio)


def _facilidad_validacion(n_casas: int, n_partidos: int, dispersion_cv: Optional[float]) -> float:
    """
    "¿Qué tan barato es cerrar la brecha de datos que falta?", 0-1.

    No mide qué tan bueno es el pick (eso ya lo hace edge/quality_score),
    mide el ESFUERZO de la verificación pendiente, con coste FIJO por
    tipo de tarea en vez de proporcional a un mínimo pequeño (con
    MIN_CASAS_CONSENSO=3, faltar 2 casas es "faltan 2 de 3" -> penaliza
    igual que faltar la mitad de la muestra, cuando en la práctica
    buscar una cuota más es mucho más rápido que rastrear partidos):

    - Faltar casas de consenso -> barato (revisar 1-2 webs más).
    - Faltar partidos de muestra -> caro, con un coste fijo aparte del
      número exacto que falte, porque el rastreo manual de histórico
      es cualitativamente más laborioso (y puede que no haya más datos
      que encontrar) que comparar cuotas.
    - Dispersión alta sin causa clara -> penaliza solo si de verdad hay
      exceso de dispersión; si no hay dato (típico cuando falta n_casas)
      no se penaliza aquí para no contar el mismo problema dos veces.
    """
    casas_faltantes = max(0, MIN_CASAS_CONSENSO - n_casas)
    facilidad_casas = max(0.0, 1 - COSTE_POR_CASA_FALTANTE * casas_faltantes)

    partidos_faltantes = max(0, MIN_PARTIDOS_MUESTRA - n_partidos)
    if partidos_faltantes > 0:
        coste_muestra = COSTE_FIJO_MUESTRA_CORTA + COSTE_POR_PARTIDO_FALTANTE * partidos_faltantes
    else:
        coste_muestra = 0.0
    facilidad_muestra = max(0.0, 1 - coste_muestra)

    if dispersion_cv is None or dispersion_cv <= DISPERSION_ALERTA:
        facilidad_dispersion = 1.0
    else:
        exceso = dispersion_cv - DISPERSION_ALERTA
        facilidad_dispersion = max(0.0, 1 - exceso / DISPERSION_ALERTA)

    # El problema más caro de resolver domina: si algo requiere ampliar
    # muestra a mano, no importa que las casas estén resueltas, sigue
    # siendo una tarea de varios minutos.
    return min(facilidad_casas, facilidad_muestra, facilidad_dispersion)


def calcular_prioridad_manual(ev: Optional[float], n_casas: int, n_partidos: int,
                                dispersion_cv: Optional[float]) -> float:
    """
    Prioridad de revisión (0-5 estrellas).

    Prioridad = EV potencial x facilidad de validación

    Responde a "¿dónde merece la pena invertir 2 minutos porque hay
    más probabilidad de que se convierta en un pick real?", que es
    distinto de "¿qué tan bueno parece el pick?" (eso ya lo contesta
    el edge/quality_score de siempre).

    - EV negativo o ausente -> 0 estrellas, no hay nada que revisar.
    - EV alto + solo falta 1 dato barato (ej. una 2ª casa) -> máxima.
    - EV alto + falta ampliar muestra a mano -> media (cuesta más).
    """
    if ev is None or ev <= 0:
        return 0.0

    ev_norm = min(ev, 40) / 40  # normaliza el EV a 0-1, cap en 40%
    facilidad = _facilidad_validacion(n_casas, n_partidos, dispersion_cv)

    prioridad = ev_norm * facilidad * 5
    return round(min(prioridad, 5.0), 1)


def motivo_verificacion(n_casas: int, n_partidos: int, dispersion_cv: Optional[float]) -> dict:
    """
    Devuelve qué falló exactamente, la acción concreta a realizar (como
    checklist de tareas, no solo una frase) y qué haría falta para
    pasar a categoría A. Cada entrada es una tarea con su propio
    checkbox, pensada para copiarse tal cual al informe:

    ☐ Buscar una segunda cuota
    ☐ Confirmar alineación
    ☐ Recalcular EV
    """
    faltantes = []
    tareas = []

    if n_casas < MIN_CASAS_CONSENSO:
        faltantes.append({
            "problema": f"Solo {n_casas} casa(s) disponible(s)",
            "para_pasar_a_A": f"Conseguir cuota en al menos {MIN_CASAS_CONSENSO} casas para consenso fiable",
        })
        tareas.append("Buscar una segunda (y tercera) cuota en otra casa")

    if n_partidos < MIN_PARTIDOS_MUESTRA:
        faltantes.append({
            "problema": f"Muestra corta ({n_partidos} partidos)",
            "para_pasar_a_A": f"Ampliar histórico a >= {MIN_PARTIDOS_MUESTRA} partidos (verificar manualmente en StatsHub si existen más)",
        })
        tareas.append("Ampliar muestra manualmente en StatsHub (revisar si el scraper se quedó corto)")

    if dispersion_cv is not None and dispersion_cv > DISPERSION_ALERTA:
        faltantes.append({
            "problema": f"Dispersión alta entre casas (cv={dispersion_cv})",
            "para_pasar_a_A": "Confirmar cuál cuota es la real / descartar errores de una casa suelta",
        })
        tareas.append("Confirmar cuál cuota es la correcta (dispersión alta entre casas)")

    tareas.append("Confirmar alineación titular antes de cerrar el pick")
    tareas.append("Recalcular EV con los datos ya verificados")

    return {"faltantes": faltantes, "tareas": tareas}


def clasificar_mercado(
    summary: dict,
    linea: float,
    mejor_cuota: float,
    prob_modelo: Optional[float],
    prob_mercado_frac: Optional[float],
    n_casas_consenso: int,
    dispersion_cv: Optional[float],
) -> dict:
    """
    Clasifica un mercado con cuota real en A/B/C/D.

    Devuelve un dict con: categoria, prioridad_manual (solo relevante
    para B), motivo (solo relevante para B), y los datos crudos para
    que el informe pueda mostrar el porqué sin recalcular nada.
    """
    over = summary["overs"].get(str(linea)) if summary else None
    if over:
        n_partidos = over["games"]
    elif summary:
        n_partidos = summary.get("n_partidos_validos", 0)
    else:
        n_partidos = 0
    hit_rate = over["rate"] if over else None

    # D: sin datos suficientes ni para opinar
    if summary is None or n_partidos < MIN_PARTIDOS_MUESTRA_DURA or hit_rate is None:
        return {
            "categoria": CATEGORIA_SIN_DATOS,
            "n_partidos": n_partidos,
            "hit_rate": hit_rate,
        }

    # Sin probabilidad de modelo o de mercado -> no se puede calcular edge
    if prob_modelo is None or prob_mercado_frac is None:
        return {
            "categoria": CATEGORIA_SIN_DATOS,
            "n_partidos": n_partidos,
            "hit_rate": hit_rate,
        }

    edge = (prob_modelo / 100 - prob_mercado_frac) * 100
    ev = (prob_modelo / 100 * mejor_cuota - 1) * 100

    cobertura_ok = (
        n_casas_consenso >= MIN_CASAS_CONSENSO
        and n_partidos >= MIN_PARTIDOS_MUESTRA
        and (dispersion_cv is None or dispersion_cv <= DISPERSION_ALERTA)
    )

    if edge <= 0:
        # C: el propio modelo dice que no hay valor, cobertura aparte
        return {
            "categoria": CATEGORIA_DESCARTADO,
            "edge": round(edge, 2),
            "ev": round(ev, 2),
            "n_partidos": n_partidos,
            "hit_rate": hit_rate,
        }

    if cobertura_ok:
        # A: edge positivo Y cobertura suficiente -> pick recomendado
        return {
            "categoria": CATEGORIA_RECOMENDADO,
            "edge": round(edge, 2),
            "ev": round(ev, 2),
            "n_partidos": n_partidos,
            "hit_rate": hit_rate,
        }

    # B: edge positivo pero cobertura insuficiente -> verificar a mano.
    # La prioridad usa el EV real (no solo el edge en puntos porcentuales),
    # porque el EV ya incorpora la cuota conseguida y por tanto refleja
    # mejor "cuánto hay en juego" si el pick se confirma.
    prioridad = calcular_prioridad_manual(ev, n_casas_consenso, n_partidos, dispersion_cv)
    motivo = motivo_verificacion(n_casas_consenso, n_partidos, dispersion_cv)

    return {
        "categoria": CATEGORIA_VERIFICAR,
        "edge": round(edge, 2),
        "ev": round(ev, 2),
        "n_partidos": n_partidos,
        "hit_rate": hit_rate,
        "prioridad_manual": prioridad,
        "motivo": motivo,
    }


def _confianza_estadistica(hit_rate: Optional[float], n_partidos: int) -> str:
    """Etiqueta legible tipo 'Alta (80% hit rate, n=10)'."""
    if hit_rate is None:
        return "Sin dato"
    if hit_rate >= 75 and n_partidos >= 8:
        nivel = "Alta"
    elif hit_rate >= 60:
        nivel = "Media"
    else:
        nivel = "Baja"
    return f"{nivel} ({hit_rate}% hit rate, n={n_partidos})"


def formatear_entrada_verificacion(pick_parcial: dict, clasificacion: dict) -> str:
    """
    Genera el bloque de checklist completo, formato cola de trabajo:

    ⚠️ REVISIÓN MANUAL

    Mercado:
    Quiñones +0.5 TAP

    Motivo de exclusión:
    Solo 1 bookmaker

    Confianza estadística:
    Alta (80% hit rate, n=10)

    Acción necesaria:
    ☐ Buscar una segunda cuota
    ☐ Confirmar alineación
    ☐ Recalcular EV

    Si se confirma:
    → Pasa automáticamente a Categoría A
    """
    motivo_principal = (
        clasificacion["motivo"]["faltantes"][0]["problema"]
        if clasificacion["motivo"]["faltantes"] else "Cobertura insuficiente"
    )
    confianza = _confianza_estadistica(clasificacion.get("hit_rate"), clasificacion.get("n_partidos", 0))
    estrellas = "⭐" * max(1, round(clasificacion["prioridad_manual"]))

    lineas = [
        f"⚠️ REVISIÓN MANUAL  {estrellas}",
        "",
        "Mercado:",
        f"{pick_parcial['player']} +{pick_parcial.get('line', '?')} {pick_parcial['market']}",
        "",
        "Motivo de exclusión:",
        motivo_principal,
        "",
        "Confianza estadística:",
        confianza,
        "",
        "Acción necesaria:",
    ]
    for tarea in clasificacion["motivo"]["tareas"]:
        lineas.append(f"☐ {tarea}")
    lineas += ["", "Si se confirma:", "→ Pasa automáticamente a Categoría A"]

    return "\n".join(lineas)


def formatear_mercado_excluido(pick_parcial: dict, clasificacion: dict) -> str:
    """
    Versión corta para el bloque "Mercados excluidos" del resumen
    ejecutivo -- responde de antemano a "¿por qué no sale X?" sin que
    el usuario tenga que preguntarlo después.

    Anthony Gordon TAP
    Estado: ❌ No rankeado
    Razón: n=5 (<6 mínimo)
    Acción: Ampliar historial
    """
    categoria = clasificacion["categoria"]

    if categoria == CATEGORIA_SIN_DATOS:
        razon = f"n={clasificacion.get('n_partidos', 0)} (< {MIN_PARTIDOS_MUESTRA_DURA} mínimo para opinar)"
        accion = "Ampliar historial / verificar que el jugador tiene más partidos registrados"
    elif categoria == CATEGORIA_DESCARTADO:
        razon = f"EV negativo ({clasificacion.get('edge', 0):+.1f} pts de edge)"
        accion = "Ninguna -- el modelo indica que no hay valor real"
    elif categoria == CATEGORIA_VERIFICAR:
        razon = (
            clasificacion["motivo"]["faltantes"][0]["problema"]
            if clasificacion["motivo"]["faltantes"] else "Cobertura insuficiente"
        )
        accion = clasificacion["motivo"]["tareas"][0] if clasificacion["motivo"]["tareas"] else "Revisar manualmente"
    else:
        return ""  # categoría A no se "excluye", ya está en el ranking principal

    return (
        f"{pick_parcial['player']} {pick_parcial['market']}\n"
        f"Estado: ❌ No rankeado\n"
        f"Razón: {razon}\n"
        f"Acción: {accion}"
    )
