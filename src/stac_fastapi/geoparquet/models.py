from typing import Annotated

import attr
import stac_fastapi.api.models
from fastapi import Query
from stac_fastapi.api.models import ItemCollectionUri
from stac_fastapi.extensions.core.collection_search import CollectionSearchExtension
from stac_fastapi.extensions.core.fields import FieldsExtension
from stac_fastapi.extensions.core.filter import SearchFilterExtension
from stac_fastapi.extensions.core.free_text import (
    FreeTextConformanceClasses,
    FreeTextExtension,
)
from stac_fastapi.extensions.core.pagination import OffsetPaginationExtension
from stac_fastapi.extensions.core.sort import SortExtension
from stac_fastapi.types.search import APIRequest, BaseSearchPostRequest
from stac_pydantic.shared import BBox

from .search import FixedSearchGetRequest, _bbox_converter, _ids_converter

EXTENSIONS = [
    OffsetPaginationExtension(),
    SearchFilterExtension(),
    FieldsExtension(),
    SortExtension(),
]

GetSearchRequestModel = stac_fastapi.api.models.create_get_request_model(
    base_model=FixedSearchGetRequest, extensions=EXTENSIONS
)
PostSearchRequestModel = stac_fastapi.api.models.create_post_request_model(
    base_model=BaseSearchPostRequest, extensions=EXTENSIONS
)
ItemsGetRequestModel = stac_fastapi.api.models.create_get_request_model(
    base_model=ItemCollectionUri, extensions=EXTENSIONS
)


# ---------------------------------------------------------------------------
# Collection-search extensions (used for GET /collections)
#
# Only advertise what `Client.all_collections` actually implements:
# ids/bbox/datetime/limit (core collection-search) plus free-text `q`.
# ---------------------------------------------------------------------------

collection_search_ext = CollectionSearchExtension.from_extensions(
    [FreeTextExtension(conformance_classes=[FreeTextConformanceClasses.COLLECTIONS])]
)


def _q_converter(
    val: Annotated[
        str | None,
        Query(
            description=(
                "Free-text search terms, comma-separated. A collection matches "
                "if any term appears in its title, description, or keywords "
                "(case-insensitive, partial matches count)."
            ),
            openapi_examples={
                "user-provided": {"value": None},
                "single-term": {"value": "imagery"},
                "multi-term": {"value": "ocean,coast"},
            },
        ),
    ] = None,
) -> list[str] | None:
    """Split a comma-separated free-text query string into individual terms.

    Comma-separated with OR semantics is what the Free-Text Search extension
    specifies; spaces carry no meaning, so a term may contain them.
    """
    if val:
        return [term.strip() for term in val.split(",") if term.strip()]
    return None


@attr.s
class CollectionSearchRequest(APIRequest):
    """Query parameters for ``GET /collections`` collection search."""

    ids: list[str] | None = attr.ib(default=None, converter=_ids_converter)
    bbox: BBox | None = attr.ib(default=None, converter=_bbox_converter)
    datetime: Annotated[
        str | None,
        Query(
            description=(
                "Only return collections whose temporal extent overlaps this value. "
                "Either a date-time or an interval (open or closed). "
                "Date and time expressions adhere to RFC 3339. "
                "Open intervals are expressed using double-dots."
            ),
            openapi_examples={
                "user-provided": {"value": None},
                "datetime": {"value": "2018-02-12T23:20:50Z"},
                "closed-interval": {
                    "value": "2018-02-12T00:00:00Z/2018-03-18T12:31:12Z"
                },
                "open-interval-from": {"value": "2018-02-12T00:00:00Z/.."},
                "open-interval-to": {"value": "../2018-03-18T12:31:12Z"},
            },
        ),
    ] = attr.ib(default=None)
    q: list[str] | None = attr.ib(default=None, converter=_q_converter)
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=10_000,
            description="Maximum number of collections to return (1–10 000).",
        ),
    ] = attr.ib(default=None)
    offset: Annotated[
        int | None,
        Query(ge=0, description="Number of collections to skip for pagination."),
    ] = attr.ib(default=None)


# Override the GET model with the hand-crafted one so the /collections
# endpoint uses CollectionSearchRequest instead of the auto-generated model.
collection_search_ext.GET = CollectionSearchRequest
