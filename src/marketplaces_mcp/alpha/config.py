"""Configuration for the Alpha ERP connector, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    # None lets dbfread pick the code page from each DBF header (language driver byte).
    encoding: str | None
    max_rows: int
    # Subfolder levels below data_dir to look for .DBF files (0 = only data_dir itself).
    scan_depth: int


def load_settings() -> Settings:
    raw_dir = os.environ.get("ALPHA_DATA_DIR", "")
    if not raw_dir:
        raise RuntimeError(
            "ALPHA_DATA_DIR must be set (see .env.example): the folder where Alpha ERP "
            "keeps its .DBF files, e.g. C:\\Alpha\\Datos or \\\\SERVIDOR\\Alpha\\Datos."
        )
    data_dir = Path(raw_dir).expanduser()
    if not data_dir.is_dir():
        raise RuntimeError(
            f"ALPHA_DATA_DIR '{data_dir}' does not exist or is not a folder reachable from "
            "this machine. If the data lives on the server you reach by Remote Desktop, run "
            "this connector on that server or map its shared folder first (see README)."
        )

    encoding = os.environ.get("ALPHA_DBF_ENCODING", "").strip() or None
    max_rows = int(os.environ.get("ALPHA_MAX_ROWS", "500"))
    scan_depth = int(os.environ.get("ALPHA_SCAN_DEPTH", "1"))

    return Settings(data_dir=data_dir, encoding=encoding, max_rows=max_rows, scan_depth=scan_depth)
