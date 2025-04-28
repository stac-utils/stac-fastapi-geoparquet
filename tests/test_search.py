import urllib.parse
from typing import Any

from fastapi.testclient import TestClient


def test_get_search(client: TestClient) -> None:
    response = client.get("/search")
    assert response.status_code == 200, response.text


def test_post_search(client: TestClient) -> None:
    response = client.post("/search", json={})
    assert response.status_code == 200, response.text


def test_paging(client: TestClient) -> None:
    params = {"limit": 1}
    response = client.get("/search", params=params)
    assert response.status_code == 200
    assert response.json()["features"][0]["id"] == "ne_m_4110264_sw_13_060_20220827"
    next_link = next(
        (link for link in response.json()["links"] if link["rel"] == "next")
    )
    url = urllib.parse.urlparse(next_link["href"])
    assert urllib.parse.parse_qs(url.query) == {
        "limit": ["1"],
        "offset": ["1"],
        "collections": ["naip,naip-10,openaerialmap-10,openaerialmap"],
    }
    response = client.get("/search", params=url.query)
    assert response.status_code == 200
    assert response.json()["features"][0]["id"] == "ne_m_4110263_sw_13_060_20220820"


def test_collection_link(client: TestClient) -> None:
    response = client.get("/search", params={"limit": 1})
    response.raise_for_status()
    data = response.json()
    link = next(
        (link for link in data["features"][0]["links"] if link["rel"] == "collection")
    )
    assert link["href"].startswith(str(client.base_url)), (
        link["href"] + " does not start with the test client base url"
    )


def test_string_datetime(client: TestClient) -> None:
    response = client.get("/search", params={"datetime": "2025-04-27T00:00:00Z"})
    response.raise_for_status()

    response = client.get("/search", params={"datetime": "2025-04-27T00:00:00Z/"})
    response.raise_for_status()

    response = client.get("/search", params={"datetime": "/2025-04-27T00:00:00Z"})
    response.raise_for_status()


def test_400_bbox(client: TestClient) -> None:
    response = client.get("/search", params={"bbox": "[100.0, 0.0, 105.0, 1.0]"})
    assert response.status_code == 400

    response = client.get("/search", params={"bbox": "100.0"})
    assert response.status_code == 400


def test_multiple_collections(client: TestClient) -> None:
    # https://github.com/stac-utils/stac-fastapi-geoparquet/issues/18
    response = client.get(
        "/search", params={"collections": "naip-10,openaerialmap-10", "limit": "3"}
    )
    response.raise_for_status()
    data = response.json()
    items: list[dict[str, Any]] = data["features"]
    next_link = next(link for link in data["links"] if link["rel"] == "next")
    while next_link:
        response = client.get(next_link["href"])
        response.raise_for_status()
        data = response.json()
        items.extend(data["features"])
        next_link = next(
            (link for link in data["links"] if link["rel"] == "next"), None
        )
    assert len(items) == 20


def test_filter(client: TestClient) -> None:
    params = {"limit": 1, "filter": "naip:year='notayear'"}
    response = client.get("/search", params=params)
    response.raise_for_status()
    assert not response.json()["features"]


def test_filter_post(client: TestClient) -> None:
    params = {
        "limit": 1,
        "filter": {"op": "=", "args": [{"property": "naip:year"}, "2022"]},
    }
    response = client.post("/search", json=params)
    response.raise_for_status()
    assert len(response.json()["features"]) == 1

    params = {
        "limit": 1,
        "filter": {"op": "=", "args": [{"property": "naip:year"}, "notayear"]},
    }
    response = client.post("/search", json=params)
    response.raise_for_status()
    assert len(response.json()["features"]) == 0


def test_paging_filter(client: TestClient) -> None:
    params = {"limit": 1, "filter": "naip:year='2022'"}
    response = client.get("/search", params=params)
    assert response.status_code == 200
    assert response.json()["features"][0]["id"] == "ne_m_4110264_sw_13_060_20220827"
    next_link = next(
        (link for link in response.json()["links"] if link["rel"] == "next")
    )
    url = urllib.parse.urlparse(next_link["href"])
    assert urllib.parse.parse_qs(url.query) == {
        "limit": ["1"],
        "offset": ["1"],
        "collections": ["naip,naip-10,openaerialmap-10,openaerialmap"],
        "filter": ["naip:year='2022'"],
        "filter-lang": ["cql2-text"],
    }
    response = client.get("/search", params=url.query)
    assert response.status_code == 200
    assert response.json()["features"][0]["id"] == "ne_m_4110263_sw_13_060_20220820"


def test_fields_get(client: TestClient) -> None:
    response = client.get(
        "/search", params={"collections": "naip", "limit": "1", "fields": "id,geometry"}
    )
    response.raise_for_status()
    data = response.json()
    assert "properties" not in data["features"][0]


def test_fields_post(client: TestClient) -> None:
    response = client.post(
        "/search",
        json={
            "collections": ["naip"],
            "limit": "1",
            "fields": {"include": ["id", "geometry"]},
        },
    )
    response.raise_for_status()
    data = response.json()
    assert "properties" not in data["features"][0]


def test_sort_get(client: TestClient) -> None:
    response = client.get("/search", params={"limit": "1", "sortby": "datetime"})
    response.raise_for_status()


def test_sort_post(client: TestClient) -> None:
    response = client.post(
        "/search",
        json={"limit": "1", "sortby": [{"field": "datetime", "direction": "asc"}]},
    )
    print(response.json())
    response.raise_for_status()
