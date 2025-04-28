import json
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, TypedDict

import obstore.store
import pystac.utils
from fastapi import FastAPI, HTTPException
from rustac import Collection, DuckdbClient

import stac_fastapi.api.models
from stac_fastapi.api.app import StacApi
from stac_fastapi.extensions.core.fields import FieldsExtension
from stac_fastapi.extensions.core.filter import SearchFilterExtension
from stac_fastapi.extensions.core.pagination import OffsetPaginationExtension
from stac_fastapi.extensions.core.sort import SortExtension
from stac_fastapi.types.search import BaseSearchPostRequest

from .client import Client
from .search import FixedSearchGetRequest
from .settings import Settings

GEOPARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"
EXTENSIONS = [
    OffsetPaginationExtension(),
    SearchFilterExtension(),
    FieldsExtension(),
    SortExtension(),
]

GetSearchRequestModel = stac_fastapi.api.models.create_get_request_model(
    base_model=FixedSearchGetRequest, extensions=EXTENSIONS
)
PostSearchRequestModel = stac_fastapi.api.models.create_post_request_model(
    base_model=BaseSearchPostRequest, extensions=EXTENSIONS
)


class State(TypedDict):
    """Application state."""

    client: DuckdbClient
    """The DuckDB client.
    
    It's just an in-memory DuckDB connection with the spatial extension enabled.
    """

    collections: dict[str, dict[str, Any]]
    """A mapping of collection id to collection."""

    hrefs: dict[str, str]
    """A mapping of collection id to geoparquet href."""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[State]:
    client = app.extra["duckdb_client"]
    settings: Settings = app.extra["settings"]
    collections = app.extra["collections"]
    collection_dict = dict()
    hrefs = dict()
    for collection in collections:
        if collection["id"] in collection_dict:
            raise HTTPException(
                500, f"two collections with the same id: {collection['id']}"
            )
        else:
            collection_dict[collection["id"]] = collection
        for key, asset in collection["assets"].items():
            if asset.get("type") == GEOPARQUET_MEDIA_TYPE:
                if collection["id"] in hrefs:
                    raise HTTPException(
                        500, f"two hrefs for one collection: {collection['id']}"
                    )
                else:
                    hrefs[collection["id"]] = pystac.utils.make_absolute_href(
                        asset["href"],
                        settings.stac_fastapi_collections_href,
                        start_is_dir=False,
                    )
    yield {
        "client": client,
        "collections": collection_dict,
        "hrefs": hrefs,
    }


def create(
    settings: Settings | None = None,
    duckdb_client: DuckdbClient | None = None,
) -> StacApi:
    if duckdb_client is None:
        duckdb_client = DuckdbClient()
    if settings is None:
        settings = Settings(
            stac_fastapi_landing_id="stac-fastapi-geoparquet",
            stac_fastapi_title="stac-fastapi-geoparquet",
            stac_fastapi_description="A stac-fastapi server backend by stac-geoparquet",
        )

    if settings.stac_fastapi_collections_href:
        if urllib.parse.urlparse(settings.stac_fastapi_collections_href).scheme:
            href = settings.stac_fastapi_collections_href
        else:
            href = "file://" + str(
                Path(settings.stac_fastapi_collections_href).absolute()
            )
        prefix, file_name = href.rsplit("/", 1)
        store = obstore.store.from_url(prefix)
        result = store.get(file_name)
        collections = json.loads(bytes(result.bytes()))
    else:
        collections = []

    if settings.stac_fastapi_geoparquet_href:
        collections.extend(
            collections_from_geoparquet_href(
                settings.stac_fastapi_geoparquet_href,
                duckdb_client,
            )
        )

    api = StacApi(
        settings=settings,
        client=Client(),
        app=FastAPI(
            lifespan=lifespan,
            openapi_url=settings.openapi_url,
            docs_url=settings.docs_url,
            redoc_url=settings.docs_url,
            settings=settings,
            collections=collections,
            duckdb_client=duckdb_client,
        ),
        search_get_request_model=GetSearchRequestModel,
        search_post_request_model=PostSearchRequestModel,
        extensions=EXTENSIONS,
    )
    return api


def collections_from_geoparquet_href(
    href: str, duckdb_client: DuckdbClient
) -> list[Collection]:
    collections = duckdb_client.get_collections(href)
    for collection in collections:
        collection["links"] = []
        collection["assets"] = {"data": {"href": href, "type": GEOPARQUET_MEDIA_TYPE}}
    return collections
