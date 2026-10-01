"""Read-only access to Alpha ERP's DBF (xBase/FoxPro) tables.

Alpha ERP stores its data as .DBF files (plus .FPT/.DBT memo files) instead of
a SQL server. This module only ever opens them for reading, so it can't
corrupt the ERP's data or indexes while Alpha is running.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from dbfread import DBF, FieldParser

from .config import Settings


class _LenientParser(FieldParser):
    """Return the raw text instead of failing the whole read on one bad value
    (old xBase files often carry invalid dates or garbage in numeric fields)."""

    def parse(self, field, data):
        try:
            return super().parse(field, data)
        except ValueError:
            return data.decode("latin-1", errors="replace").strip()


@dataclass
class TableInfo:
    name: str
    path: Path
    size_bytes: int
    modified: str


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, str):
        return value.rstrip()
    return value


class AlphaData:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._tables: dict[str, TableInfo] | None = None

    # -- discovery ------------------------------------------------------------

    def tables(self, refresh: bool = False) -> dict[str, TableInfo]:
        """All .DBF files under ALPHA_DATA_DIR, keyed by their relative path without
        extension, lowercased (e.g. 'empresa01/clientes')."""
        if self._tables is None or refresh:
            root = self.settings.data_dir
            found: dict[str, TableInfo] = {}
            for path in root.rglob("*"):
                if path.suffix.lower() != ".dbf" or not path.is_file():
                    continue
                name = path.relative_to(root).with_suffix("").as_posix().lower()
                stat = path.stat()
                found[name] = TableInfo(
                    name=name,
                    path=path,
                    size_bytes=stat.st_size,
                    modified=dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                )
            self._tables = dict(sorted(found.items()))
        return self._tables

    def _resolve(self, table: str) -> TableInfo:
        tables = self.tables()
        key = table.lower().replace("\\", "/").removesuffix(".dbf")
        if key in tables:
            return tables[key]
        # Allow the bare file name when it's unambiguous across company folders.
        matches = [t for n, t in tables.items() if n.rsplit("/", 1)[-1] == key]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(
                f"Table '{table}' exists in several folders, use the full name: "
                + ", ".join(m.name for m in matches)
            )
        raise ValueError(f"Table '{table}' not found under {self.settings.data_dir}")

    def _open(self, info: TableInfo) -> DBF:
        return DBF(
            str(info.path),
            encoding=self.settings.encoding,
            char_decode_errors="replace",
            ignore_missing_memofile=True,
            parserclass=_LenientParser,
            load=False,
        )

    # -- public operations ----------------------------------------------------

    def list_tables(self, name_contains: str | None = None) -> list[dict[str, Any]]:
        needle = (name_contains or "").lower()
        return [
            {"table": t.name, "size_bytes": t.size_bytes, "modified": t.modified}
            for t in self.tables().values()
            if needle in t.name
        ]

    def describe_table(self, table: str) -> dict[str, Any]:
        info = self._resolve(table)
        dbf = self._open(info)
        return {
            "table": info.name,
            "path": str(info.path),
            # Header count includes records marked as deleted.
            "record_count": dbf.header.numrecords,
            "last_update": dbf.date.isoformat() if dbf.date else None,
            "encoding": dbf.encoding,
            "fields": [
                {"name": f.name, "type": f.type, "length": f.length, "decimals": f.decimal_count}
                for f in dbf.fields
            ],
        }

    def _iter_rows(self, info: TableInfo) -> Iterator[dict[str, Any]]:
        for record in self._open(info):
            yield {k: _jsonable(v) for k, v in record.items()}

    def query(
        self,
        table: str,
        fields: list[str] | None = None,
        equals: dict[str, Any] | None = None,
        contains: dict[str, str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Scan a table, keeping rows where every `equals` field matches exactly
        (strings compared trimmed and case-insensitive) and every `contains` field
        includes the given text (case-insensitive)."""
        info = self._resolve(table)
        limit = max(1, min(limit, self.settings.max_rows))
        equals = {k.upper(): v for k, v in (equals or {}).items()}
        contains = {k.upper(): str(v).lower() for k, v in (contains or {}).items()}
        wanted = [f.upper() for f in fields] if fields else None

        def matches(row: dict[str, Any]) -> bool:
            for key, expected in equals.items():
                value = row.get(key)
                if isinstance(value, str) and isinstance(expected, str):
                    if value.strip().lower() != expected.strip().lower():
                        return False
                elif value != expected:
                    return False
            for key, needle in contains.items():
                if needle not in str(row.get(key, "")).lower():
                    return False
            return True

        rows: list[dict[str, Any]] = []
        matched = 0
        for row in self._iter_rows(info):
            if not matches(row):
                continue
            matched += 1
            if matched <= offset:
                continue
            if len(rows) >= limit:
                # One more match exists past this page.
                return {"table": info.name, "rows": rows, "has_more": True}
            rows.append({k: row.get(k) for k in wanted} if wanted else row)
        return {"table": info.name, "rows": rows, "has_more": False}

    def search(self, text: str, table_contains: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Find rows whose character fields contain `text`, across every table (or
        those whose name includes `table_contains`). Useful to locate where a
        customer, SKU or invoice lives when the schema is unknown."""
        needle = text.lower()
        hits: list[dict[str, Any]] = []
        for name in self.tables():
            if table_contains and table_contains.lower() not in name:
                continue
            info = self.tables()[name]
            try:
                dbf = self._open(info)
                text_fields = [f.name for f in dbf.fields if f.type in ("C", "M", "V")]
                if not text_fields:
                    continue
                for record in dbf:
                    for field in text_fields:
                        value = record.get(field)
                        if isinstance(value, str) and needle in value.lower():
                            hits.append({
                                "table": name,
                                "field": field,
                                "row": {k: _jsonable(v) for k, v in record.items()},
                            })
                            break
                    if len(hits) >= limit:
                        return hits
            except Exception as exc:  # unreadable/locked/unknown-format file: report and move on
                hits.append({"table": name, "error": f"{type(exc).__name__}: {exc}"})
        return hits
