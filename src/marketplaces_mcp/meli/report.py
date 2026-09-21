"""Standalone CLI to generate the listing-quality HTML dashboard.

Run with `meli-mx-quality-report` (no Claude Code / MCP client needed --
useful when only this repo's Python environment is available).
"""

from __future__ import annotations

import sys
import webbrowser

from .client import MeliClient
from .config import load_settings
from .quality import BANDS, fetch_quality_report, render_html, save_report


def main() -> None:
    output_path = sys.argv[1] if len(sys.argv) > 1 else "quality_report.html"
    status = None if "--all" in sys.argv else "active"

    settings = load_settings()
    client = MeliClient(settings)
    try:
        print("Consultando publicaciones y su score de calidad (puede tardar unos segundos)...")
        report = fetch_quality_report(client, status=status)
    finally:
        client.close()

    html = render_html(report, seller_label=f"vendedor {client.user_id}")
    path = save_report(html, output_path)

    print(f"\n{report.total} publicaciones analizadas:")
    for key, _low, _high, label, _color in BANDS:
        print(f"  {label}: {report.band_counts.get(key, 0)}")
    print(f"\nReporte guardado en: {path.resolve()}")

    if "--open" in sys.argv:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
