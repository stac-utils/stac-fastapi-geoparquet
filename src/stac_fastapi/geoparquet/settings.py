from stac_fastapi.types.config import ApiSettings


class Settings(ApiSettings):  # type: ignore[misc]
    """stac-fastapi-geoparquet settings"""

    stac_fastapi_collections_href: str | None = None
    """The href of a file containing JSON list of collections.

    Any parquet assets on the collection will be loaded into the server."""

    stac_fastapi_geoparquet_href: str | None = None
    """The href of a stac-geoparquet file.

    The items in the file will be used to auto-generate one or more collections."""
