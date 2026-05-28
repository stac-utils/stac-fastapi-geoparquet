"""
Filter extension client for stac-fastapi-geoparquet.
Implements ``/queryables`` and ``/collections/{collection_id}/queryables``
"""

from __future__ import annotations

import os
from typing import Any, cast

from stac_fastapi.extensions.core.filter.client import BaseFiltersClient
from stac_fastapi.types.errors import NotFoundError
from starlette.requests import Request
from rustac import DuckdbClient

# ---------------------------------------------------------------------------
# DuckDB type → JSON-Schema type mapping (AI written)
# ---------------------------------------------------------------------------

_DUCKDB_TO_JSONSCHEMA: dict[str, dict[str, str]] = {
    # strings
    "VARCHAR": {"type": "string"},
    "TEXT": {"type": "string"},
    # integers
    "BIGINT": {"type": "integer"},
    "INTEGER": {"type": "integer"},
    "INT": {"type": "integer"},
    "SMALLINT": {"type": "integer"},
    "TINYINT": {"type": "integer"},
    "HUGEINT": {"type": "integer"},
    "UBIGINT": {"type": "integer"},
    "UINTEGER": {"type": "integer"},
    "USMALLINT": {"type": "integer"},
    "UTINYINT": {"type": "integer"},
    # numbers
    "DOUBLE": {"type": "number"},
    "FLOAT": {"type": "number"},
    "REAL": {"type": "number"},
    "DECIMAL": {"type": "number"},
    "NUMERIC": {"type": "number"},
    # booleans
    "BOOLEAN": {"type": "boolean"},
    "BOOL": {"type": "boolean"},
    # date-time
    "TIMESTAMP WITH TIME ZONE": {
        "type": "string",
        "format": "date-time",
    },
    "TIMESTAMP": {"type": "string", "format": "date-time"},
    "DATE": {"type": "string", "format": "date"},
    # arrays → just mark as array; avoid full recursion for compound types
}


def _duckdb_type_to_jsonschema(duckdb_type: str) -> dict[str, Any]:
    """Convert a DuckDB column type string to a JSON-Schema fragment."""
    upper = duckdb_type.upper().strip()

    # Geometry columns – treat as GeoJSON geometry object
    if upper.startswith("GEOMETRY"):
        return {
            "type": "object",
            "title": "GeoJSON Geometry",
            "format": "geojson-geometry",
        }

    # Array types: e.g. "VARCHAR[]", "BIGINT[]"
    if upper.endswith("[]"):
        item_schema = _duckdb_type_to_jsonschema(upper[:-2])
        return {"type": "array", "items": item_schema}

    # STRUCT – represent as JSON object (too complex to unroll inline)
    if upper.startswith("STRUCT"):
        return {"type": "object"}

    # MAP types
    if upper.startswith("MAP"):
        return {"type": "object"}

    # Exact match
    if upper in _DUCKDB_TO_JSONSCHEMA:
        return dict(_DUCKDB_TO_JSONSCHEMA[upper])

    # Prefix match (e.g. DECIMAL(10,2), NUMERIC(5))
    for prefix, schema in _DUCKDB_TO_JSONSCHEMA.items():
        if upper.startswith(prefix):
            return dict(schema)

    # Fallback
    return {}


# Fields that are standard STAC properties always present
_STAC_CORE_QUERYABLES: dict[str, dict[str, Any]] = {
    "id": {
        "title": "Item ID",
        "description": "Provider identifier for the Item.",
        "type": "string",
    },
    "collection": {
        "title": "Collection",
        "description": "The ID of the collection this Item belongs to.",
        "type": "string",
    },
    "datetime": {
        "title": "Datetime",
        "description": "Datetime associated with this Item.",
        "type": "string",
        "format": "date-time",
    },
    "geometry": {
        "title": "Item Geometry",
        "description": "Spatial extent of this Item.",
        "type": "object",
        "format": "geojson-geometry",
    },
}

# Columns that are internal implementation details, not useful as queryables
_SKIP_COLUMNS: frozenset[str] = frozenset(
    {
        "type",
        "stac_version",
        "stac_extensions",
        "links",
        "assets",
        "providers",
        "bbox",
        "geometry",  # re-added via _STAC_CORE_QUERYABLES with a nicer description
    }
)


def _extract_queryable_properties(
    describe_rows: list[tuple[Any, ...]],
) -> dict[str, dict[str, Any]]:
    """Turn DESCRIBE output rows into a JSON-Schema *properties* dict."""
    properties: dict[str, dict[str, Any]] = {}

    for row in describe_rows:
        col_name: str = row[0]
        col_type: str = row[1]

        if col_name in _SKIP_COLUMNS:
            continue

        schema_fragment = _duckdb_type_to_jsonschema(col_type)
        if col_name == "datetime":
            schema_fragment = _STAC_CORE_QUERYABLES["datetime"].copy()
        elif col_name == "id":
            schema_fragment = _STAC_CORE_QUERYABLES["id"].copy()
        elif col_name == "collection":
            schema_fragment = _STAC_CORE_QUERYABLES["collection"].copy()
        else:
            # Use title-cased column name as a human-readable title
            schema_fragment["title"] = col_name.replace(":", ": ").replace("_", " ").title()

        properties[col_name] = schema_fragment

    return properties


class FiltersClient(BaseFiltersClient):
    """filter client using rustac

    When `collection_id` is *None* (i.e. the global ``/queryables``
    endpoint) returns static common queryables
    """

    def get_queryables(
        self,
        collection_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request: Request = kwargs["request"]

        hrefs: dict[str, str] = request.state.hrefs
        collections: dict[str, Any] = request.state.collections

        base_url = str(request.base_url).rstrip("/")

        if collection_id is not None:
            # Single-collection queryables
            if collection_id not in collections:
                raise NotFoundError(f"Collection does not exist: {collection_id}")

            href = hrefs.get(collection_id)
            if href is None:
                # Collection exists but has no geoparquet backing – return core only
                return _build_queryables_doc(
                    collection_id=collection_id,
                    base_url=base_url,
                    properties=dict(_STAC_CORE_QUERYABLES),
                )

            properties = self._properties_for_href(href, request)
            return _build_queryables_doc(
                collection_id=collection_id,
                base_url=base_url,
                properties=properties,
            )

        else:
            # Global /queryables – return only the STAC core properties. The Filter
            # spec explicitly allows this; clients that need collection-specific
            # properties should query /collections/{id}/queryables instead.
            return _build_queryables_doc(
                collection_id=None,
                base_url=base_url,
                properties=dict(_STAC_CORE_QUERYABLES),
            )

    def _properties_for_href(
        self,
        href: str,
        request: Request,
    ) -> dict[str, dict[str, Any]]:
        """Run DESCRIBE on the parquet file and return queryable properties."""
        client = cast(DuckdbClient, request.state.client)
        try:
            safe_href = href.replace("'", "''")
            arrow_table = client.query_to_table(
                f"DESCRIBE SELECT * FROM read_parquet('{safe_href}') LIMIT 0"
            )

            # Convert these columns from the Arrow Table to Python lists.
            column_names = arrow_table.column("column_name").to_pylist()
            column_types = arrow_table.column("column_type").to_pylist()

            # Zip the lists together
            rows = list(zip(column_names, column_types))

            return _extract_queryable_properties(rows)
        except Exception:
                # If schema introspection fails fall back to the STAC core queryables
                return dict(_STAC_CORE_QUERYABLES)

def _build_queryables_doc(
    *,
    collection_id: str | None,
    base_url: str,
    properties: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a conformant OGC-API / STAC-API Queryables JSON-Schema document."""
    if collection_id is not None:
        doc_id = f"{base_url}/collections/{collection_id}/queryables"
        title = f"Queryables for {collection_id}"
        description = (
            f"Filterable properties available for items in collection '{collection_id}'."
        )
    else:
        doc_id = f"{base_url}/queryables"
        title = "Global Queryables"
        description = (
            "Filterable properties available across all collections in this STAC API."
        )

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": doc_id,
        "type": "object",
        "title": title,
        "description": description,
        "properties": properties,
    }
