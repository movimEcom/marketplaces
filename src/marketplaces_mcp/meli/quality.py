"""Listing quality report: buckets the seller's items by Mercado Libre's
`health` score (0..1, how complete/good a listing's title/photos/attributes
are) into the same four bands Mercado Libre's own seller tools use.
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

_ITEM_ATTRIBUTES = ["id", "title", "health", "status", "permalink"]


def _band_for_score(score: int) -> str:
    for key, low, high, _label, _color in BANDS:
        if low <= score <= high:
            return key
    return "critico"


@dataclass
class QualityItem:
    id: str
    title: str
    score: int
    status: str
    permalink: str
    band: str


@dataclass
class QualityReport:
    generated_at: str
    total: int
    band_counts: dict[str, int]
    items: list[QualityItem] = field(default_factory=list)

    def items_in_band(self, band: str) -> list[QualityItem]:
        return [item for item in self.items if item.band == band]


def fetch_quality_report(
    client: MeliClient, status: str | None = "active"
) -> QualityReport:
    item_ids = client.list_all_my_item_ids(status=status)
    raw_items = client.multiget_items(item_ids, attributes=_ITEM_ATTRIBUTES)

    items: list[QualityItem] = []
    band_counts = {key: 0 for key, *_ in BANDS}

    for raw in raw_items:
        health = raw.get("health")
        score = round((health or 0) * 100)
        band = _band_for_score(score)
        band_counts[band] += 1
        items.append(
            QualityItem(
                id=raw["id"],
                title=raw.get("title", ""),
                score=score,
                status=raw.get("status", ""),
                permalink=raw.get("permalink", ""),
                band=band,
            )
        )

    items.sort(key=lambda item: item.score)

    return QualityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total=len(items),
        band_counts=band_counts,
        items=items,
    )


def _esc(text: str) -> str:
    return html_escape.escape(text, quote=True)


def _stat_tile(key: str, low: int, high: int, label: str, color: str, count: int) -> str:
    range_label = f"{low}–100" if high == 100 else f"{low}–{high}" if key != "critico" else f"< {high + 1}"
    return f"""
    <div class="tile">
      <span class="tile-icon" style="background:{color}" aria-hidden="true"></span>
      <div class="tile-value">{count}</div>
      <div class="tile-label">{_esc(label)}</div>
      <div class="tile-range">{range_label}</div>
    </div>"""


def _item_row(item: QualityItem, color_by_band: dict[str, str]) -> str:
    color = color_by_band[item.band]
    title = _esc(item.title) or item.id
    link = _esc(item.permalink) if item.permalink else "#"
    return f"""
        <tr>
          <td class="num">{item.score}</td>
          <td><span class="dot" style="background:{color}"></span>{item.band.capitalize()}</td>
          <td><a href="{link}" target="_blank" rel="noopener">{title}</a></td>
          <td>{_esc(item.status)}</td>
        </tr>"""


def render_html(report: QualityReport, seller_label: str = "") -> str:
    color_by_band = {key: color for key, *_rest, color in BANDS}
    tiles = "".join(
        _stat_tile(key, low, high, label, color, report.band_counts.get(key, 0))
        for key, low, high, label, color in BANDS
    )

    attention_items = [i for i in report.items if i.band in ("mejorable", "critico")]
    if attention_items:
        rows = "".join(_item_row(item, color_by_band) for item in attention_items[:200])
        table_note = (
            f"Mostrando {min(len(attention_items), 200)} de {len(attention_items)} "
            "publicaciones en banda Mejorable o Crítico, ordenadas de menor a mayor score."
        )
        table_html = f"""
        <table>
          <thead>
            <tr><th>Score</th><th>Banda</th><th>Publicación</th><th>Estado</th></tr>
          </thead>
          <tbody>{rows}
          </tbody>
        </table>
        <p class="muted">{table_note}</p>"""
    else:
        table_html = '<p class="muted">No hay publicaciones en banda Mejorable o Crítico. \U0001F389</p>'

    generated_local = report.generated_at.replace("T", " ").split(".")[0] + " UTC"
    subtitle = f"Mercado Libre México{' · ' + _esc(seller_label) if seller_label else ''}"

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
    max-width: 980px;
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
    <p>{subtitle} · {report.total} publicaciones · generado {generated_local}</p>
  </div>
  <div class="content">
    <div class="tiles">{tiles}
    </div>
    <h2>Publicaciones que necesitan atención</h2>
    {table_html}
  </div>
</body>
</html>
"""


def save_report(html: str, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(html, encoding="utf-8")
    return path
