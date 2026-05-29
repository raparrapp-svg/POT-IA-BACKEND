from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column
from geoalchemy2 import Geometry
from db.database import Base


class ConsultaCache(Base):
    """
    Caché de resultados de servicios ArcGIS REST.
    Evita re-consultar el mismo municipio/capa repetidamente.
    """
    __tablename__ = "consulta_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    clave: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # departamento + municipio + capa + filtros → hash único
    municipio: Mapped[str] = mapped_column(String(100), index=True)
    departamento: Mapped[str] = mapped_column(String(100), index=True)
    capa: Mapped[str] = mapped_column(String(100))
    geojson: Mapped[dict] = mapped_column(JSON)
    total_features: Mapped[int] = mapped_column(Integer, default=0)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    fuente_real: Mapped[bool] = mapped_column(Boolean, default=False)
    # True = datos reales del servicio; False = datos demo


class PredioAmenaza(Base):
    """
    Tabla PostGIS: cruces entre predios y zonas de amenaza.
    Se llena al ejecutar el análisis espacial.
    """
    __tablename__ = "predios_amenaza"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    municipio: Mapped[str] = mapped_column(String(100), index=True)
    departamento: Mapped[str] = mapped_column(String(100))
    numero_predial: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destinacion_economica: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tipo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    zona: Mapped[str | None] = mapped_column(String(50), nullable=True)
    area_terreno: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Geometría del predio (WGS84)
    geom: Mapped[Geometry] = mapped_column(
    Geometry(geometry_type="GEOMETRY", srid=4326), nullable=True
    )

    # Resultado del cruce
    nivel_amenaza: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tipo_amenaza: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capa_amenaza: Mapped[str | None] = mapped_column(String(200), nullable=True)
    area_interseccion_m2: Mapped[float | None] = mapped_column(Float, nullable=True)

    analizado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlertaNormativa(Base):
    """
    Alertas normativas generadas para un municipio.
    """
    __tablename__ = "alertas_normativas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    municipio: Mapped[str] = mapped_column(String(100), index=True)
    departamento: Mapped[str] = mapped_column(String(100))
    severidad: Mapped[str] = mapped_column(String(20))  # ALTA / MEDIA / BAJA / OK
    titulo: Mapped[str] = mapped_column(String(300))
    descripcion: Mapped[str] = mapped_column(Text)
    norma_referencia: Mapped[str | None] = mapped_column(Text, nullable=True)
    accion_recomendada: Mapped[str | None] = mapped_column(Text, nullable=True)
    predios_afectados: Mapped[int] = mapped_column(Integer, default=0)
    generado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CapaLocal(Base):
    """
    Capas GeoJSON subidas por el usuario.
    """
    __tablename__ = "capas_locales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(255))
    municipio: Mapped[str | None] = mapped_column(String(100), nullable=True)
    geojson: Mapped[dict] = mapped_column(JSON)
    campos_seleccionados: Mapped[list | None] = mapped_column(JSON, nullable=True)
    total_features: Mapped[int] = mapped_column(Integer, default=0)
    subido_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
