"""
Motor de reglas normativas POT/EOT Colombia.
Cada regla referencia la norma exacta que incumple.
"""
from dataclasses import dataclass, field


@dataclass
class Alerta:
    severidad: str          # ALTA / MEDIA / BAJA / OK
    titulo: str
    descripcion: str
    norma_referencia: str
    accion_recomendada: str
    predios_afectados: int = 0
    uso_incompatible: str = ""


# Matriz de compatibilidad de usos (simplificada)
# Formato: {uso: {restriccion: accion}}
INCOMPATIBILIDADES = {
    "Habitacional": {
        "amenaza_alta":   "ALTA",
        "area_protegida": "ALTA",
    },
    "Comercial": {
        "amenaza_alta":   "ALTA",
        "area_protegida": "MEDIA",
    },
    "Industrial": {
        "amenaza_alta":   "ALTA",
        "area_protegida": "ALTA",
    },
    "Agropecuario": {
        "area_protegida": "MEDIA",
    },
}


def evaluar_alertas(
    conteos: dict,
    total_predios: int,
    municipio: str,
    departamento: str,
    predios_conflicto: list[dict] | None = None,
) -> list[Alerta]:
    """
    Genera alertas normativas a partir de los conteos del análisis espacial.
    """
    alertas: list[Alerta] = []
    predios_conflicto = predios_conflicto or []

    c_alta = conteos.get("amenaza_alta", 0)
    c_media = conteos.get("amenaza_media", 0)
    c_prot = conteos.get("area_protegida", 0)
    c_local = conteos.get("capa_local", 0)

    # --- Alerta: predios en amenaza ALTA ---
    if c_alta > 0:
        pct = round(c_alta / total_predios * 100, 1) if total_predios else 0
        alertas.append(Alerta(
            severidad="ALTA",
            titulo="Predios en zona de amenaza alta por inundación",
            descripcion=(
                f"{c_alta} predio(s) ({pct}% del total) se superponen con zonas de amenaza "
                f"ALTA por inundación en el municipio de {municipio}."
            ),
            norma_referencia=(
                "Decreto 1807/2014 arts. 11–13 · Ley 1523/2012 art. 39 · "
                "Art. 35 Ley 388/1997 · Art. 32 nivel 1 Ley 2294/2023"
            ),
            accion_recomendada=(
                "1. Prohibir nuevas edificaciones en estas áreas mientras se realiza "
                "estudio detallado de mitigabilidad (Decreto 1807 art. 13). "
                "2. Si el riesgo no es mitigable, reclasificar como suelo de protección "
                "(art. 35 Ley 388/1997) en la próxima revisión del EOT. "
                "3. Registrar en el Expediente Municipal (art. 112 Ley 388/1997)."
            ),
            predios_afectados=c_alta,
        ))

    # --- Alerta: predios en amenaza MEDIA ---
    if c_media > 0:
        alertas.append(Alerta(
            severidad="MEDIA",
            titulo="Predios en zona de amenaza media por inundación",
            descripcion=(
                f"{c_media} predio(s) en zona de amenaza MEDIA por inundación "
                f"(período de retorno 20–50 años) en {municipio}."
            ),
            norma_referencia="Decreto 1807/2014 art. 10 · Decreto 1232/2020",
            accion_recomendada=(
                "Exigir estudio complementario de inundabilidad como requisito "
                "previo a toda licencia de construcción en el área. "
                "Incorporar condición de amenaza en la norma urbanística del EOT."
            ),
            predios_afectados=c_media,
        ))

    # --- Alerta: predios en área protegida ---
    if c_prot > 0:
        alertas.append(Alerta(
            severidad="ALTA",
            titulo="Predios con superposición en área protegida SINAP/RUNAP",
            descripcion=(
                f"{c_prot} predio(s) en {municipio} se superponen con áreas "
                f"protegidas del SINAP."
            ),
            norma_referencia=(
                "Decreto 2372/2010 · Art. 10 Ley 388/1997 · "
                "Art. 32 nivel 1 Ley 2294/2023 (determinante de superior jerarquía)"
            ),
            accion_recomendada=(
                "El área protegida prevalece sobre el POT/EOT como determinante "
                "de superior jerarquía (art. 10 Ley 388 mod. Ley 2294/2023). "
                "Los usos incompatibles deben cesar. Coordinar con la CAR y "
                "PNN el régimen de usos permitidos."
            ),
            predios_afectados=c_prot,
        ))

    # --- Alerta: uso habitacional en zona de amenaza alta ---
    hab_alta = [
        p for p in predios_conflicto
        if ("habit" in (p.get("destinacion_economica") or "").lower()
            or "resid" in (p.get("destinacion_economica") or "").lower())
        and p.get("nivel_amenaza") == "ALTA"
    ]
    if hab_alta:
        alertas.append(Alerta(
            severidad="ALTA",
            titulo="Uso habitacional incompatible con amenaza alta",
            descripcion=(
                f"{len(hab_alta)} predio(s) de uso habitacional/residencial "
                f"ubicados dentro de zona de amenaza ALTA en {municipio}."
            ),
            norma_referencia=(
                "Art. 35 Ley 388/1997 · Decreto 1807/2014 art. 11 · "
                "Art. 32 nivel 1 Ley 2294/2023"
            ),
            accion_recomendada=(
                "El uso residencial es incompatible con amenaza alta no mitigable. "
                "Ordenar reubicación a través del programa de reasentamiento "
                "del municipio. Incluir en el componente de gestión del riesgo "
                "del EOT (Decreto 1807/2014 art. 22)."
            ),
            predios_afectados=len(hab_alta),
            uso_incompatible="Habitacional en amenaza alta",
        ))

    # --- Alerta: capa local ---
    if c_local > 0:
        alertas.append(Alerta(
            severidad="MEDIA",
            titulo="Superposición con capa local cargada",
            descripcion=(
                f"{c_local} predio(s) se superponen con la capa local. "
                "Verificar la naturaleza jurídica de la restricción."
            ),
            norma_referencia="Verificar norma aplicable según tipo de capa.",
            accion_recomendada=(
                "Revisar con el operador de la capa la naturaleza de la restricción "
                "y su efecto normativo sobre los predios cruzados."
            ),
            predios_afectados=c_local,
        ))

    if not alertas:
        alertas.append(Alerta(
            severidad="OK",
            titulo="Sin conflictos detectados",
            descripcion=(
                f"Las capas analizadas para {municipio} no presentan "
                "incompatibilidades normativas."
            ),
            norma_referencia="",
            accion_recomendada="",
            predios_afectados=0,
        ))

    return alertas
