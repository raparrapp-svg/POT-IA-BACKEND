"""
Endpoints para gestión de capas locales y consultas individuales a servicios.
"""
import json
import logging
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import CapaLocal
from services.arcgis_proxy import query_catastro, query_ideam, IDEAM_LAYERS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/layers", tags=["Capas"])


@router.post("/upload")
async def upload_local_layer(
    file: UploadFile = File(...),
    municipio: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Recibe un archivo GeoJSON subido por el usuario.
    Devuelve los campos disponibles para que el usuario seleccione cuáles usar.
    """
    if not file.filename.endswith((".geojson", ".json")):
        raise HTTPException(400, "Solo se aceptan archivos .geojson o .json")

    content = await file.read()
    try:
        gj = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON inválido: {e}")

    features = gj.get("features", [])
    if not features:
        raise HTTPException(400, "El GeoJSON no contiene features")

    # Extraer campos únicos
    campos = list({k for f in features for k in (f.get("properties") or {}).keys()})
    campos.sort()

    # Guardar en BD
    capa = CapaLocal(
        nombre=file.filename,
        municipio=municipio.upper() if municipio else None,
        geojson=gj,
        total_features=len(features),
    )
    db.add(capa)
    await db.commit()
    await db.refresh(capa)

    return {
        "id": capa.id,
        "nombre": file.filename,
        "total_features": len(features),
        "campos_disponibles": campos,
        "mensaje": "Capa cargada. Selecciona los campos para el reporte.",
    }


@router.put("/upload/{capa_id}/campos")
async def set_campos(
    capa_id: int,
    campos: list[str],
    db: AsyncSession = Depends(get_db),
):
    """Guarda los campos seleccionados por el usuario para una capa local."""
    from sqlalchemy import select
    result = await db.execute(select(CapaLocal).where(CapaLocal.id == capa_id))
    capa = result.scalar_one_or_none()
    if not capa:
        raise HTTPException(404, "Capa no encontrada")
    capa.campos_seleccionados = campos
    await db.commit()
    return {"mensaje": f"Campos actualizados: {campos}"}


@router.get("/upload/{capa_id}")
async def get_local_layer(capa_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna el GeoJSON y metadata de una capa local guardada."""
    from sqlalchemy import select
    result = await db.execute(select(CapaLocal).where(CapaLocal.id == capa_id))
    capa = result.scalar_one_or_none()
    if not capa:
        raise HTTPException(404, "Capa no encontrada")
    return {
        "id": capa.id,
        "nombre": capa.nombre,
        "campos_seleccionados": capa.campos_seleccionados or [],
        "geojson": capa.geojson,
        "total_features": capa.total_features,
    }


@router.get("/proxy/catastro")
async def proxy_catastro(
    municipio: str,
    departamento: str,
    bbox: str,   # "xmin,ymin,xmax,ymax"
    numero_predial: str | None = None,
    destinacion_economica: str | None = None,
    tipo: str | None = None,
    zona: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Proxy directo al servicio catastro IGAC.
    El frontend llama a este endpoint en lugar de llamar directamente al IGAC.
    """
    try:
        bbox_list = [float(x) for x in bbox.split(",")]
    except Exception:
        raise HTTPException(400, "bbox debe ser 'xmin,ymin,xmax,ymax'")

    filtros = {}
    if numero_predial:       filtros["numero_predial"] = numero_predial
    if destinacion_economica: filtros["destinacion_economica"] = destinacion_economica
    if tipo:                 filtros["tipo"] = tipo
    if zona:                 filtros["zona"] = zona

    data, real = await query_catastro(db, municipio, departamento, bbox_list, filtros)
    return {"geojson": data, "fuente_real": real, "total": len(data.get("features", []))}


@router.get("/proxy/ideam/{layer_key}")
async def proxy_ideam_layer(
    layer_key: str,
    municipio: str,
    departamento: str,
    bbox: str,
    db: AsyncSession = Depends(get_db),
):
    """Proxy para una capa IDEAM individual."""
    if layer_key not in IDEAM_LAYERS:
        raise HTTPException(404, f"Capa '{layer_key}' no disponible. "
                                  f"Claves válidas: {list(IDEAM_LAYERS.keys())}")
    try:
        bbox_list = [float(x) for x in bbox.split(",")]
    except Exception:
        raise HTTPException(400, "bbox debe ser 'xmin,ymin,xmax,ymax'")

    data, status = await query_ideam(db, municipio, departamento, bbox_list, [layer_key])
    return {
        "geojson": data,
        "fuente_real": status.get(layer_key, False),
        "capa": IDEAM_LAYERS[layer_key],
        "total": len(data.get("features", [])),
    }
