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
    # True when amount_field is a unit price: line amount = quantity x amount_field.
    amount_is_unit_price: bool = False
    # Name for rows that pass `equals` but whose channel value isn't listed in
    # `channels` (e.g. one-off online customers). None drops them.
    other_channel: str | None = None
    # Returns/cancellations to subtract: {"equals": {...}, "match_fields": [...]}.
    # They are valued at the unit price of the original sale row found by
    # match_fields (e.g. reference number + product), since Alpha stores them at 0.
    returns: dict[str, Any] | None = None
    # e.g. 0.16 to also report amounts with IVA when amount_field excludes it.
    iva_rate: float | None = None


def load_sales_config(path: str | os.PathLike | None = None) -> SalesConfig:
    path = Path(path or os.environ.get("ALPHA_SALES_CONFIG") or DEFAULT_CONFIG_PATH)
    if not path.is_file():
        raise RuntimeError(
            f"No existe {path}. Copia config/alpha_ecommerce_sales.example.json a ese "
            "nombre y llena la tabla, los campos y los valores de cada canal."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    # "_comment" / "*_note" keys are documentation for humans, not settings.
    raw = {k: v for k, v in raw.items() if not k.startswith("_") and not k.endswith("_note")}
    return SalesConfig(**raw)


def month_range(month: str) -> tuple[str, str]:
    year, mon = (int(p) for p in month.split("-")[:2])
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _key(value: Any) -> str:
    """Comparable form of a code: numeric DBF fields come back as 2365 or 2365.0
    while the config may say "2365" -- all three must match."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value if value is not None else "").strip().upper()


def ecommerce_sales(
    data: AlphaData,
    cfg: SalesConfig,
    date_from: str,
    date_to: str,
    top_products: int = 20,
) -> dict[str, Any]:
    """One pass over the sales table: gross sales, returns and net per channel,
    per day and per product for the period. Returns are attributed to the
    period and day they were registered in, and to the channel of the sale
    they reverse."""
    info = data._resolve(cfg.table)
    is_sale = data._make_filter(cfg.equals, None, None, None, None)
    is_return = (
        data._make_filter(cfg.returns["equals"], None, None, None, None) if cfg.returns else None
    )
    match_keys = [f.upper() for f in (cfg.returns or {}).get("match_fields", [])]
    channel_of = {_key(v): name for name, values in cfg.channels.items() for v in values}
    chan_key = cfg.channel_field.upper()
    amount_key = cfg.amount_field.upper()
    qty_key = cfg.quantity_field.upper() if cfg.quantity_field else None
    prod_key = cfg.product_field.upper() if cfg.product_field else None
    date_key = cfg.date_field.upper()

    def channel_for(row: dict[str, Any]) -> str | None:
        return channel_of.get(_key(row.get(chan_key)), cfg.other_channel)

    def unit_and_qty(row: dict[str, Any]) -> tuple[float, float]:
        qty = _num(row.get(qty_key)) if qty_key else 0.0
        value = _num(row.get(amount_key))
        return (value, qty) if cfg.amount_is_unit_price else ((value / qty) if qty else 0.0, qty)

    def in_period(row: dict[str, Any]) -> str | None:
        day = row.get(date_key)
        if isinstance(day, str) and len(day) >= 10 and date_from <= day[:10] <= date_to:
            return day[:10]
        return None

    def blank() -> dict[str, float]:
        return {"lines": 0, "quantity": 0.0, "gross": 0.0, "returns": 0.0, "returned_quantity": 0.0}

    channel_names = list(cfg.channels) + ([cfg.other_channel] if cfg.other_channel else [])
    by_channel = {name: blank() for name in channel_names}
    by_day: dict[str, dict[str, float]] = {}
    by_product: dict[str, dict[str, float]] = {}
    total = blank()

    def buckets(channel: str, day: str, product: str) -> list[dict[str, float]]:
        out = [total, by_channel[channel], by_day.setdefault(day, blank())]
        if prod_key:
            out.append(by_product.setdefault(product, blank()))
        return out

    # Unit price and channel of every sale line, any date, to value returns.
    sale_index: dict[tuple, tuple[float, str | None]] = {}
    pending_returns: list[tuple[str, dict[str, Any]]] = []

    for row in data._iter_rows(info):
        if is_sale(row):
            channel = channel_for(row)
            unit, qty = unit_and_qty(row)
            if match_keys:
                sale_index[tuple(_key(row.get(k)) for k in match_keys)] = (unit, channel)
            day = in_period(row)
            if day is None or channel is None:
                continue
            for b in buckets(channel, day, _key(row.get(prod_key)) if prod_key else ""):
                b["lines"] += 1
                b["quantity"] += qty
                b["gross"] += unit * qty
        elif is_return and is_return(row):
            day = in_period(row)
            if day is not None:
                pending_returns.append((day, row))

    unmatched = {"lines": 0, "quantity": 0.0}
    for day, row in pending_returns:
        sale = sale_index.get(tuple(_key(row.get(k)) for k in match_keys))
        qty = abs(_num(row.get(qty_key))) if qty_key else 0.0
        if sale is None:
            unmatched["lines"] += 1
            unmatched["quantity"] += qty
            continue
        unit, channel = sale
        if channel is None:  # reverses a sale outside the ecommerce criteria
            continue
        for b in buckets(channel, day, _key(row.get(prod_key)) if prod_key else ""):
            b["returns"] += unit * qty
            b["returned_quantity"] += qty

    ranked = sorted(by_product.items(), key=lambda kv: kv[1]["gross"] - kv[1]["returns"], reverse=True)
    top = ranked[:top_products]
    descriptions = _product_descriptions(data, cfg, {k for k, _ in top})

    def fmt(b: dict[str, float]) -> dict[str, Any]:
        net = b["gross"] - b["returns"]
        out: dict[str, Any] = {
            "lines": int(b["lines"]),
            "gross": round(b["gross"], 2),
            "returns": round(b["returns"], 2),
            "net": round(net, 2),
        }
        if cfg.iva_rate:
            out["net_with_iva"] = round(net * (1 + cfg.iva_rate), 2)
        if qty_key:
            out["quantity"] = round(b["quantity"], 2)
            out["returned_quantity"] = round(b["returned_quantity"], 2)
        return out

    return {
        "date_from": date_from,
        "date_to": date_to,
        "criteria": {
            "table": info.name,
            "amount": (
                f"{cfg.quantity_field} x {cfg.amount_field}"
                if cfg.amount_is_unit_price else cfg.amount_field
            ),
            "includes_iva": False if cfg.iva_rate else None,
            "channel_field": cfg.channel_field,
            "sale_filters": cfg.equals,
            "return_filters": (cfg.returns or {}).get("equals"),
        },
        "total": fmt(total),
        "by_channel": {name: fmt(b) for name, b in by_channel.items()},
        "by_day": {day: fmt(b) for day, b in sorted(by_day.items())},
        "top_products": [
            {"product": k, "description": descriptions.get(k), **fmt(b)} for k, b in top
        ],
        # Returns in the period whose original sale wasn't found by match_fields.
        "unmatched_returns": {"lines": unmatched["lines"], "quantity": round(unmatched["quantity"], 2)},
    }


def _product_descriptions(data: AlphaData, cfg: SalesConfig, keys: set[str]) -> dict[str, str]:
    lookup = cfg.product_lookup
    if not lookup or not keys:
        return {}
    key_field = lookup["key_field"].upper()
    desc_field = lookup["description_field"].upper()
    found: dict[str, str] = {}
    for row in data._iter_rows(data._resolve(lookup["table"])):
        key = _key(row.get(key_field))
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
    t = report["total"]
    print(f"Ventas ecommerce {date_from} a {date_to} (tabla {report['criteria']['table']}, "
          f"importe = {report['criteria']['amount']}, sin IVA)")
    print(f"\nBruto ${t['gross']:,.2f} - devoluciones ${t['returns']:,.2f} = neto ${t['net']:,.2f}"
          + (f"  (con IVA ${t['net_with_iva']:,.2f})" if "net_with_iva" in t else ""))
    print("\nPor canal (neto sin IVA):")
    for name, b in report["by_channel"].items():
        print(f"  {name:<22} ${b['net']:>14,.2f}  (bruto ${b['gross']:,.2f}, dev. ${b['returns']:,.2f})")
    if report["top_products"]:
        print("\nProductos más vendidos (neto):")
        for p in report["top_products"]:
            label = f"{p['product']} {p['description'] or ''}".strip()
            print(f"  {label[:50]:<50} ${p['net']:>12,.2f}")
    u = report["unmatched_returns"]
    if u["lines"]:
        print(f"\nOjo: {u['lines']} devoluciones del periodo sin venta original encontrada "
              "(no se restaron).")

if __name__ == "__main__":
    main()
