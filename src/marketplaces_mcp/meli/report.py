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
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    positional = [a for a in args if not a.startswith("--")]

    output_path = positional[0] if positional else "quality_report.html"
    status = None if "--all" in flags else "active"

    settings = load_settings()
    client = MeliClient(settings)
    try:
        print("Buscando publicaciones...")
        item_ids = client.list_all_my_item_ids(status=status)
        print(f"{len(item_ids)} publicaciones encontradas. Consultando su calidad "
              "(una llamada por publicación, puede tardar varios minutos)...")

        def progress(done: int, total: int) -> None:
            if done % 25 == 0 or done == total:
                print(f"  {done}/{total}")

        report = fetch_quality_report(client, status=status, on_progress=progress, item_ids=item_ids)
    finally:
        client.close()

    html = render_html(report, seller_label=f"vendedor {client.user_id}")
    path = save_report(html, output_path)

    regular_count = sum(1 for i in report.items if i.kind == "regular")
    catalog_count = sum(1 for i in report.items if i.kind == "catalog")
    print(f"\n{report.total} publicaciones clasificadas "
          f"({regular_count} regulares con score real, {catalog_count} de catálogo con score estimado):")
    for key, _low, _high, label, _color in BANDS:
        print(f"  {label}: {report.band_counts.get(key, 0)}")
    if catalog_count:
        print("  (el score de catálogo es nuestra estimación por completitud de ficha, "
              "no un dato oficial de Mercado Libre)")
    if report.skipped:
        print(f"\n{report.skipped} publicaciones omitidas (sin datos de calidad resolubles).")
    if report.skip_reasons:
        print("\nMotivo de las omitidas (código HTTP de /item/{id}/performance):")
        for status_code, count in sorted(report.skip_reasons.items()):
            print(f"  {status_code}: {count}")
            sample = report.skip_samples.get(status_code)
            if sample:
                print(f"    ejemplo de respuesta: {sample[:500]}")
    print(f"\nReporte guardado en: {path.resolve()}")

    if "--open" in flags:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
