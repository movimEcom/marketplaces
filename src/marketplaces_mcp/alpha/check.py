"""Connection check for Alpha ERP: `alpha-erp-check [tabla]`.

Lists what the connector can see in ALPHA_DATA_DIR so you can confirm the
path, permissions and encoding before registering the MCP server.
"""

from __future__ import annotations

import sys

from .config import load_settings
from .dbf import AlphaData


def main() -> None:
    settings = load_settings()
    data = AlphaData(settings)
    tables = data.tables()
    print(f"Carpeta: {settings.data_dir}")
    print(f"{len(tables)} tablas .DBF encontradas.")
    if not tables:
        print("Revisa que ALPHA_DATA_DIR apunte a la carpeta de datos de Alpha (donde están los .DBF).")
        sys.exit(1)

    print("\nLas 15 más grandes:")
    for t in sorted(tables.values(), key=lambda t: t.size_bytes, reverse=True)[:15]:
        print(f"  {t.name:<40} {t.size_bytes / 1_048_576:8.1f} MB  {t.modified}")

    if len(sys.argv) > 1:
        table = sys.argv[1]
        desc = data.describe_table(table)
        print(f"\n{desc['table']}: {desc['record_count']} registros, encoding {desc['encoding']}")
        for f in desc["fields"]:
            print(f"  {f['name']:<12} {f['type']} {f['length']}" + (f".{f['decimals']}" if f["decimals"] else ""))
        print("\nPrimeros 3 registros:")
        for row in data.query(table, limit=3)["rows"]:
            print(f"  {row}")


if __name__ == "__main__":
    main()
