"""Tests for GET /collections collection search parameters."""

import urllib.parse
from typing import Any

from fastapi.testclient import TestClient

# IDs present in data/collections.json
ALL_IDS = {"naip", "naip-10", "openaerialmap-10", "openaerialmap"}

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_collections(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/collections", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def test_all_collections_returned_by_default(client: TestClient) -> None:
    data = _get_collections(client)
    ids = {c["id"] for c in data["collections"]}
    assert ids == ALL_IDS


# ---------------------------------------------------------------------------
# ids filter
# ---------------------------------------------------------------------------


def test_filter_by_exact_id(client: TestClient) -> None:
    # Exact match — only "naip" (not "naip-10") when the term equals the id.
    # Note: "naip" is a substring of "naip-10", so both match under partial rules.
    # To test exact-only behaviour use a term that isn't a substring of anything else.
    data = _get_collections(client, ids="openaerialmap-10")
    assert len(data["collections"]) == 1
    assert data["collections"][0]["id"] == "openaerialmap-10"


def test_filter_by_partial_id_substring(client: TestClient) -> None:
    # "naip" is a substring of "naip" and "naip-10" — both should be returned.
    data = _get_collections(client, ids="naip")
    ids = {c["id"] for c in data["collections"]}
    assert ids == {"naip", "naip-10"}


def test_filter_partial_id_prefix(client: TestClient) -> None:
    # "openaerialmap" matches "openaerialmap" and "openaerialmap-10".
    data = _get_collections(client, ids="openaerialmap")
    ids = {c["id"] for c in data["collections"]}
    assert ids == {"openaerialmap", "openaerialmap-10"}


def test_filter_by_multiple_partial_ids(client: TestClient) -> None:
    # Two terms — union of their partial matches.
    data = _get_collections(client, ids="naip,openaerialmap-10")
    ids = {c["id"] for c in data["collections"]}
    # "naip" → naip + naip-10; "openaerialmap-10" → openaerialmap-10 (exact)
    assert ids == {"naip", "naip-10", "openaerialmap-10"}


def test_filter_partial_id_case_insensitive(client: TestClient) -> None:
    data = _get_collections(client, ids="NAIP")
    ids = {c["id"] for c in data["collections"]}
    assert ids == {"naip", "naip-10"}


def test_filter_by_unknown_id_returns_empty(client: TestClient) -> None:
    data = _get_collections(client, ids="does-not-exist")
    assert data["collections"] == []


# ---------------------------------------------------------------------------
# q (free-text) filter
# ---------------------------------------------------------------------------


def test_freetext_matches_description(client: TestClient) -> None:
    # All test collections share the word "rustac" in their description.
    data = _get_collections(client, q="rustac")
    ids = {c["id"] for c in data["collections"]}
    assert ids == ALL_IDS


def test_freetext_no_match_returns_empty(client: TestClient) -> None:
    data = _get_collections(client, q="zzznomatchzzz")
    assert data["collections"] == []


def test_freetext_comma_separated_terms_are_or_ed(client: TestClient) -> None:
    # The Free-Text extension separates terms with commas and matches any of
    # them, so an unmatched term must not narrow the result.
    data = _get_collections(client, q="zzznomatchzzz,rustac")
    assert {c["id"] for c in data["collections"]} == ALL_IDS

    # Only "naip" has "10000 items" in its description.
    data = _get_collections(client, q="10000,zzznomatchzzz")
    assert {c["id"] for c in data["collections"]} == {"naip"}


def test_freetext_spaces_are_part_of_the_term(client: TestClient) -> None:
    # Spaces have no special meaning: this is one term, not two.
    data = _get_collections(client, q="from 10000 items")
    assert {c["id"] for c in data["collections"]} == {"naip"}

    # ... so the same words in the wrong order match nothing.
    data = _get_collections(client, q="10000 from items")
    assert data["collections"] == []


def test_freetext_is_case_insensitive(client: TestClient) -> None:
    data = _get_collections(client, q="RUSTAC")
    assert {c["id"] for c in data["collections"]} == ALL_IDS


# ---------------------------------------------------------------------------
# bbox filter
# ---------------------------------------------------------------------------


def test_bbox_overlapping_naip(client: TestClient) -> None:
    # naip bbox: -109.13, 36.93, -101.99, 41.07  (Colorado / Wyoming)
    data = _get_collections(client, bbox="-108,37,-102,41")
    ids = {c["id"] for c in data["collections"]}
    assert "naip" in ids
    assert "naip-10" in ids  # naip-10 is within naip's extent


def test_bbox_no_overlap_returns_empty(client: TestClient) -> None:
    # Use a bbox over the middle of the Pacific — no collection covers it exclusively
    # openaerialmap covers almost the whole world, so use an Arctic bbox
    data = _get_collections(client, bbox="0,80,10,90")
    # naip and naip-10 are entirely in the US mid-west, so they should be excluded
    ids = {c["id"] for c in data["collections"]}
    assert "naip" not in ids
    assert "naip-10" not in ids


# ---------------------------------------------------------------------------
# datetime filter
# ---------------------------------------------------------------------------


def test_datetime_single_overlapping(client: TestClient) -> None:
    # naip temporal: 2019-09-19 → 2022-08-27
    data = _get_collections(client, datetime="2021-01-01T00:00:00Z")
    ids = {c["id"] for c in data["collections"]}
    assert "naip" in ids


def test_datetime_interval_no_overlap(client: TestClient) -> None:
    # Before all collections (naip starts 2019-09-19)
    data = _get_collections(
        client, datetime="1900-01-01T00:00:00Z/1901-01-01T00:00:00Z"
    )
    ids = {c["id"] for c in data["collections"]}
    assert "naip" not in ids
    assert "naip-10" not in ids


def test_datetime_open_end_interval(client: TestClient) -> None:
    # Open-ended from 2020 should include naip
    data = _get_collections(client, datetime="2020-01-01T00:00:00Z/..")
    ids = {c["id"] for c in data["collections"]}
    assert "naip" in ids


# ---------------------------------------------------------------------------
# limit / offset (pagination)
# ---------------------------------------------------------------------------


def test_limit_reduces_result_count(client: TestClient) -> None:
    data = _get_collections(client, limit=2)
    assert len(data["collections"]) == 2


def test_offset_skips_first_n(client: TestClient) -> None:
    all_data = _get_collections(client)
    all_ids = [c["id"] for c in all_data["collections"]]

    offset_data = _get_collections(client, offset=1)
    offset_ids = [c["id"] for c in offset_data["collections"]]

    assert offset_ids == all_ids[1:]


def test_limit_and_offset_together(client: TestClient) -> None:
    all_data = _get_collections(client)
    all_ids = [c["id"] for c in all_data["collections"]]

    paged = _get_collections(client, limit=2, offset=1)
    assert [c["id"] for c in paged["collections"]] == all_ids[1:3]


def test_next_link_present_when_more_results(client: TestClient) -> None:
    data = _get_collections(client, limit=1)
    rels = {link["rel"] for link in data["links"]}
    assert "next" in rels


def test_no_next_link_when_all_results_returned(client: TestClient) -> None:
    data = _get_collections(client)
    rels = {link["rel"] for link in data["links"]}
    assert "next" not in rels


def test_prev_link_present_after_offset(client: TestClient) -> None:
    data = _get_collections(client, limit=2, offset=2)
    rels = {link["rel"] for link in data["links"]}
    assert "prev" in rels


def test_next_link_href_encodes_offset(client: TestClient) -> None:
    data = _get_collections(client, limit=1)
    next_link = next(link for link in data["links"] if link["rel"] == "next")
    parsed = urllib.parse.urlparse(next_link["href"])
    qs = urllib.parse.parse_qs(parsed.query)
    assert qs["offset"] == ["1"]
    assert qs["limit"] == ["1"]


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


def test_ids_and_q_combined(client: TestClient) -> None:
    # "openaerialmap" (partial) matches openaerialmap + openaerialmap-10;
    # q="17702" further narrows to the one with "17702 items" in its description.
    data = _get_collections(client, ids="openaerialmap", q="17702")
    ids = {c["id"] for c in data["collections"]}
    assert ids == {"openaerialmap"}


def test_bbox_and_limit(client: TestClient) -> None:
    data = _get_collections(client, bbox="-180,-90,180,90", limit=2)
    assert len(data["collections"]) <= 2
