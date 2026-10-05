"""MCP server exposing read-only access to Alpha ERP data (DBF tables).

Run with `alpha-erp-mcp` (stdio transport) on a machine that can see the
folder in ALPHA_DATA_DIR -- normally the server you reach by Remote Desktop.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import load_settings
from .dbf import AlphaData
from .sales import ecommerce_sales, load_sales_config, month_range

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
    date_field: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Any:
    """Read rows from a table. `fields` limits the columns returned; `equals`
    filters by exact value per field; `contains` filters by case-insensitive
    substring per field; `date_field` + `date_from`/`date_to` (YYYY-MM-DD,
    inclusive) filters by a date (type D) field. Paginate with limit/offset
    while has_more is true. Read-only: Alpha ERP data is never modified."""
    return get_data().query(
        table, fields=fields, equals=equals, contains=contains, limit=limit, offset=offset,
        date_field=date_field, date_from=date_from, date_to=date_to,
    )


@mcp.tool()
def alpha_aggregate(
    table: str,
    group_by: list[str] | None = None,
    sum_fields: list[str] | None = None,
    equals: dict[str, Any] | None = None,
    contains: dict[str, str] | None = None,
    date_field: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    max_groups: int = 200,
) -> Any:
    """Totals for a whole table without paging through it: row count and sums
    of `sum_fields` per combination of `group_by` fields, after the same
    filters as alpha_query_table. Use it for reports (sales of a month by
    customer/channel/product). With only group_by, it lists the distinct
    values of those fields and how often each appears -- useful to find out
    which code identifies e.g. the ecommerce channel before filtering on it."""
    return get_data().aggregate(
        table, group_by=group_by, sum_fields=sum_fields, equals=equals, contains=contains,
        date_field=date_field, date_from=date_from, date_to=date_to, max_groups=max_groups,
    )


@mcp.tool()
def alpha_search(text: str, table_contains: str | None = None, limit: int = 20) -> Any:
    """Search text (a customer name, SKU, RFC, invoice folio...) across the
    character fields of every table, or only tables whose name includes
    `table_contains`. Slow on large data folders: narrow it when you can."""
    return get_data().search(text, table_contains=table_contains, limit=limit)


@mcp.tool()
def alpha_ecommerce_sales(
    month: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_products: int = 20,
) -> Any:
    """Fixed ecommerce sales report: totals per channel (Mercado Libre, Amazon,
    Walmart...), per day and top products, for a month ('2026-09') or a
    date_from/date_to range (YYYY-MM-DD). Table, fields and which values
    identify each channel come from config/alpha_ecommerce_sales.json, so
    every period uses the same criteria -- prefer this over ad-hoc
    alpha_aggregate calls for ecommerce sales."""
    if month:
        date_from, date_to = month_range(month)
    if not date_from or not date_to:
        raise ValueError("Pass month='YYYY-MM' or both date_from and date_to.")
    return ecommerce_sales(get_data(), load_sales_config(), date_from, date_to, top_products)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
