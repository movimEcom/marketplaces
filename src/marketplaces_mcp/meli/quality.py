"""Listing quality report: one Excelente/Bueno/Mejorable/Crítico
classification across every listing, regular or catalog-linked.

Mercado Libre exposes quality differently per listing kind, so getting them
onto the same 4-band scale takes two different real signals:

- "Regular" listings (no `user_product_id`): `/item/{id}/performance`
  returns a 0-100 score + pending objectives -- the same number shown in the
  seller center's own quality widget. Verified against two real listings
  (69 and 73) before trusting it; the older `health` item field is a
  different, deprecated metric that does NOT match it (it returned 1 for
  both). Mercado Libre only computes this for `status="active"` listings
  ("Only status active is supported").
- Catalog-linked listings (`user_product_id` set): `/item/{id}/performance`
  explicitly 400s ("Product items are not supported"), and both
  `/products/{catalog_product_id}/performance` and
  `/user-products/{id}/performance` 500 -- Mercado Libre does not expose a
  0-100 score for these at all. The only public signal is `quality_type` on
  `/products/{catalog_product_id}` (e.g. "COMPLETE"), a coarse category
  shared by every seller listing that same catalog product. To still place
  these on the same 0-100 scale (at the user's request), we compute our own
  ESTIMATED score from the user_product's ficha completeness: % of
  attributes filled + how many pictures it has. This is our own heuristic,
  not a Mercado Libre number -- every such item is flagged
  `is_estimated=True` and labeled "(estimado)" wherever it's shown, and its
  `quality_type` floors the estimate at 80 when Mercado Libre itself already
  calls the ficha COMPLETE.

Listings with no resolvable signal at all (mostly non-active regular
listings, since Mercado Libre doesn't compute quality for those) are placed
in Crítico with no `score` -- per explicit user instruction, "no data" is
treated as the worst case rather than left unclassified. `skip_reasons` /
`skip_samples` still record why, for diagnostics.
"""

from __future__ import annotations

import html as html_escape
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .client import MeliClient

# (key, low, high, label, status color) -- colors are the dataviz skill's
# fixed status palette (good/warning/serious/critical), never reused as
# categorical hues, always paired with an icon + text label rather than
# carrying meaning through color alone.
BANDS = (
    ("excelente", 80, 100, "Excelente", "#0ca30c"),
    ("bueno", 60, 79, "Bueno", "#fab219"),
    ("mejorable", 40, 59, "Mejorable", "#ec835a"),
    ("critico", 0, 39, "Crítico", "#d03b3b"),
)

_ITEM_ATTRIBUTES = ["id", "title", "status", "permalink", "user_product_id", "category_id", "attributes"]


def _extract_attribute_value(attributes: list[dict], attr_id: str) -> str:
    for attr in attributes or []:
        if attr.get("id") == attr_id:
            return attr.get("value_name") or ""
    return ""


def _band_for_score(score: int) -> str:
    for key, low, high, _label, _color in BANDS:
        if low <= score <= high:
            return key
    return "critico"


@dataclass
class QualityItem:
    id: str
    title: str
    status: str
    permalink: str
    band: str
    kind: str  # "regular" | "catalog"
    score: int | None = None
    is_estimated: bool = False  # True for catalog items -- score is our own heuristic
    level: str = ""
    pending_objectives: list[str] = field(default_factory=list)
    category_name: str = ""
    brand: str = ""

    @property
    def sort_score(self) -> int:
        return self.score if self.score is not None else -1

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Clase (categoría), marca y artículo (título) -- para agrupar las
        publicaciones de una misma banda por lo que pidió el usuario."""
        article = self.title or self.id
        return (
            (self.category_name or "￿").lower(),
            (self.brand or "￿").lower(),
            article.lower(),
        )


def _estimate_catalog_score(attributes: list[dict], pictures: list, quality_type: str | None) -> int:
    """Our own 0-100 ficha-completeness heuristic for catalog-linked
    listings, since Mercado Libre exposes no numeric score for them: 70%
    weight on the share of attributes that have a real value (Mercado Libre
    returns unset ones as a placeholder like {"id": "-1", "name": None}),
    30% weight on picture count (capped at 5, a common "enough photos"
    baseline). Floored at 80 when Mercado Libre's own `quality_type` already
    says the ficha is COMPLETE, so the estimate doesn't contradict the one
    real signal we do have.
    """
    total = len(attributes)
    filled = sum(
        1
        for attr in attributes
        if any(v.get("name") not in (None, "") or v.get("id") not in (None, "-1") for v in attr.get("values", []))
    )
    attribute_ratio = (filled / total) if total else 0.0
    picture_ratio = min(len(pictures) / 5, 1.0)

    score = round(100 * (0.7 * attribute_ratio + 0.3 * picture_ratio))
    if quality_type == "COMPLETE":
        score = max(score, 80)
    return max(0, min(100, score))


@dataclass
class QualityReport:
    generated_at: str
    total: int
    skipped: int
    band_counts: dict[str, int]
    items: list[QualityItem] = field(default_factory=list)
    skip_reasons: dict[int, int] = field(default_factory=dict)  # http_status -> count
    skip_samples: dict[int, str] = field(default_factory=dict)  # http_status -> example body

    def items_in_band(self, band: str) -> list[QualityItem]:
        return [item for item in self.items if item.band == band]


def _pending_objectives(performance: dict) -> list[str]:
    return [
        bucket.get("title", bucket.get("key", ""))
        for bucket in performance.get("buckets", [])
        if bucket.get("status") != "COMPLETED"
    ]


def fetch_quality_report(
    client: MeliClient,
    status: str | None = "active",
    on_progress=None,
    item_ids: list[str] | None = None,
) -> QualityReport:
    if item_ids is None:
        item_ids = client.list_all_my_item_ids(status=status)
    metadata_by_id = {
        raw["id"]: raw for raw in client.multiget_items(item_ids, attributes=_ITEM_ATTRIBUTES)
    }
    category_names = client.get_category_names(
        [meta.get("category_id", "") for meta in metadata_by_id.values()]
    )

    def _category_and_brand(meta: dict) -> tuple[str, str]:
        category_id = meta.get("category_id", "")
        category_name = category_names.get(category_id, category_id)
        brand = _extract_attribute_value(meta.get("attributes", []), "BRAND")
        return category_name, brand

    # Catalog-linked listings (`user_product_id` set) don't have a numeric
    # /performance score at all -- skip calling it for them and resolve their
    # coarse `quality_type` instead, rather than burning API calls on 400s.
    regular_ids = [i for i in item_ids if not metadata_by_id.get(i, {}).get("user_product_id")]
    catalog_ids = [i for i in item_ids if metadata_by_id.get(i, {}).get("user_product_id")]

    performance_by_id, errors_by_id, skip_samples = client.get_items_performance(
        regular_ids, on_progress=on_progress
    )
    skip_reasons: dict[int, int] = {}
    for status_code in errors_by_id.values():
        skip_reasons[status_code] = skip_reasons.get(status_code, 0) + 1

    items: list[QualityItem] = []
    band_counts = {key: 0 for key, *_ in BANDS}

    for item_id, perf in performance_by_id.items():
        meta = metadata_by_id.get(item_id, {})
        category_name, brand = _category_and_brand(meta)
        score = round(perf.get("score", 0))
        band = _band_for_score(score)
        band_counts[band] += 1
        items.append(
            QualityItem(
                id=item_id,
                title=meta.get("title", ""),
                status=meta.get("status", ""),
                permalink=meta.get("permalink", ""),
                band=band,
                kind="regular",
                score=score,
                level=perf.get("level", ""),
                pending_objectives=_pending_objectives(perf),
                category_name=category_name,
                brand=brand,
            )
        )

    # Regular listings Mercado Libre refused to score at all (mostly
    # non-active ones) go straight to Crítico -- no data to evaluate them by
    # is treated as the worst case, per explicit instruction, rather than
    # left unclassified.
    for item_id in errors_by_id:
        meta = metadata_by_id.get(item_id, {})
        category_name, brand = _category_and_brand(meta)
        band_counts["critico"] += 1
        items.append(
            QualityItem(
                id=item_id,
                title=meta.get("title", ""),
                status=meta.get("status", ""),
                permalink=meta.get("permalink", ""),
                band="critico",
                kind="regular",
                pending_objectives=["Sin datos de calidad (Mercado Libre no la calculó)"],
                category_name=category_name,
                brand=brand,
            )
        )

    catalog_user_product_ids = [metadata_by_id[i]["user_product_id"] for i in catalog_ids]
    catalog_quality = client.get_catalog_quality_by_user_product(catalog_user_product_ids)

    for item_id in catalog_ids:
        meta = metadata_by_id[item_id]
        resolved = catalog_quality.get(meta["user_product_id"])
        category_name, brand = _category_and_brand(meta)
        if resolved is None:
            # No signal at all -- also straight to Crítico, same as above.
            band_counts["critico"] += 1
            items.append(
                QualityItem(
                    id=item_id,
                    title=meta.get("title", ""),
                    status=meta.get("status", ""),
                    permalink=meta.get("permalink", ""),
                    band="critico",
                    kind="catalog",
                    pending_objectives=["Sin datos suficientes para evaluar la ficha"],
                    category_name=category_name,
                    brand=brand,
                )
            )
            continue
        quality_type = resolved.get("quality_type")
        score = _estimate_catalog_score(resolved.get("attributes", []), resolved.get("pictures", []), quality_type)
        band = _band_for_score(score)
        band_counts[band] += 1
        objectives = [] if band == "excelente" else ["Completa la ficha del producto (más atributos y fotos)"]
        if quality_type and quality_type != "COMPLETE":
            objectives.append(f"Mercado Libre marca la ficha como: {quality_type}")
        items.append(
            QualityItem(
                id=item_id,
                title=meta.get("title", ""),
                status=meta.get("status", ""),
                permalink=meta.get("permalink", ""),
                band=band,
                kind="catalog",
                score=score,
                is_estimated=True,
                pending_objectives=objectives,
                category_name=category_name,
                brand=brand,
            )
        )

    items.sort(key=lambda item: item.sort_score)

    return QualityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total=len(items),
        skipped=len(item_ids) - len(items),
        band_counts=band_counts,
        items=items,
        skip_reasons=skip_reasons,
        skip_samples=skip_samples,
    )


def _esc(text: str) -> str:
    return html_escape.escape(text, quote=True)


def _stat_tile(key: str, low: int, high: int, label: str, color: str, count: int, is_default: bool) -> str:
    range_label = f"{low}–100" if high == 100 else f"{low}–{high}" if key != "critico" else f"< {high + 1}"
    active_class = " tile-active" if is_default else ""
    return f"""
    <button type="button" class="tile{active_class}" data-band="{key}" data-label="{_esc(label)}"
            onclick="showBand(this)" aria-pressed="{"true" if is_default else "false"}">
      <span class="tile-icon" style="background:{color}" aria-hidden="true"></span>
      <div class="tile-value">{count}</div>
      <div class="tile-label">{_esc(label)}</div>
      <div class="tile-range">{range_label}</div>
    </button>"""


def _item_row(item: QualityItem, color_by_band: dict[str, str]) -> str:
    color = color_by_band[item.band]
    title = _esc(item.title) or item.id
    link = _esc(item.permalink) if item.permalink else "#"
    objectives = ", ".join(item.pending_objectives) or "—"
    score_display = str(item.score) if item.score is not None else "—"
    if item.is_estimated:
        score_display += " (estimado)"
    kind_label = "Regular" if item.kind == "regular" else "Catálogo"
    category = _esc(item.category_name) or "—"
    brand = _esc(item.brand) or "—"
    return f"""
        <tr>
          <td class="num">{score_display}</td>
          <td><span class="dot" style="background:{color}"></span>{item.band.capitalize()}</td>
          <td>{kind_label}</td>
          <td>{category}</td>
          <td>{brand}</td>
          <td><a href="{link}" target="_blank" rel="noopener">{title}</a></td>
          <td>{_esc(objectives)}</td>
        </tr>"""


def render_html(report: QualityReport, seller_label: str = "") -> str:
    color_by_band = {key: color for key, *_rest, color in BANDS}
    label_by_band = {key: label for key, *_rest1, label, _color in BANDS}

    # Preselect whichever band most needs attention, so the dashboard opens
    # already showing something useful instead of an empty table.
    default_band = next(
        (key for key in ("critico", "mejorable", "bueno", "excelente") if report.band_counts.get(key, 0)),
        BANDS[0][0],
    )

    tiles = "".join(
        _stat_tile(key, low, high, label, color, report.band_counts.get(key, 0), key == default_band)
        for key, low, high, label, color in BANDS
    )

    band_tables = []
    for key, _low, _high, _label, _color in BANDS:
        band_items = sorted(report.items_in_band(key), key=lambda item: item.sort_key)
        display_style = "" if key == default_band else " style=\"display:none\""
        if band_items:
            rows = "".join(_item_row(item, color_by_band) for item in band_items)
            body = f"""
        <table class="band-table" data-band="{key}"{display_style}>
          <thead>
            <tr><th>Score</th><th>Banda</th><th>Tipo</th><th>Categoría</th><th>Marca</th><th>Artículo</th><th>Objetivos pendientes</th></tr>
          </thead>
          <tbody>{rows}
          </tbody>
        </table>"""
        else:
            body = (
                f'<p class="band-table muted" data-band="{key}"{display_style}>'
                f"No hay publicaciones en banda {_esc(_label)}. \U0001F389</p>"
            )
        band_tables.append(body)
    tables_html = "".join(band_tables)

    table_note = (
        "Ordenadas por clase (categoría), marca y artículo. Los scores marcados "
        "\"(estimado)\" son de publicaciones de catálogo: Mercado Libre no expone un "
        "score 0-100 para esas (la ficha es compartida entre vendedores), así que lo "
        "calculamos nosotros a partir de qué tan completa está la ficha -- no es el "
        "dato oficial de Mercado Libre."
    )

    generated_local = report.generated_at.replace("T", " ").split(".")[0] + " UTC"
    skipped_note = f" · {report.skipped} sin datos de calidad" if report.skipped else ""
    subtitle = f"Mercado Libre México{' · ' + _esc(seller_label) if seller_label else ''}"
    default_label = _esc(label_by_band[default_band])

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Calidad de publicaciones</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --surface: #fcfcfb;
    --page: #f9f9f7;
    --ink: #0b0b0b;
    --ink-secondary: #52514e;
    --ink-muted: #898781;
    --border: #e6e5e1;
    --accent: #FFE600;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface: #1a1a19;
      --page: #0d0d0d;
      --ink: #ffffff;
      --ink-secondary: #c3c2b7;
      --ink-muted: #898781;
      --border: #33322e;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page);
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  }}
  .header {{
    background: var(--accent);
    color: #1a1a19;
    padding: 20px 24px;
  }}
  .header h1 {{
    margin: 0 0 4px;
    font-size: 20px;
  }}
  .header p {{
    margin: 0;
    font-size: 13px;
    opacity: 0.75;
  }}
  .content {{
    max-width: 1080px;
    margin: 0 auto;
    padding: 24px 16px 48px;
  }}
  .tiles {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
    margin-bottom: 28px;
  }}
  .tile {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    position: relative;
    display: block;
    width: 100%;
    text-align: left;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }}
  .tile:hover {{
    border-color: var(--ink-muted);
  }}
  .tile:focus-visible {{
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }}
  .tile-active {{
    border-color: var(--ink);
    border-width: 2px;
  }}
  .tile-icon {{
    display: inline-block;
    width: 14px;
    height: 14px;
    border-radius: 50%;
  }}
  .tile-value {{
    font-size: 32px;
    font-weight: 600;
    font-variant-numeric: proportional-nums;
    margin-top: 8px;
  }}
  .tile-label {{
    font-size: 14px;
    color: var(--ink-secondary);
  }}
  .tile-range {{
    font-size: 12px;
    color: var(--ink-muted);
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    font-size: 14px;
  }}
  th, td {{
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }}
  th {{
    color: var(--ink-muted);
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
  }}
  td.num {{
    font-variant-numeric: tabular-nums;
  }}
  .dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
  }}
  a {{
    color: var(--ink);
  }}
  .muted {{
    color: var(--ink-muted);
    font-size: 13px;
  }}
  h2 {{
    font-size: 16px;
    margin: 0 0 12px;
  }}
</style>
</head>
<body>
  <div class="header">
    <h1>Calidad de publicaciones</h1>
    <p>{subtitle} · {report.total} publicaciones clasificadas{skipped_note} · generado {generated_local}</p>
  </div>
  <div class="content">
    <div class="tiles">{tiles}
    </div>
    <h2>Publicaciones — <span id="table-title">{default_label}</span></h2>
    {tables_html}
    <p class="muted">{table_note}</p>
  </div>
  <script>
    function showBand(tile) {{
      var band = tile.dataset.band;
      document.querySelectorAll(".band-table").forEach(function (el) {{
        el.style.display = el.dataset.band === band ? "" : "none";
      }});
      document.querySelectorAll(".tile").forEach(function (el) {{
        var active = el.dataset.band === band;
        el.classList.toggle("tile-active", active);
        el.setAttribute("aria-pressed", active ? "true" : "false");
      }});
      document.getElementById("table-title").textContent = tile.dataset.label;
    }}
  </script>
</body>
</html>
"""


def save_report(html: str, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(html, encoding="utf-8")
    return path
