import asyncio
import json
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypedDict, cast

import obstore.store
import pystac.utils
from fastapi import FastAPI, HTTPException, Request, Response
from rustac import DuckdbClient
from stac_fastapi.api.app import StacApi

from .client import Client
from .models import EXTENSIONS, GetSearchRequestModel, PostSearchRequestModel
from .settings import Settings

GEOPARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"

# Global cache for collections and reload control
_collections_cache = None
_collections_cache_lock = asyncio.Lock()
_collections_cache_last_load = 0.0


async def load_collections(settings: Settings) -> list[dict[str, Any]]:
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
        collections = cast(list[dict[str, Any]], json.loads(bytes(result.bytes())))
    else:
        collections = []
    return collections


async def collections_cache_refresher(settings: Settings) -> None:
    global _collections_cache, _collections_cache_last_load
    while True:
        async with _collections_cache_lock:
            _collections_cache = await load_collections(settings)
            _collections_cache_last_load = asyncio.get_event_loop().time()
        await asyncio.sleep(settings.stac_fastapi_collections_reload_seconds)


async def get_cached_collections(settings: Settings) -> list[dict[str, Any]]:
    global _collections_cache, _collections_cache_last_load
    async with _collections_cache_lock:
        if _collections_cache is None:
            _collections_cache = await load_collections(settings)
            _collections_cache_last_load = asyncio.get_event_loop().time()
        return _collections_cache


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


# Middleware to inject latest collections/hrefs into request.state
def collections_hot_reload_middleware(
    settings: Settings,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        collections = await get_cached_collections(settings)
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
        request.state.collections = collection_dict
        request.state.hrefs = hrefs
        response = await call_next(request)
        return response

    return middleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[State]:
    client = app.extra["duckdb_client"]
    settings: Settings = app.extra["settings"]
    # Start background refresher
    app.state._collections_refresher = asyncio.create_task(
        collections_cache_refresher(settings)
    )
    yield {
        "client": client,
        "collections": {},
        "hrefs": {},
    }
    app.state._collections_refresher.cancel()


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

    # collections will be loaded and cached by the refresher
    collections = []
    if settings.stac_fastapi_geoparquet_href:
        collections.extend(
            collections_from_geoparquet_href(
                settings.stac_fastapi_geoparquet_href,
                duckdb_client,
            )
        )

    app = FastAPI(
        lifespan=lifespan,
        openapi_url=settings.openapi_url,
        docs_url=settings.docs_url,
        redoc_url=settings.docs_url,
        settings=settings,
        collections=collections,
        duckdb_client=duckdb_client,
    )
    # Add hot-reload middleware
    app.middleware("http")(collections_hot_reload_middleware(settings))

    api = StacApi(
        settings=settings,
        client=Client(),
        app=app,
        search_get_request_model=GetSearchRequestModel,
        search_post_request_model=PostSearchRequestModel,
        extensions=EXTENSIONS,
    )
    return api


def collections_from_geoparquet_href(
    href: str, duckdb_client: DuckdbClient
) -> list[dict[str, Any]]:
    collections = duckdb_client.get_collections(href)
    for collection in collections:
        collection["links"] = []
        collection["assets"] = {"data": {"href": href, "type": GEOPARQUET_MEDIA_TYPE}}
    return collections
