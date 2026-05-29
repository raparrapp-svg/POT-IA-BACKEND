-- ============================================================
-- POT-IA — Script de creación de base de datos
-- Ejecutar como superusuario de PostgreSQL
-- ============================================================

-- 1. Crear la base de datos
CREATE DATABASE pot_ia
    ENCODING = 'UTF8'
    LC_COLLATE = 'es_CO.UTF-8'
    LC_CTYPE = 'es_CO.UTF-8'
    TEMPLATE = template0;

-- 2. Conectarse a la BD y activar PostGIS
\connect pot_ia

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- búsqueda de texto
CREATE EXTENSION IF NOT EXISTS unaccent;   -- ignorar tildes en búsquedas

-- 3. Verificar instalación
SELECT PostGIS_Full_Version();

-- ============================================================
-- Las tablas las crea SQLAlchemy/Alembic automáticamente.
-- Si prefieres crearlas manualmente, ejecuta:
-- ============================================================

-- Caché de consultas ArcGIS REST
CREATE TABLE IF NOT EXISTS consulta_cache (
    id              SERIAL PRIMARY KEY,
    clave           VARCHAR(255) UNIQUE NOT NULL,
    municipio       VARCHAR(100) NOT NULL,
    departamento    VARCHAR(100) NOT NULL,
    capa            VARCHAR(100) NOT NULL,
    geojson         JSONB NOT NULL,
    total_features  INTEGER DEFAULT 0,
    creado_en       TIMESTAMP DEFAULT NOW(),
    fuente_real     BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_cache_municipio ON consulta_cache(municipio, departamento);
CREATE INDEX IF NOT EXISTS idx_cache_clave ON consulta_cache(clave);

-- Predios en zonas de amenaza (con geometría PostGIS)
CREATE TABLE IF NOT EXISTS predios_amenaza (
    id                      SERIAL PRIMARY KEY,
    municipio               VARCHAR(100) NOT NULL,
    departamento            VARCHAR(100),
    numero_predial          VARCHAR(100),
    destinacion_economica   VARCHAR(100),
    tipo                    VARCHAR(50),
    zona                    VARCHAR(50),
    area_terreno            FLOAT,
    geom                    GEOMETRY(GEOMETRY, 4326),
    nivel_amenaza           VARCHAR(20),
    tipo_amenaza            VARCHAR(100),
    capa_amenaza            VARCHAR(200),
    area_interseccion_m2    FLOAT,
    analizado_en            TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_predios_muni ON predios_amenaza(municipio);
CREATE INDEX IF NOT EXISTS idx_predios_geom ON predios_amenaza USING GIST(geom);

-- Alertas normativas
CREATE TABLE IF NOT EXISTS alertas_normativas (
    id                  SERIAL PRIMARY KEY,
    municipio           VARCHAR(100) NOT NULL,
    departamento        VARCHAR(100),
    severidad           VARCHAR(20),
    titulo              VARCHAR(300),
    descripcion         TEXT,
    norma_referencia    TEXT,
    accion_recomendada  TEXT,
    predios_afectados   INTEGER DEFAULT 0,
    generado_en         TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alertas_muni ON alertas_normativas(municipio);

-- Capas locales subidas por el usuario
CREATE TABLE IF NOT EXISTS capas_locales (
    id                      SERIAL PRIMARY KEY,
    nombre                  VARCHAR(255),
    municipio               VARCHAR(100),
    geojson                 JSONB,
    campos_seleccionados    JSONB,
    total_features          INTEGER DEFAULT 0,
    subido_en               TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Consultas útiles de diagnóstico
-- ============================================================

-- Ver todos los predios en amenaza alta de un municipio:
-- SELECT numero_predial, destinacion_economica, zona, area_terreno, nivel_amenaza
-- FROM predios_amenaza
-- WHERE municipio = 'SALENTO' AND nivel_amenaza = 'ALTA';

-- Contar predios por nivel de amenaza:
-- SELECT nivel_amenaza, COUNT(*) FROM predios_amenaza
-- WHERE municipio = 'SALENTO'
-- GROUP BY nivel_amenaza;

-- Ver área total de predios habitacionales en amenaza alta:
-- SELECT SUM(area_terreno) as area_total_m2
-- FROM predios_amenaza
-- WHERE municipio = 'SALENTO'
--   AND nivel_amenaza = 'ALTA'
--   AND UPPER(destinacion_economica) LIKE '%HABIT%';
