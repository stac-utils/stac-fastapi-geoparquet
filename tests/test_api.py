from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from rustac import DuckdbClient

import stac_fastapi.geoparquet.api
from stac_fastapi.geoparquet import Settings

from .conftest import COLLECTIONS_PATH, NAIP_PATH


@pytest.fixture
def extension_directory() -> Path:
    return Path(__file__).parent / "duckdb-extensions"


def test_create(extension_directory: Path) -> None:
    duckdb_client = DuckdbClient(extension_directory=str(extension_directory))
    settings = Settings(stac_fastapi_collections_href=str(COLLECTIONS_PATH))
    api = stac_fastapi.geoparquet.api.create(
        duckdb_client=duckdb_client, settings=settings
    )
    with TestClient(api.app) as client:
        response = client.get("/search")
        assert response.status_code == 200


def test_create_from_parquet_file() -> None:
    settings = Settings(stac_fastapi_geoparquet_href=str(NAIP_PATH))
    api = stac_fastapi.geoparquet.api.create(settings=settings)
    with TestClient(api.app) as client:
        response = client.get("/search")
        assert response.status_code == 200
        assert len(response.json()["features"]) > 0

        response = client.get("/collections")
        assert [c["id"] for c in response.json()["collections"]] == ["naip"]

        response = client.get("/collections/naip")
        assert response.status_code == 200

        # There is no collections.json to re-read, so no hot-reload middleware
        # is installed and nothing can empty the catalog behind us.
        api.app.state.collections_last_updated = datetime.now() - timedelta(seconds=120)
        client.get("/collections")
        response = client.get("/collections")
        assert [c["id"] for c in response.json()["collections"]] == ["naip"]


def test_collections_reload_on_ttl_expiry() -> None:
    settings = Settings(
        stac_fastapi_collections_href=str(COLLECTIONS_PATH),
        stac_fastapi_collections_reload_seconds=60,
    )
    api = stac_fastapi.geoparquet.api.create(settings=settings)

    with TestClient(api.app) as client:
        # Sanity check: initial collections are populated.
        response = client.get("/collections")
        assert len(response.json()["collections"]) > 0

        # Expire the TTL so the next request schedules a background refresh.
        api.app.state.collections_last_updated = datetime.now() - timedelta(seconds=120)

        # Patch load_collections to return an empty list for the reload.
        with patch(
            "stac_fastapi.geoparquet.api.load_collections",
            new_callable=AsyncMock,
            return_value=[],
        ):
            # TestClient awaits BackgroundTask before returning, so the refresh
            # has already updated app.state by the time this call returns.
            client.get("/collections")

        # The next request should see the reloaded (empty) state.
        response = client.get("/collections")
        assert response.json()["collections"] == []


def test_collections_no_reload_within_ttl() -> None:
    settings = Settings(
        stac_fastapi_collections_href=str(COLLECTIONS_PATH),
        stac_fastapi_collections_reload_seconds=3600,
    )
    api = stac_fastapi.geoparquet.api.create(settings=settings)

    with TestClient(api.app) as client:
        initial_count = len(client.get("/collections").json()["collections"])

        with patch(
            "stac_fastapi.geoparquet.api.load_collections",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_load:
            client.get("/collections")
            mock_load.assert_not_called()

        # Collections should be unchanged.
        assert len(client.get("/collections").json()["collections"]) == initial_count


def test_duckdb_client_injected() -> None:
    duckdb_client = DuckdbClient()
    settings = Settings(stac_fastapi_collections_href=str(COLLECTIONS_PATH))
    api = stac_fastapi.geoparquet.api.create(
        duckdb_client=duckdb_client, settings=settings
    )
    with TestClient(api.app) as client:
        response = client.get("/search")
        assert response.status_code == 200
        assert api.app.state.client is duckdb_client

        # Replace the client at runtime and verify the new one is used.
        new_client = DuckdbClient()
        api.app.state.client = new_client
        response = client.get("/search")
        assert response.status_code == 200
        assert api.app.state.client is new_client


def test_create_from_both_hrefs(tmp_path: Path) -> None:
    # Both sources configured at once: the hot-reload middleware is installed
    # for collections.json, and must not drop the parquet-generated ones.
    # The checked-in fixtures all appear in collections.json, so rename the
    # collection in a copy to avoid a duplicate id.
    standalone = tmp_path / "standalone.parquet"
    DuckdbClient().execute(
        f"COPY (SELECT * REPLACE ('standalone' AS collection) "
        f"FROM read_parquet('{NAIP_PATH}')) TO '{standalone}' (FORMAT PARQUET);"
    )
    settings = Settings(
        stac_fastapi_collections_href=str(COLLECTIONS_PATH),
        stac_fastapi_geoparquet_href=str(standalone),
    )
    api = stac_fastapi.geoparquet.api.create(settings=settings)
    with TestClient(api.app) as client:
        response = client.get("/collections")
        ids = [c["id"] for c in response.json()["collections"]]
        assert "standalone" in ids  # from the parquet file
        assert "openaerialmap" in ids  # from collections.json

        # Survives a reload, too.
        api.app.state.collections_last_updated = datetime.now() - timedelta(seconds=120)
        client.get("/collections")
        response = client.get("/collections")
        ids = [c["id"] for c in response.json()["collections"]]
        assert "standalone" in ids
        assert "openaerialmap" in ids
