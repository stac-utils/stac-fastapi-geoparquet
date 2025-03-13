from pydantic import BaseModel

from stac_fastapi.types.config import ApiSettings


class Settings(ApiSettings):  # type: ignore
    """stac-fastapi-geoparquet settings"""

    stac_fastapi_geoparquet_href: str
    """This can either be the href of a single geoparquet file, or the href of a TOML
    configuration file.
    """

    stac_fastapi_duckdb_extension_directory: str | None = None
    """DuckDB extension directory (if none, the default will be used)"""


class StacFastapiGeoparquetSettings(BaseModel):
    hrefs: list[str]
    """Geoparquet hrefs"""
