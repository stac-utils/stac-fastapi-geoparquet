import copy
import json
import logging
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
from .schema import describe_columns, text_columns

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10_000

# Free-text searchable columns, keyed by geoparquet href. A file's schema
# doesn't change under us, and the alternative is a DESCRIBE per `q` search.
_TEXT_COLUMNS_CACHE: dict[str, list[str]] = {}


def _cql2_text_identifier(prop: str) -> str:
    """Quote a property name for CQL2-text so it can't inject filter syntax."""
    return '"' + prop.replace('"', '""') + '"'


def _cql2_text_literal(value: str) -> str:
    """Quote a string for CQL2-text."""
    return "'" + value.replace("'", "''") + "'"


def _free_text_clauses_text(terms: list[str], columns: list[str]) -> list[str]:
    """One CQL2-text predicate per (term, column) pair.

    Per the Free-Text Search extension matching is case-insensitive and
    partial, hence ``CASEI`` around a ``LIKE '%term%'``.
    """
    return [
        f"CASEI({_cql2_text_identifier(column)}) LIKE "
        f"CASEI({_cql2_text_literal(f'%{term}%')})"
        for term in terms
        for column in columns
    ]


def _free_text_clauses_json(
    terms: list[str], columns: list[str]
) -> list[dict[str, Any]]:
    """The CQL2-json equivalent of :func:`_free_text_clauses_text`."""
    return [
        {
            "op": "like",
            "args": [
                {"op": "casei", "args": [{"property": column}]},
                {"op": "casei", "args": [f"%{term}%"]},
            ],
        }
        for term in terms
        for column in columns
    ]


def _apply_free_text(
    search_dict: dict[str, Any],
    terms: list[str],
    columns: list[str],
    filter_lang: str,
) -> None:
    """Fold a free-text search into ``search_dict``'s filter, in place.

    An item matches if any term appears in any text column, so the natural
    encoding is ``existing AND (a OR b OR ...)``. That form is miscomputed by
    the backend today: a parenthesised OR nested under an AND loses its
    grouping on the way to SQL, so rows that fail ``existing`` come back
    anyway. Distributing the disjunction into ``(existing AND a) OR (existing
    AND b)`` needs no grouping to survive, because SQL binds AND tighter than
    OR.
    """
    existing = search_dict.get("filter")
    if filter_lang == "cql2-text":
        clauses = _free_text_clauses_text(terms, columns)
        if existing:
            search_dict["filter"] = " OR ".join(
                f"({existing}) AND ({clause})" for clause in clauses
            )
        else:
            search_dict["filter"] = " OR ".join(clauses)
    else:
        json_clauses: list[Any] = list(_free_text_clauses_json(terms, columns))
        if existing:
            json_clauses = [
                {"op": "and", "args": [existing, clause]} for clause in json_clauses
            ]
        search_dict["filter"] = (
            json_clauses[0]
            if len(json_clauses) == 1
            else {"op": "or", "args": json_clauses}
        )
    search_dict["filter-lang"] = filter_lang


class Client(BaseCoreClient):
    """A stac-fastapi-geoparquet client."""

    def text_columns(self, href: str, request: Request) -> list[str]:
        """Return the columns a free-text search should look at."""
        if href not in _TEXT_COLUMNS_CACHE:
            client = cast(DuckdbClient, request.state.client)
            try:
                columns = text_columns(describe_columns(client, href))
            except Exception:
                logger.exception("Could not read the schema of %s", href)
                columns = []
            _TEXT_COLUMNS_CACHE[href] = columns
        return _TEXT_COLUMNS_CACHE[href]

    def visible_collections(self, request: Request) -> dict[str, Collection]:
        """Return the collections this request is allowed to see.

        Every endpoint goes through here instead of reading
        ``request.state.collections`` directly, so a subclass can hide
        collections from a caller by overriding this one method.
        """
        return cast(dict[str, Collection], request.state.collections)

    def search_collection(
        self,
        collection_id: str,
        href: str,
        search_dict: dict[str, Any],
        request: Request,
    ) -> list[Item]:
        """Run one collection's share of a search against its geoparquet.

        The single point where a search reaches the DuckDB client, so a
        subclass can adjust ``search_dict`` (extra filters, extra projected
        columns) or post-process the returned items.
        """
        client = cast(DuckdbClient, request.state.client)
        return cast(list[Item], client.search(href, **search_dict))

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
            maybe_intersects = json.loads(intersects)
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
        # A GET parameter the caller didn't provide arrives as None. The POST
        # body already excludes those (`exclude_none`); do the same here so an
        # unset parameter isn't forwarded to the backend or echoed into the
        # pagination links as the literal string "None".
        search_dict.update({k: v for k, v in kwargs.items() if v is not None})

        search_dict.pop("filter_crs", None)
        if filter_expr := search_dict.pop("filter_expr", None):
            search_dict["filter"] = filter_expr
        # POST spells it "filter-lang" (the pydantic alias) and GET spells it
        # "filter_lang" (a Python identifier, via the dependency function), so
        # both are read here.
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

        # The Free-Text extension's `q` is resolved per collection (the set of
        # text columns differs between them), so it's translated inside the
        # loop below rather than here.
        free_text: list[str] | None = search_dict.get("q") or None
        if isinstance(free_text, str):
            # GET requests hand it over as a comma-separated string.
            free_text = [term.strip() for term in free_text.split(",") if term.strip()]
        if free_text:
            filter_lang = filter_lang or "cql2-text"
            if filter_lang not in ("cql2-text", "cql2-json"):
                raise HTTPException(
                    400,
                    f"Unsupported filter-lang for the 'q' parameter: {filter_lang!r}",
                )

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
                # rustac has no free-text search of its own, so `q` becomes a
                # CQL2 filter over this collection's text columns.
                collection_search_dict.pop("q", None)
                if free_text:
                    columns = self.text_columns(href, request)
                    if not columns:
                        # Nothing searchable here, so nothing can match.
                        continue
                    _apply_free_text(
                        collection_search_dict,
                        free_text,
                        columns,
                        cast(str, filter_lang),
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
