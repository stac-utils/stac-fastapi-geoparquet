from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from rustac import DuckdbClient

import stac_fastapi.geoparquet.api
from stac_fastapi.geoparquet import Settings

from .conftest import COLLECTIONS_PATH


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
