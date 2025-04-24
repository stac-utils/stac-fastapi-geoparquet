import json
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TypedDict

import pystac.utils
from fastapi import FastAPI, HTTPException
from rustac import DuckdbClient

import stac_fastapi.api.models
from stac_fastapi.api.app import StacApi
from stac_fastapi.extensions.core.pagination import OffsetPaginationExtension

from .client import Client
from .search import SearchGetRequest, SearchPostRequest
from .settings import Settings

GEOPARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"

GetSearchRequestModel = stac_fastapi.api.models.create_request_model(
    model_name="SearchGetRequest",
    base_model=SearchGetRequest,
    mixins=[OffsetPaginationExtension().GET],
    request_type="GET",
)
PostSearchRequestModel = stac_fastapi.api.models.create_request_model(
    model_name="SearchPostRequest",
    base_model=SearchPostRequest,
    mixins=[OffsetPaginationExtension().POST],
    request_type="POST",
)


class State(TypedDict):
    """Application state."""

    client: DuckdbClient
    """The DuckDB client.
    
    It's just an in-memory DuckDB connection with the spatial extension enabled.
    """

    collections: dict[str, dict[str, Any]]
    """A mapping of collection id to collection."""

    hrefs: dict[str, list[str]]
    """A mapping of collection id to geoparquet href."""


def create(
    settings: Settings | None = None,
    duckdb_client: DuckdbClient | None = None,
) -> StacApi:
    if duckdb_client is None:
        duckdb_client = DuckdbClient()
    if settings is None:
        settings = Settings(
            stac_fastapi_landing_id="stac-fastapi-geoparquet",
            stac_fastapi_title="stac-geoparquet-geoparquet",
            stac_fastapi_description="A stac-fastapi server backend by stac-geoparquet",
        )

    with open(settings.stac_fastapi_collections_href, "rb") as f:
        collections = json.load(f)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[State]:
        client = app.extra["duckdb_client"]
        settings: Settings = app.extra["settings"]
        collections = app.extra["collections"]
        collection_dict = dict()
        hrefs = defaultdict(list)
        for collection in collections:
            if collection["id"] in collection_dict:
                raise HTTPException(
                    500, f"two collections with the same id: {collection.id}"
                )
            else:
                collection_dict[collection["id"]] = collection
            for key, asset in collection["assets"].items():
                if asset["type"] == GEOPARQUET_MEDIA_TYPE:
                    hrefs[collection["id"]].append(
                        pystac.utils.make_absolute_href(
                            asset["href"],
                            settings.stac_fastapi_collections_href,
                            start_is_dir=False,
                        )
                    )
        yield {
            "client": client,
            "collections": collection_dict,
            "hrefs": hrefs,
        }

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
    )
    return api
