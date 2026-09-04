import copy
import json
import urllib.parse
from typing import Any, cast

from fastapi import HTTPException
from pydantic import ValidationError
from rustac import DuckdbClient
from stac_fastapi.types.core import BaseCoreClient
from stac_fastapi.types.errors import NotFoundError
from stac_fastapi.types.search import BaseSearchPostRequest
from stac_fastapi.types.stac import Collection, Collections, Item, ItemCollection
from stac_pydantic.shared import BBox
from starlette.requests import Request

from .models import PostSearchRequestModel

DEFAULT_LIMIT = 10_000


# rustac's DuckdbClient does not implement the STAC Query Extension's `query`
# parameter directly (it raises `RustacError: query is not implemented`), so
# it must be translated into an equivalent CQL2 filter before being forwarded.
_QUERY_EXT_OPERATORS = {
    "eq": "=",
    "neq": "<>",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
}


def _cql2_text_identifier(prop: str) -> str:
    """Quote a property name for CQL2-text so it can't inject filter syntax."""
    return '"' + prop.replace('"', '""') + '"'


def _cql2_text_literal(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if value is None:
        return "NULL"
    return str(value)


def _query_ext_to_cql2_text(query: dict[str, dict[str, Any]]) -> str:
    """Translate a STAC Query Extension object into a CQL2-text filter."""
    clauses = []
    for prop, ops in query.items():
        ident = _cql2_text_identifier(prop)
        for op, value in ops.items():
            if op in _QUERY_EXT_OPERATORS:
                clauses.append(
                    f"{ident} {_QUERY_EXT_OPERATORS[op]} {_cql2_text_literal(value)}"
                )
            elif op == "in":
                values = ", ".join(_cql2_text_literal(v) for v in value)
                clauses.append(f"{ident} IN ({values})")
            elif op == "startsWith":
                clauses.append(f"{ident} LIKE {_cql2_text_literal(f'{value}%')}")
            elif op == "endsWith":
                clauses.append(f"{ident} LIKE {_cql2_text_literal(f'%{value}')}")
            elif op == "contains":
                clauses.append(f"{ident} LIKE {_cql2_text_literal(f'%{value}%')}")
            else:
                raise HTTPException(400, f"Unsupported query operator: {op!r}")
    return " AND ".join(clauses)


def _query_ext_to_cql2_json(query: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Translate a STAC Query Extension object into a CQL2-json filter."""
    clauses: list[dict[str, Any]] = []
    for prop, ops in query.items():
        for op, value in ops.items():
            if op in _QUERY_EXT_OPERATORS:
                clauses.append(
                    {
                        "op": _QUERY_EXT_OPERATORS[op],
                        "args": [{"property": prop}, value],
                    }
                )
            elif op == "in":
                clauses.append({"op": "in", "args": [{"property": prop}, list(value)]})
            elif op == "startsWith":
                clauses.append(
                    {"op": "like", "args": [{"property": prop}, f"{value}%"]}
                )
            elif op == "endsWith":
                clauses.append(
                    {"op": "like", "args": [{"property": prop}, f"%{value}"]}
                )
            elif op == "contains":
                clauses.append(
                    {"op": "like", "args": [{"property": prop}, f"%{value}%"]}
                )
            else:
                raise HTTPException(400, f"Unsupported query operator: {op!r}")
    if len(clauses) == 1:
        return clauses[0]
    return {"op": "and", "args": clauses}


class Client(BaseCoreClient):
    """A stac-fastapi-geoparquet client."""

    def all_collections(self, **kwargs: Any) -> Collections:
        request = kwargs.pop("request")
        collections = cast(dict[str, Collection], request.state.collections)
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

    def get_collection(self, collection_id: str, **kwargs: Any) -> Collection:
        request = kwargs.pop("request")
        collections = cast(dict[str, Collection], request.state.collections)
        if collection := collections.get(collection_id):
            return collection_with_links(collection, request)
        else:
            raise NotFoundError(f"Collection does not exist: {collection_id}")

    def get_item(self, item_id: str, collection_id: str, **kwargs: Any) -> Item:
        item_collection = self.get_search(
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

    def get_search(  # type: ignore
        self,
        collections: list[str] | None = None,
        ids: list[str] | None = None,
        bbox: BBox | str | None = None,
        intersects: str | None = None,
        datetime: str | None = None,
        limit: int | None = 10,
        **kwargs: Any,
    ) -> ItemCollection:
        request = kwargs.pop("request")

        if intersects:
            try:
                maybe_intersects = json.loads(intersects)
            except json.JSONDecodeError as e:
                raise HTTPException(400, f"invalid intersects: {e}")
        else:
            maybe_intersects = None

        if isinstance(bbox, str):
            if bbox.startswith("["):
                bbox = bbox[1:]
            if bbox.endswith("]"):
                bbox = bbox[:-1]
            try:
                bbox = cast(BBox, [float(s) for s in bbox.split(",")])
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
            url=str(request.url_for("Search")),
            **kwargs,
        )

    def item_collection(
        self,
        collection_id: str,
        bbox: BBox | None = None,
        datetime: str | None = None,
        limit: int = 10,
        token: str | None = None,
        **kwargs: Any,
    ) -> ItemCollection:
        request = kwargs.pop("request")
        offset = kwargs.pop("offset", None)
        search = PostSearchRequestModel(
            collections=[collection_id],
            bbox=bbox,
            datetime=datetime,
            limit=limit,
            offset=offset,
        )
        return self.search(
            request=request,
            search=cast(BaseSearchPostRequest, search),
            url=str(request.url_for("Get ItemCollection", collection_id=collection_id)),
            **kwargs,
        )

    def post_search(
        self, search_request: BaseSearchPostRequest, **kwargs: Any
    ) -> ItemCollection:
        request = kwargs.pop("request")
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
        hrefs = cast(dict[str, str], request.state.hrefs)

        if search.collections:
            collections = search.collections
        else:
            collections = list(hrefs.keys())

        search_dict = search.model_dump(exclude_none=True, by_alias=True)
        search_dict.update(**kwargs)

        search_dict.pop("filter_crs", None)
        if filter_expr := search_dict.pop("filter_expr", None):
            search_dict["filter"] = filter_expr
        # The key varies by request method: POST's `search_dict` comes from
        # `model_dump(by_alias=True)`, which uses the pydantic alias
        # "filter-lang" (hyphen); GET's arrives via **kwargs from a FastAPI
        # dependency function, which can only use the valid identifier
        # "filter_lang" (underscore). Read both, so the `query` translation
        # below always sees the language the caller actually asked for.
        filter_lang: str | None = search_dict.pop(
            "filter-lang", None
        ) or search_dict.pop("filter_lang", None)
        if filter_lang:
            search_dict["filter-lang"] = filter_lang
        if "filter" not in search_dict:
            search_dict.pop("filter-lang", None)
        if fields := search_dict.pop("fields", None):
            if isinstance(fields, list):
                include = []
                exclude = []
                for field in fields:
                    if field.startswith("-"):
                        exclude.append(field)
                    else:
                        include.append(field)
                search_dict.update({"include": include, "exclude": exclude})
            elif isinstance(fields, dict):
                search_dict.update(
                    {
                        "include": list(fields.get("include", [])),
                        "exclude": list(fields.get("exclude", [])),
                    }
                )
            else:
                raise HTTPException(400, f"unexpected fields type: {fields}")
        if sortby := search_dict.pop("sortby", None):
            search_dict["sortby"] = sortby

        # Translate the Query Extension's `query` into an equivalent CQL2
        # filter — rustac's DuckdbClient only understands `filter`/CQL2 and
        # raises RustacError("query is not implemented") if `query` reaches it.
        if query := search_dict.pop("query", None):
            # On GET requests `query` arrives as a JSON-encoded string.
            if isinstance(query, str):
                try:
                    query = json.loads(query)
                except json.JSONDecodeError as e:
                    raise HTTPException(400, f"invalid query: {e}")
            if not isinstance(query, dict):
                raise HTTPException(400, "invalid query: expected a JSON object")
            filter_lang = filter_lang or "cql2-text"
            if filter_lang == "cql2-text":
                query_filter: Any = _query_ext_to_cql2_text(query)
                search_dict["filter"] = (
                    f"({search_dict['filter']}) AND ({query_filter})"
                    if search_dict.get("filter")
                    else query_filter
                )
            elif filter_lang == "cql2-json":
                query_filter = _query_ext_to_cql2_json(query)
                search_dict["filter"] = (
                    {"op": "and", "args": [search_dict["filter"], query_filter]}
                    if search_dict.get("filter")
                    else query_filter
                )
            else:
                raise HTTPException(
                    400,
                    f"Unsupported filter-lang for the 'query' extension: "
                    f"{filter_lang!r}",
                )
            search_dict["filter-lang"] = filter_lang

        limit = search_dict.get("limit", DEFAULT_LIMIT)
        offset = search_dict.get("offset", 0) or 0
        items: list[Item] = []
        while collections:
            collection = collections.pop(0)
            if href := hrefs.get(collection):
                collection_search_dict = copy.deepcopy(search_dict)
                collection_search_dict.update(
                    {
                        "collections": [],
                        "limit": limit,
                        "offset": offset,
                    }
                )
                collection_items = client.search(href, **collection_search_dict)
                for item in collection_items:
                    # Careful ... we aren't updating `collection_items` with the
                    # correct links.
                    items.append(
                        self.item_with_links(cast(Item, item), request, collection)
                    )
                if len(items) >= limit:
                    collections.insert(0, collection)
                    offset = offset + len(collection_items)
                    break
                else:
                    limit = limit - len(collection_items)
                    offset = 0

        num_items = len(items)

        if collections and ((search.limit or DEFAULT_LIMIT) <= num_items):
            next_search = copy.deepcopy(search_dict)
            next_search["limit"] = search.limit or DEFAULT_LIMIT
            next_search["offset"] = offset
            next_search["collections"] = collections
        else:
            next_search = None

        links: list[dict[str, Any]] = [
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
                if bbox := next_search.get("bbox"):
                    next_search["bbox"] = ",".join(map(str, bbox))
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

        return {
            "type": "FeatureCollection",
            "features": items,
            "links": links,
        }

    def item_with_links(self, item: Item, request: Request, collection: str) -> Item:
        links = [
            {
                "href": str(request.url_for("Landing Page")),
                "rel": "root",
                "type": "application/json",
            },
        ]
        item["collection"] = collection
        href = str(request.url_for("Get Collection", collection_id=collection))
        links.append({"href": href, "rel": "collection", "type": "application/json"})
        links.append({"href": href, "rel": "parent", "type": "application/json"})
        if item_id := item.get("id"):
            links.append(
                {
                    "href": str(
                        request.url_for(
                            "Get Item",
                            collection_id=collection,
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


def collection_with_links(collection: Collection, request: Request) -> Collection:
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
