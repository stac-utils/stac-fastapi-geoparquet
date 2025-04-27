from typing import Annotated, Any

import attr
from fastapi import HTTPException, Query
from pydantic import PositiveInt
from pydantic.functional_validators import AfterValidator
from stac_pydantic.shared import BBox

from stac_fastapi.types.rfc3339 import str_to_interval
from stac_fastapi.types.search import APIRequest, DatetimeMixin

DateTimeQueryType = Annotated[
    str | None,
    Query(
        description="""Only return items that have a temporal property that intersects this value.\n
Either a date-time or an interval, open or closed. Date and time expressions adhere to RFC 3339. Open intervals are expressed using double-dots.""",  # noqa: E501
        openapi_examples={
            "user-provided": {"value": None},
            "datetime": {"value": "2018-02-12T23:20:50Z"},
            "closed-interval": {"value": "2018-02-12T00:00:00Z/2018-03-18T12:31:12Z"},
            "open-interval-from": {"value": "2018-02-12T00:00:00Z/.."},
            "open-interval-to": {"value": "../2018-03-18T12:31:12Z"},
        },
    ),
]


def crop(v: PositiveInt) -> PositiveInt:
    """Crop value to 10,000."""
    limit = 10_000
    if v > limit:
        v = limit
    return v


Limit = Annotated[PositiveInt, AfterValidator(crop)]


def _validate_datetime(instance: Any, attribute: Any, value: str) -> None:
    """Validate Datetime."""
    _ = str_to_interval(value)


def _collection_converter(
    val: Annotated[
        str | None,
        Query(
            description="Array of collection Ids to search for items.",
            openapi_examples={
                "user-provided": {"value": None},
                "single-collection": {"value": "collection1"},
                "multi-collections": {"value": "collection1,collection2"},
            },
        ),
    ] = None,
) -> list[str] | None:
    if val:
        return val.split(",")
    return None


def _ids_converter(
    val: Annotated[
        str | None,
        Query(
            description="Array of Item ids to return.",
            openapi_examples={
                "user-provided": {"value": None},
                "single-item": {"value": "item1"},
                "multi-items": {"value": "item1,item2"},
            },
        ),
    ] = None,
) -> list[str] | None:
    if val:
        return val.split(",")
    return None


def _bbox_converter(
    val: Annotated[
        str | None,
        Query(
            description="Only return items intersecting this bounding box. "
            "Mutually exclusive with **intersects**.",
            openapi_examples={
                "user-provided": {"value": None},
                "Montreal": {"value": "-73.896103,45.364690,-73.413734,45.674283"},
            },
        ),
    ] = None,
) -> BBox | None:
    if val:
        try:
            t = tuple(float(v) for v in val.split(","))
        except ValueError as e:
            raise HTTPException(400, f"invalid bbox: {e}")
        if len(t) not in (4, 6):
            raise HTTPException(400, f"invalid bbox count: {len(t)}")
        return t  # type: ignore[return-value]
    else:
        return None


@attr.s
class FixedSearchGetRequest(APIRequest, DatetimeMixin):  # type: ignore[misc]
    """Base arguments for GET Request."""

    collections: list[str] | None = attr.ib(
        default=None, converter=_collection_converter
    )
    ids: list[str] | None = attr.ib(default=None, converter=_ids_converter)
    bbox: BBox | None = attr.ib(default=None, converter=_bbox_converter)
    intersects: Annotated[
        str | None,
        Query(
            description="""Only return items intersecting this GeoJSON Geometry. Mutually exclusive with **bbox**. \n
*Remember to URL encode the GeoJSON geometry when using GET request*.""",  # noqa: E501
            openapi_examples={
                "user-provided": {"value": None},
                "madrid": {
                    "value": {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "coordinates": [
                                [
                                    [-3.8549260500072933, 40.54923557897152],
                                    [-3.8549260500072933, 40.29428000041938],
                                    [-3.516597069715033, 40.29428000041938],
                                    [-3.516597069715033, 40.54923557897152],
                                    [-3.8549260500072933, 40.54923557897152],
                                ]
                            ],
                            "type": "Polygon",
                        },
                    },
                },
                "new-york": {
                    "value": {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "coordinates": [
                                [
                                    [-74.50117532354284, 41.128266394414055],
                                    [-74.50117532354284, 40.35633909727355],
                                    [-73.46713183168603, 40.35633909727355],
                                    [-73.46713183168603, 41.128266394414055],
                                    [-74.50117532354284, 41.128266394414055],
                                ]
                            ],
                            "type": "Polygon",
                        },
                    },
                },
            },
        ),
    ] = attr.ib(default=None)
    datetime: DateTimeQueryType = attr.ib(default=None, validator=_validate_datetime)
    limit: Annotated[
        Limit | None,
        Query(
            description="Limits the number of results that are included in each page of the response (capped to 10_000)."  # noqa: E501
        ),
    ] = attr.ib(default=100)
