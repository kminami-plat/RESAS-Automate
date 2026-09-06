"""指定された列名でCSVを書き出す。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterable, Sequence

log = logging.getLogger(__name__)

HEADERS: dict[str, list[str]] = {
    "stay": ["年月", "エリア", "滞在人口"],
    "fromto": ["流入元", "滞在人口"],
    "lodging": ["年月", "宿泊者数", "うち外国人"],
}

FILENAMES: dict[str, str] = {
    "stay": "resas-stay.csv",
    "fromto": "resas-fromto.csv",
    "lodging": "resas-lodging.csv",
}


def write(kind: str, rows: Iterable[Sequence[object]], out_dir: Path) -> Path:
    """kind（stay/fromto/lodging）に応じた列名でCSVを出力する。

    Excelでの文字化けを避けるため UTF-8 BOM 付きで書き出す。
    """
    header = HEADERS[kind]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILENAMES[kind]
    rows = list(rows)
    if not rows:
        raise RuntimeError(f"{kind}: 出力する行がありません（取得条件を確認してください）")
    for i, row in enumerate(rows):
        if len(row) != len(header):
            raise ValueError(
                f"{kind}: {i}行目の列数が{len(row)}、期待は{len(header)} -> {row!r}"
            )
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    log.info("書き出し: %s (%d行)", path, len(rows))
    return path


def sidecar_note(path: Path, lines: list[str]) -> Path:
    """CSVの出典・注記を同名の .md に残す（CSV本体は列仕様どおりに保つため）。"""
    note = path.with_suffix(".md")
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note
