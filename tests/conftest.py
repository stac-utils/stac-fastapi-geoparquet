from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import stac_fastapi.geoparquet.api
from stac_fastapi.geoparquet import Settings

COLLECTIONS_PATH = Path(__file__).parents[1] / "data" / "collections.json"
NAIP_PATH = Path(__file__).parents[1] / "data" / "naip.parquet"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(stac_fastapi_collections_href=str(COLLECTIONS_PATH))
    api = stac_fastapi.geoparquet.api.create(settings)
    with TestClient(api.app) as client:
        yield client
