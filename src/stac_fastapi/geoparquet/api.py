import json
import logging
import os
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

import obstore.store
import pystac.utils
from fastapi import FastAPI, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
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


def make_collections_middleware(
    settings: Settings,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Return a TTL-based hot-reload middleware for collections.

    On every request the current ``app.state.collections`` / ``app.state.hrefs``
    are injected into ``request.state`` so that the rest of the stack is
    unaffected.  After the response is sent, a background task re-reads
    ``collections.json`` from object storage and updates ``app.state`` when the
    configured TTL has elapsed.
    """

    async def _refresh(app: FastAPI) -> None:
        try:
            raw = await load_collections(settings)
            collection_dict, hrefs = _parse_collections(raw, settings)
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
    # with an empty catalog.
    raw = await load_collections(settings)
    collection_dict, hrefs = _parse_collections(raw, settings)
    app.state.client = client
    app.state.collections = collection_dict
    app.state.hrefs = hrefs
    app.state.collections_last_updated = datetime.now()

    yield {"client": client}


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
        # Swagger UI is served from `/api.html` below, off the assets bundled
        # with the package rather than a CDN, so FastAPI's own doc routes are
        # disabled.
        docs_url=None,
        redoc_url=None,
        settings=settings,
        collections=collections,
        duckdb_client=duckdb_client,
    )
    # Add hot-reload middleware
    app.middleware("http")(make_collections_middleware(settings))

    # The Swagger UI assets ship inside the package; resolve them relative to
    # this module so it works in a source checkout, an installed wheel and a
    # container alike. STATIC_DIR overrides the location.
    static_dir = os.getenv("STATIC_DIR", str(Path(__file__).parent / "static"))
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get(str(settings.docs_url), include_in_schema=False, name="swagger_ui_html")
    async def swagger_ui_html(request: Request) -> HTMLResponse:
        # root_path comes from the request so the page works both directly and
        # behind a proxy that mounts the app under a prefix.
        root_path = request.scope.get("root_path", "").rstrip("/")
        oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url
        if oauth2_redirect_url:
            oauth2_redirect_url = root_path + oauth2_redirect_url
        return get_swagger_ui_html(
            openapi_url=root_path + str(app.openapi_url),
            title=app.title + " - OpenAPI UI",
            oauth2_redirect_url=oauth2_redirect_url,
            swagger_js_url=root_path + "/static/swagger-ui-bundle.js",
            swagger_css_url=root_path + "/static/swagger-ui.css",
        )

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
