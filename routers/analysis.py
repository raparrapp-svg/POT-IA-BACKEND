"""
Router principal: recibe parámetros del frontend,
consulta los servicios ArcGIS, hace los cruces y devuelve alertas.
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import AlertaNormativa
from services.arcgis_proxy import query_catastro, query_ideam, query_runap, IDEAM_LAYERS
from services.spatial import run_spatial_analysis, save_analysis_to_postgis
from rules.engine import evaluar_alertas

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["Análisis espacial"])


# ---- Modelos de entrada/salida ----

class FiltrosCatastro(BaseModel):
    numero_predial: str | None = None
    destinacion_economica: str | None = None
    tipo: str | None = None
    zona: str | None = None


class AnalysisRequest(BaseModel):
    municipio: str = Field(..., min_length=2, max_length=100)
    departamento: str = Field(..., min_length=2, max_length=100)
    bbox: list[float] = Field(..., min_length=4, max_length=4,
                               description="[xmin, ymin, xmax, ymax] en WGS84")
    filtros_catastro: FiltrosCatastro = Field(default_factory=FiltrosCatastro)
    capas_ideam: list[str] = Field(
        default=["base_100k", "tr_50"],
        description="Keys de IDEAM_LAYERS a consultar",
    )
    incluir_runap: bool = True
    local_geojson: dict | None = None   # GeoJSON de capa local subida por el usuario


class AlertaOut(BaseModel):
    severidad: str
    titulo: str
    descripcion: str
    norma_referencia: str
    accion_recomendada: str
    predios_afectados: int


class AnalysisResponse(BaseModel):
    municipio: str
    departamento: str
    total_predios: int
    total_amenaza_features: int
    conteos: dict[str, int]
    fuentes: dict[str, bool]          # {capa: fue_real}
    alertas: list[AlertaOut]
    conflictos_geojson: dict           # GeoJSON de predios en conflicto
    catastro_geojson: dict             # GeoJSON completo del catastro
    amenaza_geojson: dict              # GeoJSON de amenazas


# ---- Endpoint ----

@router.post("/run", response_model=AnalysisResponse)
async def run_analysis(req: AnalysisRequest, db: AsyncSession = Depends(get_db)):
    """
    Endpoint principal:
    1. Consulta catastro IGAC via proxy (sin CORS)
    2. Consulta capas IDEAM seleccionadas
    3. Consulta RUNAP (opcional)
    4. Ejecuta cruces espaciales con GeoPandas
    5. Evalúa reglas normativas
    6. Guarda resultados en PostGIS
    7. Devuelve GeoJSON + alertas al frontend
    """
    mun = req.municipio.strip().upper()
    dep = req.departamento.strip().upper()
    fuentes: dict[str, bool] = {}

    # 1. Catastro
    cat_gj, cat_real = await query_catastro(
        db, mun, dep, req.bbox,
        filtros=req.filtros_catastro.model_dump(exclude_none=True),
    )
    fuentes["catastro"] = cat_real

    # 2. IDEAM
    amen_gj, ideam_status = await query_ideam(db, mun, dep, req.bbox, req.capas_ideam)
    fuentes.update({f"ideam_{k}": v for k, v in ideam_status.items()})

    # 3. RUNAP
    runap_gj = None
    if req.incluir_runap:
        runap_gj, runap_real = await query_runap(db, mun, dep, req.bbox)
        fuentes["runap"] = runap_real

    # 4. Análisis espacial con GeoPandas
    try:
        result = run_spatial_analysis(
            catastro_gj=cat_gj,
            amenazas_gj=amen_gj,
            runap_gj=runap_gj,
            local_gj=req.local_geojson,
        )
    except Exception as e:
        logger.error("Error en análisis espacial: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error en análisis espacial: {e}")

    # 5. Reglas normativas
    alertas_obj = evaluar_alertas(
        conteos=result["conteos"],
        total_predios=result["total_predios"],
        municipio=mun,
        departamento=dep,
        predios_conflicto=result["predios_conflicto"],
    )

    # 6. Guardar en PostGIS
    try:
        await save_analysis_to_postgis(db, mun, dep, result)
    except Exception as e:
        logger.warning("No se pudo guardar en PostGIS: %s", e)

    # 7. Guardar alertas en BD
    for a in alertas_obj:
        db.add(AlertaNormativa(
            municipio=mun, departamento=dep,
            severidad=a.severidad, titulo=a.titulo,
            descripcion=a.descripcion,
            norma_referencia=a.norma_referencia,
            accion_recomendada=a.accion_recomendada,
            predios_afectados=a.predios_afectados,
        ))
    await db.commit()

    # GeoJSON de predios en conflicto
    conflict_feats = []
    for p in result["predios_conflicto"]:
        geom = p.get("geometry")
        if geom and hasattr(geom, "__geo_interface__"):
            conflict_feats.append({
                "type": "Feature",
                "properties": {k: v for k, v in p.items() if k != "geometry"},
                "geometry": geom.__geo_interface__,
            })
    conflict_gj = {"type": "FeatureCollection", "features": conflict_feats}

    return AnalysisResponse(
        municipio=mun,
        departamento=dep,
        total_predios=result["total_predios"],
        total_amenaza_features=result["total_amenaza"],
        conteos=result["conteos"],
        fuentes=fuentes,
        alertas=[AlertaOut(**a.__dict__) for a in alertas_obj],
        conflictos_geojson=conflict_gj,
        catastro_geojson=cat_gj,
        amenaza_geojson=amen_gj,
    )


@router.get("/ideam-layers")
async def get_ideam_layers():
    """Lista las capas IDEAM disponibles."""
    return [{"key": k, **v} for k, v in IDEAM_LAYERS.items()]


@router.get("/history/{municipio}")
async def get_history(municipio: str, db: AsyncSession = Depends(get_db)):
    """Retorna las últimas alertas guardadas para un municipio."""
    from sqlalchemy import select, desc
    result = await db.execute(
        select(AlertaNormativa)
        .where(AlertaNormativa.municipio == municipio.upper())
        .order_by(desc(AlertaNormativa.generado_en))
        .limit(50)
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "severidad": r.severidad,
            "titulo": r.titulo,
            "descripcion": r.descripcion,
            "predios_afectados": r.predios_afectados,
            "generado_en": r.generado_en.isoformat(),
        }
        for r in rows
    ]
