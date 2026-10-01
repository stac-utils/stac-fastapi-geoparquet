"""Reading a geoparquet file's column schema."""

from typing import Any, cast

from rustac import DuckdbClient

# Columns that are structural STAC members rather than searchable metadata.
STRUCTURAL_COLUMNS: frozenset[str] = frozenset(
    {
        "type",
        "stac_version",
        "stac_extensions",
        "links",
        "assets",
        "providers",
        "bbox",
        "geometry",
    }
)

_TEXT_TYPES: frozenset[str] = frozenset({"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR"})


def describe_columns(client: DuckdbClient, href: str) -> list[tuple[str, str]]:
    """Return ``(name, duckdb type)`` for every column in a geoparquet file.

    Raises whatever DuckDB raises if the file can't be read; callers decide
    whether an unreadable file is fatal.
    """
    safe_href = href.replace("'", "''")
    table = client.query_to_table(
        f"DESCRIBE SELECT * FROM read_parquet('{safe_href}') LIMIT 0"
    )
    names = cast(list[str], table.column("column_name").to_pylist())
    types = cast(list[str], table.column("column_type").to_pylist())
    return list(zip(names, types))


def text_columns(columns: list[tuple[str, Any]]) -> list[str]:
    """Pick the plain-text columns worth searching from a DESCRIBE result.

    Structural members are dropped: matching every item because its `type` is
    "Feature" is not what a free-text search means.
    """
    return [
        name
        for name, column_type in columns
        if name not in STRUCTURAL_COLUMNS
        and str(column_type).upper().strip() in _TEXT_TYPES
    ]
