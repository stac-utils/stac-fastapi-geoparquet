import copy
import json
import urllib.parse
from typing import Any, cast

from fastapi import HTTPException
from pydantic import ValidationError
from rustac import DuckdbClient
from starlette.requests import Request

from stac_fastapi.api.models import BaseSearchPostRequest
from stac_fastapi.types.core import BaseCoreClient
from stac_fastapi.types.errors import NotFoundError
from stac_fastapi.types.rfc3339 import DateTimeType
from stac_fastapi.types.stac import BBox, Collection, Collections, Item, ItemCollection


class Client(BaseCoreClient):  # type: ignore[misc]
    """A stac-fastapi-geoparquet client."""

    def all_collections(self, *, request: Request, **kwargs: Any) -> Collections:
        collections = cast(dict[str, dict[str, Any]], request.state.collections)
        return Collections(
            collections=[
                collection_with_links(c, request) for c in collections.values()
            ],
            links=[
                {
                    "href": str(request.url_for("Landing Page")),
                    "rel": "root",
                    "type": "application/json",
                },
                {
                    "href": str(request.url_for("Get Collections")),
                    "rel": "self",
                    "type": "application/json",
                },
            ],
        )

    def get_collection(
        self, *, request: Request, collection_id: str, **kwargs: Any
    ) -> Collection:
        collections = cast(dict[str, dict[str, Any]], request.state.collections)
        if collection := collections.get(collection_id):
            return collection_with_links(collection, request)
        else:
            raise NotFoundError(f"Collection does not exist: {collection_id}")

    def get_item(
        self, *, request: Request, item_id: str, collection_id: str, **kwargs: Any
    ) -> Item:
        item_collection = self.get_search(
            request=request,
            ids=[item_id],
            collections=[collection_id],
            **kwargs,
        )
        if len(item_collection["features"]) == 1:
            return Item(**item_collection["features"][0])
        else:
            raise NotFoundError(
                f"Item does not exist: {item_id} in collection {collection_id}"
            )

    def get_search(
        self,
        *,
        request: Request,
        collections: list[str] | None = None,
        ids: list[str] | None = None,
        bbox: BBox | str | None = None,
        intersects: str | None = None,
        datetime: DateTimeType | None = None,
        limit: int | None = 10,
        offset: int | None = 0,
        **kwargs: str,
    ) -> ItemCollection:
        if intersects:
            maybe_intersects = json.loads(intersects)
        else:
            maybe_intersects = None

        if isinstance(bbox, str):
            if bbox.startswith("["):
                bbox = bbox[1:]
            if bbox.endswith("]"):
                bbox = bbox[:-1]
            try:
                bbox = [float(s) for s in bbox.split(",")]
            except ValueError as e:
                raise HTTPException(400, f"invalid bbox: {e}")

        try:
            search = BaseSearchPostRequest(
                collections=collections,
                ids=ids,
                bbox=bbox,
                intersects=maybe_intersects,
                datetime=datetime,
                limit=limit,
            )
        except ValidationError as e:
            raise HTTPException(400, f"invalid request: {e}")

        return self.search(
            request=request,
            search=search,
            offset=offset,
            url=str(request.url_for("Search")),
            **kwargs,
        )

    def item_collection(
        self,
        *,
        request: Request,
        collection_id: str,
        bbox: BBox | None = None,
        datetime: DateTimeType | None = None,
        limit: int = 10,
        offset: int = 0,
        **kwargs: str,
    ) -> ItemCollection:
        search = BaseSearchPostRequest(
            collections=[collection_id],
            bbox=bbox,
            datetime=datetime,
            limit=limit,
            offset=offset,
        )
        return self.search(
            request=request,
            search=search,
            url=str(request.url_for("Get ItemCollection", collection_id=collection_id)),
            **kwargs,
        )

    def post_search(
        self, search_request: BaseSearchPostRequest, *, request: Request, **kwargs: Any
    ) -> ItemCollection:
        return self.search(
            search=search_request,
            request=request,
            url=str(request.url_for("Search")),
            **kwargs,
        )

    def search(
        self,
        *,
        request: Request,
        url: str,
        search: BaseSearchPostRequest,
        **kwargs: Any,
    ) -> ItemCollection:
        client = cast(DuckdbClient, request.state.client)
        hrefs = cast(dict[str, list[str]], request.state.hrefs)

        if search.collections:
            collections = search.collections
        else:
            collections = list(hrefs.keys())

        search_dict = search.model_dump(exclude_none=True)
        search_dict["offset"] = kwargs.get("offset", 0)
        items: list[dict[str, Any]] = []
        while collections and not (search.limit and len(items) >= search.limit):
            collection = collections.pop(0)
            if collection_hrefs := hrefs.get(collection):
                collection_search_dict = copy.deepcopy(search_dict)
                collection_search_dict["collections"] = [collection]
                for href in collection_hrefs:
                    items.extend(client.search(href, **collection_search_dict))
                    if search.limit and len(items) >= search.limit:
                        collections.insert(0, collection)
                        break
                search_dict["offset"] = 0

        item_collection = {
            "type": "FeatureCollection",
            "features": [self.item_with_links(item, request) for item in items],
        }
        num_items = len(item_collection["features"])
        offset = int(search_dict.get("offset", None) or 0)

        if search.limit and search.limit <= num_items:
            next_search = copy.deepcopy(search_dict)
            next_search["limit"] = search.limit
            next_search["offset"] = offset + search.limit
            next_search["collections"] = collections
        else:
            next_search = None

        links = [
            {
                "href": str(request.url_for("Landing Page")),
                "rel": "root",
                "type": "application/json",
            }
        ]
        if request.method == "GET":
            links.append(
                {
                    "href": str(request.url),
                    "rel": "self",
                    "type": "application/geo+json",
                    "method": "GET",
                }
            )
            if next_search:
                if "collections" in next_search:
                    next_search["collections"] = ",".join(collections)
                links.append(
                    {
                        "href": url + "?" + urllib.parse.urlencode(next_search),
                        "rel": "next",
                        "type": "application/geo+json",
                        "method": "GET",
                    }
                )
        else:
            links.append(
                {
                    "href": str(request.url),
                    "rel": "self",
                    "type": "application/geo+json",
                    "method": "POST",
                    "body": search_dict,
                }
            )
            if next_search:
                links.append(
                    {
                        "href": url,
                        "rel": "next",
                        "type": "application/geo+json",
                        "method": "POST",
                        "body": next_search,
                    }
                )

        item_collection["links"] = links
        return ItemCollection(**item_collection)

    def item_with_links(self, item: dict[str, Any], request: Request) -> dict[str, Any]:
        links = [
            {
                "href": str(request.url_for("Landing Page")),
                "rel": "root",
                "type": "application/json",
            },
        ]
        if collection_id := item.get("collection"):
            href = str(request.url_for("Get Collection", collection_id=collection_id))
            links.append(
                {"href": href, "rel": "collection", "type": "application/json"}
            )
            links.append({"href": href, "rel": "parent", "type": "application/json"})
            if item_id := item.get("id"):
                links.append(
                    {
                        "href": str(
                            request.url_for(
                                "Get Item",
                                collection_id=collection_id,
                                item_id=item_id,
                            )
                        ),
                        "rel": "self",
                        "type": "application/geo+json",
                    }
                )
        for link in item.get("links", []):
            if link["rel"] not in ("root", "parent", "collection", "self"):
                links.append(link)
        item["links"] = links
        return item


def collection_with_links(
    collection: dict[str, Any], request: Request
) -> dict[str, Any]:
    collection["links"] = [
        {
            "href": str(request.url_for("Landing Page")),
            "rel": "root",
            "type": "application/json",
        },
        {
            "href": str(request.url_for("Landing Page")),
            "rel": "parent",
            "type": "application/json",
        },
        {
            "href": str(
                request.url_for("Get Collection", collection_id=collection["id"])
            ),
            "rel": "self",
            "type": "application/json",
        },
        {
            "href": str(
                request.url_for("Get ItemCollection", collection_id=collection["id"])
            ),
            "rel": "items",
            "type": "application/geo+json",
        },
    ]
    return collection
