from stac_fastapi.types.config import ApiSettings


class Settings(ApiSettings):  # type: ignore
    """stac-fastapi-geoparquet settings"""

    stac_fastapi_collections_href: str
    """A file containing JSON list of collections.

    Any parquet assets on the collection will be loaded into the server."""
