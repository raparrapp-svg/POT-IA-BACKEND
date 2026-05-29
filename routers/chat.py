"""
Endpoint del asistente IA con Groq.
Dos modos:
  1. /ask       — pregunta conversacional con contexto del municipio
  2. /query     — Text-to-SQL: interpreta pregunta, ejecuta en PostGIS, devuelve datos + GeoJSON
"""
import json
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import httpx

from config import get_settings
from db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Chat IA"])
settings = get_settings()

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_POT = """Eres un experto en ordenamiento territorial colombiano, especializado en
Esquemas de Ordenamiento Territorial (EOT) para municipios pequeños (menos de 30.000 habitantes).
Respondes en español, de forma concisa y técnica, citando la norma exacta cuando aplica:
Ley 388/1997, Decreto 1077/2015, Decreto 1232/2020, Decreto 1807/2014, Decreto 3600/2007,
Ley 1523/2012, Ley 2294/2023 art. 32, Resolución IGAC 471/2020.
Cuando no sepas algo con certeza, lo dices claramente."""

# Esquema exacto de las tablas para que Groq genere SQL correcto
DB_SCHEMA = """
Tablas disponibles en PostgreSQL (esquema public):

1. predios_amenaza — predios que cruzan con zonas de amenaza
   Columnas: id, municipio, departamento, numero_predial,
             destinacion_economica, tipo, zona, area_terreno,
             nivel_amenaza, tipo_amenaza, area_interseccion_m2,
             geom (geometría PostGIS EPSG:4326)
   Valores comunes de destinacion_economica: Habitacional, Agropecuario,
     Religioso, Uso_Publico, Comercial, Institucional,
     Lote_Urbanizable_No_Urbanizado, Lote_Urbanizado_No_Construido
   Valores de nivel_amenaza: ALTA, MEDIA
   Valores de zona: Urbana, Rural

2. alertas_normativas — alertas generadas por municipio
   Columnas: id, municipio, departamento, severidad, titulo,
             descripcion, norma_referencia, accion_recomendada,
             predios_afectados, generado_en

IMPORTANTE:
- Usa siempre WHERE municipio = '{municipio}' para filtrar
- Para geometrías usa ST_AsGeoJSON(geom) AS geojson
- Limita resultados con LIMIT 500 máximo
- Solo SELECT, nunca INSERT/UPDATE/DELETE
- Para agrupar usa GROUP BY
"""

SYSTEM_SQL = """Eres un generador de SQL para PostgreSQL/PostGIS.
Tu única tarea es generar una consulta SQL válida basada en la pregunta del usuario.
Responde ÚNICAMENTE con el SQL, sin explicaciones, sin markdown, sin backticks.
El SQL debe ser una sola consulta SELECT válida."""

async def call_groq(messages: list, system: str, max_tokens: int = 800) -> str:
    key = getattr(settings, "groq_api_key", "")
    if not key:
        return "Error: GROQ_API_KEY no configurada."
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": [{"role":"system","content":system}] + messages,
                  "max_tokens": max_tokens, "temperature": 0.1})
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


# ── Modelos ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    mensaje: str
    contexto_municipio: dict | None = None
    historial: list[dict] | None = None

class ChatResponse(BaseModel):
    respuesta: str
    modelo: str = GROQ_MODEL
    tokens_usados: int = 0

class QueryRequest(BaseModel):
    pregunta: str
    municipio: str
    departamento: str | None = None

class QueryResponse(BaseModel):
    respuesta: str          # texto en lenguaje natural
    sql_ejecutado: str      # SQL que se generó
    total_registros: int
    geojson: dict | None    # GeoJSON si la consulta retornó geometrías
    tabla: list[dict]       # datos tabulares para mostrar


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/ask", response_model=ChatResponse)
async def ask(req: ChatRequest):
    """Pregunta conversacional con contexto del municipio."""
    ctx = ""
    if req.contexto_municipio:
        c = req.contexto_municipio
        ctx = (f"\nCONTEXTO DEL ANÁLISIS:\n"
               f"Municipio: {c.get('municipio','—')}, {c.get('departamento','—')} (EOT <30k hab.)\n"
               f"Total predios: {c.get('total_predios',0)}\n"
               f"Amenaza alta: {c.get('amenaza_alta',0)} predios\n"
               f"Amenaza media: {c.get('amenaza_media',0)} predios\n"
               f"Alertas: {c.get('alertas_resumen','')}\n")
    messages = []
    if req.historial:
        for h in (req.historial or [])[-8:]:
            if h.get("role") in ("user","assistant"):
                messages.append({"role":h["role"],"content":h["content"]})
    messages.append({"role":"user","content":req.mensaje})
    try:
        respuesta = await call_groq(messages, SYSTEM_POT + ctx)
        return ChatResponse(respuesta=respuesta)
    except Exception as e:
        return ChatResponse(respuesta=f"Error: {e}")


@router.post("/query", response_model=QueryResponse)
async def query_db(req: QueryRequest, db: AsyncSession = Depends(get_db)):
    """
    Text-to-SQL: convierte pregunta en SQL, ejecuta en PostGIS,
    devuelve datos tabulares + GeoJSON si hay geometrías.
    """
    mun = req.municipio.strip().upper()
    schema = DB_SCHEMA.replace("{municipio}", mun)

    # 1. Generar SQL con Groq
    sql_prompt = (f"{schema}\n\n"
                  f"Municipio activo: {mun}\n"
                  f"Pregunta del usuario: {req.pregunta}\n\n"
                  f"Genera el SQL para responder esta pregunta. "
		  f"IMPORTANTE: cuando uses ST_AsGeoJSON(geom), "
		  f"siempre transforma primero a WGS84 así: "
		  f"ST_AsGeoJSON(ST_Transform(ST_SetSRID(geom, 9377), 4326)) AS geojson
    try:
        sql_raw = await call_groq(
            [{"role":"user","content":sql_prompt}],
            SYSTEM_SQL, max_tokens=300)
    except Exception as e:
        return QueryResponse(respuesta=f"Error generando SQL: {e}",
                             sql_ejecutado="", total_registros=0,
                             geojson=None, tabla=[])

    # Limpiar SQL — quitar backticks y sql keyword si Groq los pone
    sql = sql_raw.strip().strip('`')
    if sql.lower().startswith('sql'):
        sql = sql[3:].strip()

    # Seguridad: solo SELECT
    if not sql.upper().strip().startswith("SELECT"):
        return QueryResponse(
            respuesta="Solo puedo ejecutar consultas SELECT.",
            sql_ejecutado=sql, total_registros=0, geojson=None, tabla=[])

    # Asegurar LIMIT
    if "LIMIT" not in sql.upper():
        sql = sql.rstrip(";") + " LIMIT 500"

    # 2. Ejecutar en PostGIS
    try:
        result = await db.execute(text(sql))
        rows = result.mappings().all()
    except Exception as e:
        # Si falla, devolver el error y el SQL para debug
        return QueryResponse(
            respuesta=f"Error ejecutando la consulta: {e}",
            sql_ejecutado=sql, total_registros=0, geojson=None, tabla=[])

    if not rows:
        return QueryResponse(
            respuesta=f"No se encontraron registros para: {req.pregunta}",
            sql_ejecutado=sql, total_registros=0, geojson=None, tabla=[])

    # 3. Separar geometrías de datos tabulares
    tabla = []
    features = []
    for row in rows:
        row_dict = dict(row)
        geojson_str = row_dict.pop("geojson", None)
        tabla.append(row_dict)
        if geojson_str:
            try:
                geom = json.loads(geojson_str)
                props = {k: v for k, v in row_dict.items()
                         if k not in ("geom",) and v is not None}
                features.append({"type":"Feature","geometry":geom,"properties":props})
            except Exception:
                pass

    geojson_out = {"type":"FeatureCollection","features":features} if features else None

    # 4. Generar respuesta en lenguaje natural
    resumen_datos = json.dumps(tabla[:10], ensure_ascii=False, default=str)
    nl_prompt = (f"El usuario preguntó: '{req.pregunta}'\n"
                 f"Municipio: {mun}\n"
                 f"La consulta retornó {len(rows)} registros.\n"
                 f"Primeros resultados: {resumen_datos}\n\n"
                 f"Responde en español de forma concisa explicando los resultados. "
                 f"Si es relevante menciona implicaciones normativas para el EOT.")
    try:
        respuesta_nl = await call_groq(
            [{"role":"user","content":nl_prompt}], SYSTEM_POT, max_tokens=400)
    except Exception:
        respuesta_nl = f"Se encontraron {len(rows)} registros."

    return QueryResponse(
        respuesta=respuesta_nl,
        sql_ejecutado=sql,
        total_registros=len(rows),
        geojson=geojson_out,
        tabla=[dict(r) for r in tabla[:100]])
