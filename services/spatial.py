"""
Motor de análisis espacial.
Usa GeoPandas para los cruces y guarda resultados en PostGIS.
"""
import logging
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, delete

from db.models import PredioAmenaza

logger = logging.getLogger(__name__)


def _geojson_to_gdf(geojson: dict, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """Convierte GeoJSON dict a GeoDataFrame. Retorna GDF vacío si no hay features."""
    features = geojson.get("features", [])
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs=crs)
    rows = []
    for f in features:
        if not f.get("geometry"):
            continue
        props = dict(f.get("properties") or {})
        try:
            geom = shape(f["geometry"])
            props["geometry"] = geom
            rows.append(props)
        except Exception as e:
            logger.debug("Geometría inválida, ignorada: %s", e)
    if not rows:
        return gpd.GeoDataFrame(geometry=[], crs=crs)
    return gpd.GeoDataFrame(rows, crs=crs)


def run_spatial_analysis(
    catastro_gj: dict,
    amenazas_gj: dict,
    runap_gj: dict | None = None,
    local_gj: dict | None = None,
) -> dict:
    """
    Ejecuta los cruces espaciales entre capas.
    Retorna dict con:
        - predios_conflicto: list de dicts (predios afectados)
        - conteos: dict con totales por tipo de conflicto
        - gdf_conflicto: GeoDataFrame (para guardar en PostGIS)
    """
    cat_gdf = _geojson_to_gdf(catastro_gj)
    amen_gdf = _geojson_to_gdf(amenazas_gj)
    prot_gdf = _geojson_to_gdf(runap_gj) if runap_gj else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    local_gdf = _geojson_to_gdf(local_gj) if local_gj else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    if cat_gdf.empty:
        return {"predios_conflicto": [], "conteos": {}, "total_predios": 0, "total_amenaza": 0, "gdf_conflicto": None}

    # Reproyectar a Origen Nacional Colombia (EPSG:9377) para cálculos de área
    try:
        cat_m = cat_gdf.to_crs("EPSG:9377")
        amen_m = amen_gdf.to_crs("EPSG:9377") if not amen_gdf.empty else amen_gdf
        prot_m = prot_gdf.to_crs("EPSG:9377") if not prot_gdf.empty else prot_gdf
        local_m = local_gdf.to_crs("EPSG:9377") if not local_gdf.empty else local_gdf
    except Exception as e:
        logger.warning("No se pudo reproyectar, usando WGS84: %s", e)
        cat_m, amen_m, prot_m, local_m = cat_gdf, amen_gdf, prot_gdf, local_gdf

    conteos = {"amenaza_alta": 0, "amenaza_media": 0, "area_protegida": 0, "capa_local": 0}
    conflict_rows: list[dict] = []

    # --- Cruce predios × amenaza ---
    if not amen_m.empty:
        alta = amen_m[amen_m.get("_nivel", pd.Series(dtype=str)) == "ALTA"] if "_nivel" in amen_m.columns else amen_m
        media = amen_m[amen_m.get("_nivel", pd.Series(dtype=str)) == "MEDIA"] if "_nivel" in amen_m.columns else gpd.GeoDataFrame(geometry=[], crs=amen_m.crs)

        try:
            join_alta = gpd.sjoin(cat_m, alta[["geometry","_nivel","_capa","_layer_key"]],
                                  how="inner", predicate="intersects")
            conteos["amenaza_alta"] = len(join_alta)

            for _, row in join_alta.iterrows():
                try:
                    area_m2 = row.geometry.intersection(
                        alta.loc[row.index_right, "geometry"]
                    ).area if "index_right" in row.index else 0
                except Exception:
                    area_m2 = 0
                conflict_rows.append({**_predio_dict(row),
                                       "nivel_amenaza": "ALTA",
                                       "tipo_amenaza": row.get("_capa", "Inundación"),
                                       "area_interseccion_m2": round(area_m2, 2)})
        except Exception as e:
            logger.warning("Cruce amenaza alta falló: %s", e)

        try:
            if not media.empty:
                join_media = gpd.sjoin(cat_m, media[["geometry","_nivel","_capa","_layer_key"]],
                                       how="inner", predicate="intersects")
                conteos["amenaza_media"] = len(join_media)
                for _, row in join_media.iterrows():
                    conflict_rows.append({**_predio_dict(row),
                                           "nivel_amenaza": "MEDIA",
                                           "tipo_amenaza": row.get("_capa", "Inundación"),
                                           "area_interseccion_m2": 0})
        except Exception as e:
            logger.warning("Cruce amenaza media falló: %s", e)

    # --- Cruce predios × áreas protegidas ---
    if not prot_m.empty:
        try:
            join_prot = gpd.sjoin(cat_m, prot_m[["geometry"]], how="inner", predicate="intersects")
            conteos["area_protegida"] = len(join_prot)
            for _, row in join_prot.iterrows():
                conflict_rows.append({**_predio_dict(row),
                                       "nivel_amenaza": "ALTA",
                                       "tipo_amenaza": "Área protegida SINAP",
                                       "area_interseccion_m2": 0})
        except Exception as e:
            logger.warning("Cruce RUNAP falló: %s", e)

    # --- Cruce predios × capa local ---
    if not local_m.empty:
        try:
            join_local = gpd.sjoin(cat_m, local_m[["geometry"]], how="inner", predicate="intersects")
            conteos["capa_local"] = len(join_local)
        except Exception as e:
            logger.warning("Cruce capa local falló: %s", e)

    # GeoDataFrame de resultados para guardar en PostGIS
    gdf_result = None
    if conflict_rows:
        try:
            gdf_result = gpd.GeoDataFrame(conflict_rows, crs="EPSG:9377").to_crs("EPSG:4326")
        except Exception as e:
            logger.warning("No se pudo crear GDF resultado: %s", e)

    return {
        "predios_conflicto": conflict_rows,
        "conteos": conteos,
        "total_predios": len(cat_gdf),
        "total_amenaza": len(amen_gdf) if not amen_gdf.empty else 0,
        "gdf_conflicto": gdf_result,
    }


def _predio_dict(row) -> dict:
    """Extrae campos relevantes de una fila del GeoDataFrame."""
    return {
        "numero_predial": str(row.get("numero_predial", "")),
        "destinacion_economica": str(row.get("destinacion_economica", "")),
        "tipo": str(row.get("tipo", "")),
        "zona": str(row.get("zona", "")),
        "area_terreno": float(row.get("area_terreno") or 0),
        "municipio": str(row.get("municipio", "")),
        "departamento": str(row.get("departamento", "")),
        "geometry": row.geometry,
    }


async def save_analysis_to_postgis(
    db: AsyncSession,
    municipio: str,
    departamento: str,
    analysis_result: dict,
) -> int:
    """
    Guarda el resultado del análisis en PostGIS.
    Borra los registros anteriores del mismo municipio antes.
    Retorna cantidad de registros guardados.
    """
    # Borrar análisis previo
    await db.execute(
        delete(PredioAmenaza).where(
            PredioAmenaza.municipio == municipio.upper(),
            PredioAmenaza.departamento == departamento.upper(),
        )
    )

    rows = analysis_result.get("predios_conflicto", [])
    if not rows:
        await db.commit()
        return 0

    saved = 0
    for row in rows:
        geom = row.get("geometry")
        wkt = geom.wkt if geom and hasattr(geom, "wkt") else None
        obj = PredioAmenaza(
            municipio=municipio.upper(),
            departamento=departamento.upper(),
            numero_predial=row.get("numero_predial"),
            destinacion_economica=row.get("destinacion_economica"),
            tipo=row.get("tipo"),
            zona=row.get("zona"),
            area_terreno=row.get("area_terreno"),
            geom=f"SRID=4326;{wkt}" if wkt else None,
            nivel_amenaza=row.get("nivel_amenaza"),
            tipo_amenaza=row.get("tipo_amenaza"),
            capa_amenaza=row.get("tipo_amenaza"),
            area_interseccion_m2=row.get("area_interseccion_m2"),
        )
        db.add(obj)
        saved += 1

    await db.commit()
    logger.info("Guardados %d predios en conflicto en PostGIS", saved)
    return saved
