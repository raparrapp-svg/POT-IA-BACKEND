"""
POT-IA Backend — API principal
FastAPI + GeoPandas + PostGIS

Arranca con:
    uvicorn main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

from config import get_settings
from db.database import init_db
from routers import analysis, chat, layers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pot_ia")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def lifespan(app: FastAPI):
    """Inicializa la BD al arrancar."""
    logger.info("Iniciando POT-IA Backend v%s", settings.app_version)
    try:
        await init_db()
        logger.info("Base de datos inicializada")
    except Exception as e:
        logger.warning("init_db omitido: %s", e)
    yield
    logger.info("Apagando POT-IA Backend")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "API para análisis territorial EOT Colombia. "
        "Proxy ArcGIS REST · Análisis GeoPandas · PostGIS · Chat IA"
    ),
    lifespan=lifespan,
)

# CORS: permite que el frontend (HTML local o React) consuma la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(analysis.router)
app.include_router(chat.router)
app.include_router(layers.router)


@app.get("/", tags=["Info"])
async def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "endpoints_principales": {
            "POST /api/analysis/run":          "Análisis completo (catastro + amenaza + cruces)",
            "GET  /api/analysis/ideam-layers": "Lista de capas IDEAM disponibles",
            "GET  /api/analysis/history/{m}":  "Historial de alertas por municipio",
            "POST /api/chat/ask":              "Asistente IA con contexto del análisis",
            "POST /api/layers/upload":         "Subir capa local GeoJSON",
            "GET  /api/layers/proxy/catastro": "Proxy catastro IGAC (sin CORS)",
            "GET  /api/layers/proxy/ideam/{k}":"Proxy capa IDEAM individual",
        },
    }


@app.get("/health", tags=["Info"])
async def health():
    return {"status": "ok", "version": settings.app_version}

app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/app")
async def frontend():
    return FileResponse("pot_ia_v4.html")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Error no manejado: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )
