import urllib.parse

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
        "collections": ["naip,openaerialmap"],
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
