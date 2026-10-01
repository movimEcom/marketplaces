"""MCP server exposing read-only access to Alpha ERP data (DBF tables).

Run with `alpha-erp-mcp` (stdio transport) on a machine that can see the
folder in ALPHA_DATA_DIR -- normally the server you reach by Remote Desktop.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import load_settings
from .dbf import AlphaData

mcp = MCPServer("alpha-erp")

_data: AlphaData | None = None


def get_data() -> AlphaData:
    global _data
    if _data is None:
        _data = AlphaData(load_settings())
    return _data


@mcp.tool()
def alpha_list_tables(name_contains: str | None = None, refresh: bool = False) -> Any:
    """List the Alpha ERP tables (.DBF files) under ALPHA_DATA_DIR with size and
    last-modified time. Names are relative paths without extension, e.g.
    'empresa01/clientes'. Use refresh=true after new files appear."""
    data = get_data()
    if refresh:
        data.tables(refresh=True)
    return data.list_tables(name_contains)


@mcp.tool()
def alpha_describe_table(table: str) -> Any:
    """Show a table's fields (name, xBase type C/N/D/L/M..., length, decimals)
    and record count. Call this before querying a table you haven't seen."""
    return get_data().describe_table(table)


@mcp.tool()
def alpha_query_table(
    table: str,
    fields: list[str] | None = None,
    equals: dict[str, Any] | None = None,
    contains: dict[str, str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Read rows from a table. `fields` limits the columns returned; `equals`
    filters by exact value per field; `contains` filters by case-insensitive
    substring per field. Paginate with limit/offset while has_more is true.
    Read-only: Alpha ERP data is never modified."""
    return get_data().query(table, fields=fields, equals=equals, contains=contains, limit=limit, offset=offset)


@mcp.tool()
def alpha_search(text: str, table_contains: str | None = None, limit: int = 20) -> Any:
    """Search text (a customer name, SKU, RFC, invoice folio...) across the
    character fields of every table, or only tables whose name includes
    `table_contains`. Slow on large data folders: narrow it when you can."""
    return get_data().search(text, table_contains=table_contains, limit=limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
