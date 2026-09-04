"""Tests for the Filter extension's queryables endpoints."""

from fastapi.testclient import TestClient


def test_global_queryables(client: TestClient) -> None:
    response = client.get("/queryables")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert data["$id"].endswith("/queryables")
    assert data["type"] == "object"
    for core in ("id", "collection", "datetime", "geometry"):
        assert core in data["properties"], core


def test_collection_queryables(client: TestClient) -> None:
    response = client.get("/collections/naip/queryables")
    assert response.status_code == 200, response.text
    data = response.json()
    properties = data["properties"]
    # Schema-derived, collection-specific property
    assert "naip:year" in properties
    assert properties["naip:year"]["type"] == "string"
    # Core STAC properties keep their curated schemas
    assert properties["datetime"]["format"] == "date-time"
    # Structural members must not be advertised as queryable
    assert "assets" not in properties
    assert "links" not in properties


def test_collection_queryables_unknown_collection(client: TestClient) -> None:
    response = client.get("/collections/not-a-collection/queryables")
    assert response.status_code == 404
