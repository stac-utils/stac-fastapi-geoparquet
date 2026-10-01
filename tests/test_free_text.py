"""Tests for the Free-Text Search extension on item search.

The extension specifies comma-separated terms, OR semantics between them,
and case-insensitive partial matching over an item's textual properties.
"""

from typing import Any

from fastapi.testclient import TestClient

# An id from data/naip.parquet, and the tile number inside it.
ITEM_ID = "ne_m_4110264_sw_13_060_20220827"
ITEM_ID_FRAGMENT = "4110264"


def _ids(client: TestClient, **params: Any) -> list[str]:
    response = client.get(
        "/search", params={"collections": "naip", "limit": 10_000, **params}
    )
    response.raise_for_status()
    return [feature["id"] for feature in response.json()["features"]]


def test_q_matches_a_text_property(client: TestClient) -> None:
    # `naip:state` is "ne" for a subset of the items.
    matched = _ids(client, q="ne")
    assert matched
    assert len(matched) < len(_ids(client))


def test_q_matches_the_id(client: TestClient) -> None:
    assert _ids(client, q=ITEM_ID) == [ITEM_ID]


def test_q_matches_partially(client: TestClient) -> None:
    matched = _ids(client, q=ITEM_ID_FRAGMENT)
    assert ITEM_ID in matched
    assert all(ITEM_ID_FRAGMENT in item_id for item_id in matched)


def test_q_is_case_insensitive(client: TestClient) -> None:
    assert _ids(client, q=ITEM_ID.upper()) == [ITEM_ID]


def test_q_without_a_match_is_empty(client: TestClient) -> None:
    assert _ids(client, q="zzznomatchzzz") == []


def test_q_terms_are_or_ed(client: TestClient) -> None:
    ne = set(_ids(client, q="ne"))
    co = set(_ids(client, q="co"))
    both = set(_ids(client, q="ne,co"))
    assert ne and co
    assert both == ne | co


def test_q_is_and_ed_with_a_filter(client: TestClient) -> None:
    # The caller's filter still applies; `q` narrows it further rather than
    # replacing it. This fails if the free-text disjunction is encoded as
    # `filter AND (a OR b)` - see `_apply_free_text` for why that form loses
    # its grouping before it reaches SQL.
    filtered = _ids(client, filter="naip:year='2022'")
    combined = _ids(client, q="ne", filter="naip:year='2022'")
    assert combined
    assert set(combined) < set(filtered)
    assert set(combined) < set(_ids(client, q="ne"))


def test_q_post(client: TestClient) -> None:
    response = client.post(
        "/search", json={"collections": ["naip"], "limit": 10_000, "q": [ITEM_ID]}
    )
    response.raise_for_status()
    assert [f["id"] for f in response.json()["features"]] == [ITEM_ID]


def test_q_post_with_a_cql2_json_filter(client: TestClient) -> None:
    response = client.post(
        "/search",
        json={
            "collections": ["naip"],
            "limit": 10_000,
            "q": ["ne"],
            "filter": {"op": "=", "args": [{"property": "naip:year"}, "2022"]},
            "filter-lang": "cql2-json",
        },
    )
    response.raise_for_status()
    features = response.json()["features"]
    assert features
    assert all(f["properties"]["naip:year"] == "2022" for f in features)


def test_q_is_advertised_as_conformant(client: TestClient) -> None:
    conforms_to = client.get("/").json()["conformsTo"]
    assert any("item-search#free-text" in uri for uri in conforms_to)


def test_q_survives_paging(client: TestClient) -> None:
    response = client.get(
        "/search", params={"collections": "naip", "limit": 1, "q": ITEM_ID_FRAGMENT}
    )
    response.raise_for_status()
    data = response.json()
    assert len(data["features"]) == 1
    next_link = next(
        (link for link in data["links"] if link["rel"] == "next"),
        None,
    )
    assert next_link is not None
    assert "q" in next_link["href"]

    response = client.get(next_link["href"])
    response.raise_for_status()
    for feature in response.json()["features"]:
        assert ITEM_ID_FRAGMENT in feature["id"]
