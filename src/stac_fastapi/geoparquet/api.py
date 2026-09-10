import json
import logging
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

import obstore.store
import pystac.utils
from fastapi import FastAPI, Request, Response
from rustac import DuckdbClient
from stac_fastapi.api.app import StacApi
from starlette.background import BackgroundTask

from .client import Client
from .models import (
    EXTENSIONS,
    GetSearchRequestModel,
    ItemsGetRequestModel,
    PostSearchRequestModel,
)
from .settings import Settings

logger = logging.getLogger(__name__)

GEOPARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"


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


def _parse_collections(
    collections: list[dict[str, Any]], settings: Settings
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Parse a raw collections list into (collection_dict, hrefs)."""
    collection_dict: dict[str, dict[str, Any]] = {}
    hrefs: dict[str, str] = {}
    for collection in collections:
        collection_id = collection["id"]
        if collection_id in collection_dict:
            raise ValueError(f"two collections with the same id: {collection_id}")
        collection_dict[collection_id] = collection
        for asset in collection["assets"].values():
            if asset.get("type") == GEOPARQUET_MEDIA_TYPE:
                if collection_id in hrefs:
                    raise ValueError(f"two hrefs for one collection: {collection_id}")
                hrefs[collection_id] = pystac.utils.make_absolute_href(
                    asset["href"],
                    settings.stac_fastapi_collections_href,
                    start_is_dir=False,
                )
    return collection_dict, hrefs


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


def make_collections_middleware(
    settings: Settings,
    static_collections: list[dict[str, Any]],
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Return a TTL-based hot-reload middleware for collections.

    On every request the current ``app.state.collections`` / ``app.state.hrefs``
    are injected into ``request.state`` so that the rest of the stack is
    unaffected.  After the response is sent, a background task re-reads
    ``collections.json`` from object storage and updates ``app.state`` when the
    configured TTL has elapsed.

    ``static_collections`` are the ones generated from
    ``stac_fastapi_geoparquet_href``, which have no file to be re-read from;
    they're carried across each reload so a refresh doesn't drop them.
    """

    async def _refresh(app: FastAPI) -> None:
        try:
            raw = await load_collections(settings)
            collection_dict, hrefs = _parse_collections(
                static_collections + raw, settings
            )
        except Exception:
            logger.exception("Failed to reload collections; keeping stale state")
            return
        app.state.collections = collection_dict
        app.state.hrefs = hrefs
        app.state.collections_last_updated = datetime.now()
        logger.debug(
            "Collections reloaded; %d collection(s) active", len(collection_dict)
        )

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.client = request.app.state.client
        request.state.collections = request.app.state.collections
        request.state.hrefs = request.app.state.hrefs

        background: BackgroundTask | None = None
        last_updated: datetime | None = getattr(
            request.app.state, "collections_last_updated", None
        )
        ttl = settings.stac_fastapi_collections_reload_seconds
        if last_updated is None or datetime.now() > last_updated + timedelta(
            seconds=ttl
        ):
            request.app.state.collections_last_updated = datetime.now()
            background = BackgroundTask(_refresh, request.app)

        response = await call_next(request)
        if background is not None:
            response.background = background
        return response

    return middleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[State]:
    client: DuckdbClient = app.extra["duckdb_client"]
    settings: Settings = app.extra["settings"]

    # Perform an initial blocking load so the first request is never served
    # with an empty catalog. Collections auto-generated from
    # `stac_fastapi_geoparquet_href` are built once in `create()` and arrive
    # via `app.extra`; they aren't re-read, since nothing about them changes
    # unless the file itself is replaced.
    raw = await load_collections(settings)
    collection_dict, hrefs = _parse_collections(
        app.extra["collections"] + raw, settings
    )
    app.state.client = client
    app.state.collections = collection_dict
    app.state.hrefs = hrefs
    app.state.collections_last_updated = datetime.now()

    # Also part of the request state, so requests are served correctly when
    # the hot-reload middleware isn't installed (see `create`).
    yield {"client": client, "collections": collection_dict, "hrefs": hrefs}


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

    # Collections from stac_fastapi_collections_href are loaded in the lifespan
    # and kept fresh by the hot-reload middleware.
    # Collections from stac_fastapi_geoparquet_href are static (loaded once here).
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
    # The middleware exists to re-read `collections.json`, so it's only worth
    # installing when there is one. Without it the request state comes
    # straight from the lifespan, which is all a catalog generated from
    # `stac_fastapi_geoparquet_href` needs.
    if settings.stac_fastapi_collections_href:
        app.middleware("http")(make_collections_middleware(settings, collections))

    api = StacApi(
        settings=settings,
        client=Client(),
        app=app,
        search_get_request_model=GetSearchRequestModel,
        search_post_request_model=PostSearchRequestModel,
        items_get_request_model=ItemsGetRequestModel,
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
