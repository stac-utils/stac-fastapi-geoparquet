# We want to customize our request validation so we copy
# https://github.com/stac-utils/stac-fastapi/blob/main/stac_fastapi/types/stac_fastapi/types/search.py

from typing import Annotated, Union

import attr
from fastapi import HTTPException, Query
from pydantic import Field, PositiveInt
from pydantic.functional_validators import AfterValidator
from stac_pydantic.api import Search
from stac_pydantic.shared import BBox

from stac_fastapi.types.rfc3339 import DateTimeType, str_to_interval
from stac_fastapi.types.search import APIRequest

MAX_LIMIT = 10_000


def crop(v: PositiveInt) -> PositiveInt:
    if v > MAX_LIMIT:
        return MAX_LIMIT
    else:
        return v


def str2list(x: str) -> list[str] | None:
    if x:
        return x.split(",")
    else:
        return None


def str2bbox(x: str) -> BBox | None:
    if x:
        x_as_list = str2list(x)
        assert x_as_list  # we knew the x wasn't empty
        if len(x_as_list) not in (4, 6):
            raise HTTPException(
                400, f"invalid bbox length (should be 4 or 6): {x_as_list}"
            )
        try:
            return tuple(float(v) for v in x_as_list)  # type: ignore
        except ValueError as e:
            raise HTTPException(400, f"invalid bbox: {e}")
    else:
        return None


def _collection_converter(
    val: Annotated[
        str | None,
        Query(
            description="Array of collection Ids to search for items.",
            json_schema_extra={
                "example": "collection1,collection2",
            },
        ),
    ] = None,
) -> list[str] | None:
    if val:
        return str2list(val)
    else:
        return None


def _ids_converter(
    val: Annotated[
        str | None,
        Query(
            description="Array of Item ids to return.",
            json_schema_extra={
                "example": "item1,item2",
            },
        ),
    ] = None,
) -> list[str] | None:
    if val:
        return str2list(val)
    else:
        return None


def _bbox_converter(
    val: Annotated[
        str | None,
        Query(
            description="Only return items intersecting this bounding box. Mutually exclusive with **intersects**.",  # noqa: E501
            json_schema_extra={
                "example": "-175.05,-85.05,175.05,85.05",
            },
        ),
    ] = None,
) -> BBox | None:
    if val:
        return str2bbox(val)
    else:
        return None


def _datetime_converter(
    val: Annotated[
        str | None,
        Query(
            description="""Only return items that have a temporal property that intersects this value.\n
Either a date-time or an interval, open or closed. Date and time expressions adhere to RFC 3339. Open intervals are expressed using double-dots.""",  # noqa: E501
            openapi_examples={
                "datetime": {"value": "2018-02-12T23:20:50Z"},
                "closed-interval": {
                    "value": "2018-02-12T00:00:00Z/2018-03-18T12:31:12Z"
                },
                "open-interval-from": {"value": "2018-02-12T00:00:00Z/.."},
                "open-interval-to": {"value": "../2018-03-18T12:31:12Z"},
            },
        ),
    ] = None,
) -> DateTimeType | None:
    return str_to_interval(val)


# Be careful: https://github.com/samuelcolvin/pydantic/issues/1423#issuecomment-642797287
NumType = Union[float, int]
Limit = Annotated[PositiveInt, AfterValidator(crop)]


@attr.s
class SearchGetRequest(APIRequest):  # type: ignore
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
    datetime: DateTimeType | None = attr.ib(default=None, converter=_datetime_converter)
    limit: Annotated[
        Limit | None,
        Query(
            description="Limits the number of results that are included in each page of the response (capped to 10_000)."  # noqa: E501
        ),
    ] = attr.ib(default=10)


class SearchPostRequest(Search):
    """Base arguments for POST Request."""

    limit: Limit | None = Field(
        10,
        description="Limits the number of results that are included in each page of the response (capped to 10_000).",  # noqa: E501
    )
