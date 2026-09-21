"""MCP server exposing Mercado Libre Mexico (site MLM) seller operations.

Run with `meli-mx-mcp` (stdio transport) after completing `meli-mx-authorize`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import MeliClient
from .config import load_settings
from .quality import BANDS, fetch_quality_report, render_html, save_report

mcp = MCPServer("mercadolibre-mexico")

_client: MeliClient | None = None


def get_client() -> MeliClient:
    global _client
    if _client is None:
        _client = MeliClient(load_settings())
    return _client


# -- catalog / public search -------------------------------------------------


@mcp.tool()
def meli_search_products(query: str, limit: int = 20, offset: int = 0) -> Any:
    """Search public product listings on Mercado Libre Mexico by keyword."""
    return get_client().search_products(query, limit=limit, offset=offset)


@mcp.tool()
def meli_get_item(item_id: str) -> Any:
    """Get the full public details of a listing by its item id (e.g. MLM123456789)."""
    return get_client().get_item(item_id)


@mcp.tool()
def meli_get_categories() -> Any:
    """List the top-level categories for the Mexico site (MLM)."""
    return get_client().get_categories()


@mcp.tool()
def meli_get_category_attributes(category_id: str) -> Any:
    """Get the required/optional attributes for a category (needed to create listings)."""
    return get_client().get_category_attributes(category_id)


# -- seller: listings ---------------------------------------------------------


@mcp.tool()
def meli_list_my_items(status: str | None = None, limit: int = 50, offset: int = 0) -> Any:
    """List the authenticated seller's own listings. status: active|paused|closed|under_review."""
    return get_client().list_my_items(status=status, limit=limit, offset=offset)


@mcp.tool()
def meli_create_item(item: dict[str, Any]) -> Any:
    """Create a new listing. `item` must follow the Mercado Libre item schema
    (title, category_id, price, currency_id, available_quantity, buying_mode,
    condition, listing_type_id, pictures, attributes, etc.)."""
    return get_client().create_item(item)


@mcp.tool()
def meli_update_item(item_id: str, changes: dict[str, Any]) -> Any:
    """Update arbitrary fields of an existing listing owned by the seller."""
    return get_client().update_item(item_id, changes)


@mcp.tool()
def meli_update_price(item_id: str, price: float) -> Any:
    """Update the price of one of the seller's listings."""
    return get_client().update_price(item_id, price)


@mcp.tool()
def meli_update_stock(item_id: str, quantity: int) -> Any:
    """Update the available stock quantity of one of the seller's listings."""
    return get_client().update_stock(item_id, quantity)


@mcp.tool()
def meli_set_item_status(item_id: str, status: str) -> Any:
    """Change a listing's status: 'active', 'paused' or 'closed'."""
    return get_client().set_item_status(item_id, status)


# -- seller: orders ------------------------------------------------------------


@mcp.tool()
def meli_list_orders(status: str | None = None, limit: int = 50, offset: int = 0) -> Any:
    """List the seller's orders, optionally filtered by status
    (e.g. paid, confirmed, cancelled)."""
    return get_client().list_orders(status=status, limit=limit, offset=offset)


@mcp.tool()
def meli_get_order(order_id: str) -> Any:
    """Get full details of a single order by id."""
    return get_client().get_order(order_id)


# -- seller: questions & messaging ---------------------------------------------


@mcp.tool()
def meli_list_questions(status: str = "UNANSWERED", limit: int = 50) -> Any:
    """List buyer questions on the seller's listings. status: UNANSWERED|ANSWERED|..."""
    return get_client().list_questions(status=status, limit=limit)


@mcp.tool()
def meli_answer_question(question_id: int, text: str) -> Any:
    """Answer a buyer's question by id."""
    return get_client().answer_question(question_id, text)


@mcp.tool()
def meli_list_order_messages(order_id: str) -> Any:
    """List post-sale messages exchanged with the buyer for a given order."""
    return get_client().list_order_messages(order_id)


@mcp.tool()
def meli_send_order_message(order_id: str, text: str) -> Any:
    """Send a post-sale message to the buyer of a given order."""
    return get_client().send_order_message(order_id, text)


# -- seller: reputation ---------------------------------------------------------


@mcp.tool()
def meli_get_my_reputation() -> Any:
    """Get the authenticated seller's reputation (level, ratings, claims, etc.)."""
    return get_client().get_my_reputation()


@mcp.tool()
def meli_get_user(user_id: int | None = None) -> Any:
    """Get public profile info for a user (defaults to the authenticated seller)."""
    return get_client().get_user(user_id)


# -- seller: listing quality ----------------------------------------------------


@mcp.tool()
def meli_get_listing_quality_summary(status: str | None = "active") -> Any:
    """Summarize the seller's listing quality (Mercado Libre's `/item/{id}/performance`
    score, the same number and pending objectives shown in the seller center)
    bucketed into Excelente (80-100) / Bueno (60-79) / Mejorable (40-59) /
    Crítico (0-39), plus the worst-scoring listings and what's blocking them.
    status: active|paused|closed|None (all). Makes one API call per listing,
    so it can take a while for sellers with many listings."""
    report = fetch_quality_report(get_client(), status=status)
    worst = [i for i in report.items if i.band in ("mejorable", "critico")][:50]
    return {
        "total": report.total,
        "skipped": report.skipped,
        "skip_reasons": report.skip_reasons,
        "band_counts": report.band_counts,
        "band_labels": {key: label for key, _low, _high, label, _color in BANDS},
        "generated_at": report.generated_at,
        "worst_items": [
            {
                "id": i.id,
                "title": i.title,
                "score": i.score,
                "level": i.level,
                "band": i.band,
                "pending_objectives": i.pending_objectives,
                "permalink": i.permalink,
            }
            for i in worst
        ],
    }


@mcp.tool()
def meli_generate_quality_dashboard(
    output_path: str = "quality_report.html", status: str | None = "active"
) -> str:
    """Generate an HTML dashboard of listing quality (like
    meli_get_listing_quality_summary, but rendered as a styled page) and save
    it to `output_path` on disk. Returns the absolute path written."""
    report = fetch_quality_report(get_client(), status=status)
    html = render_html(report, seller_label=f"vendedor {get_client().user_id}")
    path = save_report(html, output_path)
    return str(path.resolve())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
