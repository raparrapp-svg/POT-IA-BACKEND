"""
Proxy server-to-server para servicios ArcGIS REST.
El backend consulta los servicios directamente (sin restricciones CORS)
y devuelve GeoJSON al frontend.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from db.models import ConsultaCache

logger = logging.getLogger(__name__)
settings = get_settings()

# Mapeo de capas IDEAM disponibles
IDEAM_LAYERS = {
    "base_100k":  {"id": 10, "nombre": "Línea Base Inundación 100K 2001",      "nivel": "ALTA"},
    "tr_2":       {"id": 14, "nombre": "Amenaza Inundación TR 2 años CP 2K",   "nivel": "ALTA"},
    "tr_10":      {"id": 11, "nombre": "Amenaza Inundación TR 10 años CP 2K",  "nivel": "ALTA"},
    "tr_20":      {"id": 12, "nombre": "Amenaza Inundación TR 20 años CP 2K",  "nivel": "MEDIA"},
    "tr_50":      {"id": 13, "nombre": "Amenaza Inundación TR 50 años CP 2K",  "nivel": "MEDIA"},
    "tr_100":     {"id": 15, "nombre": "Amenaza Inundación TR 100 años CP 2K", "nivel": "ALTA"},
}


def _build_bbox_geometry(bbox: list[float]) -> dict:
    xmin, ymin, xmax, ymax = bbox
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
            "spatialReference": {"wkid": 4326}}


def _cache_key(capa: str, municipio: str, departamento: str, extra: str = "") -> str:
    raw = f"{capa}|{municipio.upper()}|{departamento.upper()}|{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def _get_cache(db: AsyncSession, clave: str) -> dict | None:
    """Busca en caché. Retorna None si expiró o no existe."""
    result = await db.execute(
        select(ConsultaCache).where(ConsultaCache.clave == clave)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        return None
    ttl = timedelta(seconds=settings.cache_ttl_seconds)
    if datetime.utcnow() - entry.creado_en > ttl:
        await db.delete(entry)
        await db.commit()
        return None
    return entry.geojson


async def _save_cache(
    db: AsyncSession,
    clave: str,
    municipio: str,
    departamento: str,
    capa: str,
    geojson: dict,
    fuente_real: bool,
) -> None:
    entry = ConsultaCache(
        clave=clave,
        municipio=municipio.upper(),
        departamento=departamento.upper(),
        capa=capa,
        geojson=geojson,
        total_features=len(geojson.get("features", [])),
        fuente_real=fuente_real,
    )
    db.add(entry)
    await db.commit()


async def _arcgis_query(
    url: str,
    bbox: list[float],
    where: str = "1=1",
    out_fields: str = "*",
    max_records: int = 1000,
) -> dict:
    """
    Consulta un endpoint ArcGIS REST /query.
    Retorna GeoJSON o lanza excepción.
    """
    params = {
        "f": "geojson",
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": max_records,
        "geometryType": "esriGeometryEnvelope",
        "geometry": json.dumps(_build_bbox_geometry(bbox)),
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(f"{url}/query", params=params)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise ValueError(f"ArcGIS error: {data['error']}")
        return data


# ---------------------------------------------------------------
# FUNCIONES PÚBLICAS
# ---------------------------------------------------------------

async def query_catastro(
    db: AsyncSession,
    municipio: str,
    departamento: str,
    bbox: list[float],
    filtros: dict | None = None,
) -> tuple[dict, bool]:
    """
    Consulta catastro IGAC (ric_terreno).
    Retorna (geojson, fuente_real).
    filtros: dict con claves opcionales:
        numero_predial, destinacion_economica, tipo, zona
    """
    filtros = filtros or {}

    # Construir WHERE clause
    parts = []
    if filtros.get("numero_predial"):
        v = filtros["numero_predial"].replace("'", "''")
        parts.append(f"numero_predial LIKE '%{v}%'")
    if filtros.get("destinacion_economica"):
        v = filtros["destinacion_economica"].replace("'", "''").upper()
        parts.append(f"UPPER(destinacion_economica) LIKE '%{v}%'")
    if filtros.get("tipo"):
        v = filtros["tipo"].replace("'", "''").upper()
        parts.append(f"UPPER(tipo) LIKE '%{v}%'")
    if filtros.get("zona"):
        v = filtros["zona"].replace("'", "''").upper()
        parts.append(f"UPPER(zona) LIKE '%{v}%'")

    where = " AND ".join(parts)
    extra = "|".join(f"{k}:{v}" for k, v in sorted(filtros.items()) if v)
    clave = _cache_key("catastro", municipio, departamento, where)

    cached = await _get_cache(db, clave)
    if cached:
        logger.info("Catastro desde caché: %s - %s", municipio, departamento)
        return cached, True  # caché = fuente válida

    out_fields = (
        "objectid,departamento,municipio,numero_predial,"
        "destinacion_economica,tipo,zona,vigencia_actualizacion,"
        "direccion,area_terreno,estadoregistro"
    )

    try:
        data = await _arcgis_query(
            settings.igac_catastro_url, bbox, where=where,
            out_fields=out_fields, max_records=2000
        )
        await _save_cache(db, clave, municipio, departamento, "catastro", data, True)
        logger.info("Catastro REAL: %d features", len(data.get("features", [])))
        return data, True
    except Exception as e:
        logger.warning("Catastro IGAC falló (%s), generando demo", e)
        demo = _gen_demo_catastro(bbox, municipio, departamento)
        return demo, False


async def query_ideam(
    db: AsyncSession,
    municipio: str,
    departamento: str,
    bbox: list[float],
    capas: list[str] | None = None,
) -> tuple[dict, dict[str, bool]]:
    """
    Consulta capas IDEAM de amenaza por inundación.
    capas: lista de keys de IDEAM_LAYERS a consultar.
           Si None → ['base_100k', 'tr_50']
    Retorna (geojson_combinado, {capa_key: fuente_real}).
    """
    if capas is None:
        capas = ["base_100k", "tr_50"]

    all_features: list[dict] = []
    status: dict[str, bool] = {}

    for key in capas:
        if key not in IDEAM_LAYERS:
            logger.warning("Capa IDEAM desconocida: %s", key)
            continue

        layer_info = IDEAM_LAYERS[key]
        clave = _cache_key(f"ideam_{key}", municipio, departamento)
        cached = await _get_cache(db, clave)

        if cached:
            feats = cached.get("features", [])
            for f in feats:
                f["properties"]["_nivel"] = layer_info["nivel"]
                f["properties"]["_capa"] = layer_info["nombre"]
                f["properties"]["_layer_key"] = key
            all_features.extend(feats)
            status[key] = True
            continue

        url = f"{settings.ideam_feature_server}/{layer_info['id']}"
        try:
            data = await _arcgis_query(url, bbox, max_records=500)
            feats = data.get("features", [])
            for f in feats:
                f["properties"]["_nivel"] = layer_info["nivel"]
                f["properties"]["_capa"] = layer_info["nombre"]
                f["properties"]["_layer_key"] = key
            all_features.extend(feats)
            await _save_cache(db, clave, municipio, departamento,
                              f"ideam_{key}", data, True)
            status[key] = True
            logger.info("IDEAM %s REAL: %d features", key, len(feats))
        except Exception as e:
            logger.warning("IDEAM %s falló (%s), generando demo", key, e)
            demo_feats = _gen_demo_amenaza(bbox, layer_info["nivel"],
                                           layer_info["nombre"], key)
            all_features.extend(demo_feats)
            status[key] = False

    combined = {"type": "FeatureCollection", "features": all_features}
    return combined, status


async def query_runap(
    db: AsyncSession,
    municipio: str,
    departamento: str,
    bbox: list[float],
) -> tuple[dict, bool]:
    """Consulta áreas protegidas SINAP/RUNAP."""
    clave = _cache_key("runap", municipio, departamento)
    cached = await _get_cache(db, clave)
    if cached:
        return cached, True

    try:
        data = await _arcgis_query(settings.runap_url, bbox, max_records=200)
        await _save_cache(db, clave, municipio, departamento, "runap", data, True)
        return data, True
    except Exception as e:
        logger.warning("RUNAP falló (%s)", e)
        return {"type": "FeatureCollection", "features": []}, False


# ---------------------------------------------------------------
# GENERADORES DE DATOS DEMO (fallback cuando los servicios fallan)
# ---------------------------------------------------------------

def _gen_demo_catastro(
    bbox: list[float], municipio: str, departamento: str
) -> dict:
    import math
    xmin, ymin, xmax, ymax = bbox
    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2
    s = min((xmax - xmin) / 14, 0.0014)
    cols, rows = 10, 8
    usos = ["Habitacional", "Habitacional", "Habitacional",
            "Comercial", "Dotacional", "Lote", "Agropecuario"]
    zonas = ["Urbana", "Urbana", "Rural"]
    features = []
    for r in range(rows):
        for c in range(cols):
            x = cx - (cols / 2) * s + c * s
            y = cy - (rows / 2) * s + r * s
            features.append({
                "type": "Feature",
                "properties": {
                    "objectid": r * cols + c + 1,
                    "numero_predial": f"{municipio[:3].upper()}0001{str(r*cols+c+1).zfill(5)}",
                    "destinacion_economica": usos[(r * 3 + c * 2) % len(usos)],
                    "tipo": "PH" if c % 5 == 0 else "NPH",
                    "zona": zonas[r % len(zonas)],
                    "area_terreno": round(200 + abs(math.sin(r + c)) * 300 + 100),
                    "municipio": municipio.upper(),
                    "departamento": departamento.upper(),
                    "_demo": True,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [x, y], [x + s * .88, y],
                        [x + s * .88, y + s * .88], [x, y + s * .88], [x, y]
                    ]],
                },
            })
    return {"type": "FeatureCollection", "features": features}


def _gen_demo_amenaza(
    bbox: list[float], nivel: str, nombre: str, key: str
) -> list[dict]:
    xmin, ymin, xmax, ymax = bbox
    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2
    dx = (xmax - xmin) * 0.18
    dy = (ymax - ymin) * 0.18
    geom = {
        "type": "Polygon",
        "coordinates": [[
            [cx - dx, cy + dy * .2],
            [cx + dx * .5, cy + dy * .3],
            [cx + dx * .8, cy + dy * 1.2],
            [cx + dx * .2, cy + dy * 1.2],
            [cx - dx * .5, cy + dy * .8],
            [cx - dx, cy + dy * .2],
        ]],
    }
    return [{
        "type": "Feature",
        "properties": {
            "_nivel": nivel,
            "_capa": nombre + " (DEMO)",
            "_layer_key": key,
            "_demo": True,
        },
        "geometry": geom,
    }]
