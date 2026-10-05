"""Fixed ecommerce sales report over Alpha ERP.

Which table/fields hold sales and how the ecommerce channels are told apart
is specific to each Alpha installation, so it lives in a JSON file
(config/alpha_ecommerce_sales.json, or ALPHA_SALES_CONFIG) rather than in
code; see config/alpha_ecommerce_sales.example.json. With that fixed, every
month is computed with the same criteria instead of being re-guessed.

CLI: `alpha-erp-sales 2026-09` (or `alpha-erp-sales 2026-09-01 2026-09-15`).
"""

from __future__ import annotations

import calendar
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_settings
from .dbf import AlphaData

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "alpha_ecommerce_sales.json"


@dataclass
class SalesConfig:
    table: str
    date_field: str
    amount_field: str
    channel_field: str
    # Channel name -> values of channel_field that belong to it (e.g. customer keys).
    channels: dict[str, list[str]]
    quantity_field: str | None = None
    product_field: str | None = None
    # Extra exact-match filters, e.g. {"TIPO": "V"} to keep only sale movements.
    equals: dict[str, Any] = field(default_factory=dict)
    # Optional lookup for product descriptions: {"table", "key_field", "description_field"}.
    product_lookup: dict[str, str] | None = None


def load_sales_config(path: str | os.PathLike | None = None) -> SalesConfig:
    path = Path(path or os.environ.get("ALPHA_SALES_CONFIG") or DEFAULT_CONFIG_PATH)
    if not path.is_file():
        raise RuntimeError(
            f"No existe {path}. Copia config/alpha_ecommerce_sales.example.json a ese "
            "nombre y llena la tabla, los campos y los valores de cada canal."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return SalesConfig(**raw)


def month_range(month: str) -> tuple[str, str]:
    year, mon = (int(p) for p in month.split("-")[:2])
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def ecommerce_sales(
    data: AlphaData,
    cfg: SalesConfig,
    date_from: str,
    date_to: str,
    top_products: int = 20,
) -> dict[str, Any]:
    """One pass over the sales table: totals per channel, per day and the top
    products by amount, for rows whose channel_field is in some channel."""
    info = data._resolve(cfg.table)
    matches = data._make_filter(cfg.equals, None, cfg.date_field, date_from, date_to)
    channel_of = {
        str(v).strip().upper(): name for name, values in cfg.channels.items() for v in values
    }
    chan_key = cfg.channel_field.upper()
    amount_key = cfg.amount_field.upper()
    qty_key = cfg.quantity_field.upper() if cfg.quantity_field else None
    prod_key = cfg.product_field.upper() if cfg.product_field else None
    date_key = cfg.date_field.upper()

    def blank() -> dict[str, float]:
        return {"lines": 0, "amount": 0.0, "quantity": 0.0}

    by_channel = {name: blank() for name in cfg.channels}
    by_day: dict[str, dict[str, float]] = {}
    by_product: dict[str, dict[str, float]] = {}
    total = blank()

    for row in data._iter_rows(info):
        if not matches(row):
            continue
        channel = channel_of.get(str(row.get(chan_key, "")).strip().upper())
        if channel is None:
            continue
        amount = _num(row.get(amount_key))
        qty = _num(row.get(qty_key)) if qty_key else 0.0
        buckets = [total, by_channel[channel], by_day.setdefault(row[date_key][:10], blank())]
        if prod_key:
            buckets.append(by_product.setdefault(str(row.get(prod_key, "")).strip(), blank()))
        for b in buckets:
            b["lines"] += 1
            b["amount"] += amount
            b["quantity"] += qty

    top = sorted(by_product.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:top_products]
    descriptions = _product_descriptions(data, cfg, {k for k, _ in top})

    def fmt(b: dict[str, float]) -> dict[str, Any]:
        out: dict[str, Any] = {"lines": int(b["lines"]), "amount": round(b["amount"], 2)}
        if qty_key:
            out["quantity"] = round(b["quantity"], 2)
        return out

    return {
        "date_from": date_from,
        "date_to": date_to,
        "criteria": {
            "table": info.name,
            "amount_field": cfg.amount_field,
            "channel_field": cfg.channel_field,
            "extra_filters": cfg.equals,
        },
        "total": fmt(total),
        "by_channel": {name: fmt(b) for name, b in by_channel.items()},
        "by_day": {day: fmt(b) for day, b in sorted(by_day.items())},
        "top_products": [
            {"product": k, "description": descriptions.get(k), **fmt(b)} for k, b in top
        ],
    }


def _product_descriptions(data: AlphaData, cfg: SalesConfig, keys: set[str]) -> dict[str, str]:
    lookup = cfg.product_lookup
    if not lookup or not keys:
        return {}
    key_field = lookup["key_field"].upper()
    desc_field = lookup["description_field"].upper()
    found: dict[str, str] = {}
    for row in data._iter_rows(data._resolve(lookup["table"])):
        key = str(row.get(key_field, "")).strip()
        if key in keys:
            found[key] = str(row.get(desc_field, "")).strip()
            if len(found) == len(keys):
                break
    return found


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Uso: alpha-erp-sales 2026-09   |   alpha-erp-sales 2026-09-01 2026-09-15")
        sys.exit(1)
    date_from, date_to = month_range(args[0]) if len(args) == 1 else (args[0], args[1])

    report = ecommerce_sales(AlphaData(load_settings()), load_sales_config(), date_from, date_to)
    print(f"Ventas ecommerce {date_from} a {date_to} (tabla {report['criteria']['table']})")
    print(f"\nTotal: ${report['total']['amount']:,.2f} en {report['total']['lines']} renglones")
    print("\nPor canal:")
    for name, b in report["by_channel"].items():
        print(f"  {name:<20} ${b['amount']:>14,.2f}  ({b['lines']} renglones)")
    if report["top_products"]:
        print("\nProductos más vendidos:")
        for p in report["top_products"]:
            label = f"{p['product']} {p['description'] or ''}".strip()
            print(f"  {label[:50]:<50} ${p['amount']:>12,.2f}")


if __name__ == "__main__":
    main()
