from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "POT-IA Backend"
    app_version: str = "1.0.0"
    debug: bool = True

    # Base de datos
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "pot_ia"
    db_user: str = "postgres"
    db_password: str = "postgres"

    # Claude
    anthropic_api_key: str = ""
    groq_api_key: str = ""

    # Servicios ArcGIS
    ideam_feature_server: str = (
        "https://visualizador.ideam.gov.co/gisserver/rest/services/"
        "Amenaza_Ambiental/FeatureServer"
    )
    igac_catastro_url: str = (
        "https://sigi.igac.gov.co/habilitacion/rest/services/sinic/ric/MapServer/0"
    )
    runap_url: str = (
        "https://mapas.parquesnacionales.gov.co/arcgis/rest/services/"
        "pnn/runap/MapServer/0"
    )
    sgc_amenaza_url: str = (
        "https://srvags.sgc.gov.co/arcgis/rest/services/"
        "Amenaza_Movimientos_en_Masa/amenaza_mm_nacional/MapServer/0"
    )

    # CORS
    allowed_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500"

    # Caché
    cache_ttl_seconds: int = 3600

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
