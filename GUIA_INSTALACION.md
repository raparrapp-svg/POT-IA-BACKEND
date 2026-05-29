# POT-IA Backend — Guía de instalación paso a paso

## Requisitos previos
- Python 3.11 o 3.12
- PostgreSQL 14+ con extensión PostGIS instalada
- Git (opcional)

---

## PASO 1 — Verificar que PostgreSQL y PostGIS están activos

```bash
# Verificar que PostgreSQL corre
pg_isready

# Conectarse y verificar PostGIS
psql -U postgres -c "SELECT PostGIS_Version();"
```

Si PostGIS no está instalado:
```bash
# Ubuntu/Debian
sudo apt-get install postgresql-14-postgis-3

# Windows: instalar desde Application Stack Builder
# (viene con el instalador oficial de PostgreSQL)
```

---

## PASO 2 — Crear la base de datos

```bash
# Conectarse a PostgreSQL como superusuario
psql -U postgres

# Dentro de psql, ejecutar el script:
\i C:/ruta/a/pot_ia_backend/setup_db.sql

# O directamente desde la terminal:
psql -U postgres -f setup_db.sql
```

Verificar que las tablas se crearon:
```sql
\connect pot_ia
\dt
-- Debe listar: consulta_cache, predios_amenaza, alertas_normativas, capas_locales
```

---

## PASO 3 — Crear entorno virtual Python e instalar dependencias

```bash
# Entrar a la carpeta del proyecto
cd pot_ia_backend

# Crear entorno virtual
python -m venv venv

# Activar el entorno
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

> ⚠️ En Windows, si falla la instalación de geopandas o fiona,
> instala primero las ruedas binarias:
> ```
> pip install wheel
> pip install geopandas --only-binary :all:
> ```
> O usa conda: `conda install geopandas`

---

## PASO 4 — Configurar variables de entorno

```bash
# Copiar el archivo de ejemplo
cp .env.example .env

# Editar .env con tus datos reales
notepad .env        # Windows
nano .env           # Linux/Mac
```

Valores que DEBES cambiar en `.env`:
```
DB_PASSWORD=tu_password_de_postgres
ANTHROPIC_API_KEY=sk-ant-tu-key-aqui
```

Obtén tu API Key de Claude en: https://console.anthropic.com

---

## PASO 5 — Arrancar el servidor

```bash
# Asegúrate de tener el entorno virtual activo
# Windows: venv\Scripts\activate

uvicorn main:app --reload --port 8000
```

Deberías ver:
```
INFO | pot_ia | Iniciando POT-IA Backend v1.0.0
INFO | pot_ia | Base de datos inicializada
INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

## PASO 6 — Verificar que funciona

Abre el navegador en:
- **http://localhost:8000** → info general de la API
- **http://localhost:8000/docs** → documentación interactiva Swagger
- **http://localhost:8000/health** → estado del servidor

---

## PASO 7 — Probar el análisis desde Swagger

1. Ve a http://localhost:8000/docs
2. Abre el endpoint `POST /api/analysis/run`
3. Haz clic en "Try it out"
4. Usa este ejemplo para Salento, Quindío:

```json
{
  "municipio": "SALENTO",
  "departamento": "QUINDIO",
  "bbox": [-75.62, 4.60, -75.52, 4.67],
  "filtros_catastro": {
    "zona": "Urbana"
  },
  "capas_ideam": ["base_100k", "tr_50"],
  "incluir_runap": true
}
```

5. Haz clic en "Execute"
6. El backend consultará los servicios IGAC e IDEAM, hará los cruces y retornará el resultado

---

## PASO 8 — Conectar el frontend HTML al backend

Edita el archivo `pot_ia_v2.html` y cambia la URL base de la API.

En la sección `<script>` del HTML, agrega al inicio:
```javascript
const API_BASE = 'http://localhost:8000';
```

Y modifica la función `runQuery()` para que llame al backend:
```javascript
const resp = await fetch(`${API_BASE}/api/analysis/run`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    municipio: STATE.muni,
    departamento: STATE.dep,
    bbox: STATE.bbox,
    filtros_catastro: getFiltros(),
    capas_ideam: getCapasSeleccionadas(),
    incluir_runap: true
  })
});
const data = await resp.json();
// data.catastro_geojson, data.amenaza_geojson, data.alertas, etc.
```

---

## Estructura del proyecto

```
pot_ia_backend/
├── main.py                  ← App FastAPI principal
├── config.py                ← Variables de entorno
├── requirements.txt
├── setup_db.sql             ← Script SQL para crear BD
├── .env.example             ← Plantilla de configuración
├── db/
│   ├── database.py          ← Conexión async PostgreSQL
│   └── models.py            ← Modelos SQLAlchemy + PostGIS
├── services/
│   ├── arcgis_proxy.py      ← Proxy servicios ArcGIS (sin CORS)
│   └── spatial.py           ← Análisis GeoPandas + guardado PostGIS
├── rules/
│   └── engine.py            ← Motor de reglas normativas
└── routers/
    ├── analysis.py          ← POST /api/analysis/run
    ├── chat.py              ← POST /api/chat/ask
    └── layers.py            ← Capas locales + proxies individuales
```

---

## Solución de problemas comunes

### Error: "could not connect to server"
→ PostgreSQL no está corriendo.
```bash
# Windows
net start postgresql-x64-14
# Linux
sudo systemctl start postgresql
```

### Error: "extension postgis does not exist"
→ PostGIS no está instalado o no está en esa BD.
```sql
-- Conectado a pot_ia como superusuario:
CREATE EXTENSION postgis;
```

### Error al instalar geopandas en Windows
→ Usa conda o instala Anaconda:
```bash
conda create -n pot_ia python=3.11
conda activate pot_ia
conda install geopandas fastapi uvicorn
pip install -r requirements.txt
```

### Los servicios IDEAM/IGAC retornan "Demo"
→ Es normal la primera vez. El servidor intenta consultar el servicio real;
   si falla por red o autenticación, usa datos demo.
   Verifica conectividad: `curl "https://sigi.igac.gov.co/habilitacion/rest/services/sinic/ric/MapServer?f=json"`

### Quiero ver los datos en PostgreSQL directamente
```sql
\connect pot_ia

-- Predios en conflicto analizados:
SELECT municipio, nivel_amenaza, COUNT(*), SUM(area_terreno)
FROM predios_amenaza
GROUP BY municipio, nivel_amenaza;

-- Ver con geometría en WKT:
SELECT numero_predial, nivel_amenaza, ST_AsText(geom)
FROM predios_amenaza
LIMIT 5;
```
