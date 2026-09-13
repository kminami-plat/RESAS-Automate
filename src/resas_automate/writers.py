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
    "cc-area": ["消費地", "消費額"],
    "cc-category": ["費目", "消費額"],
    "consumption-domestic": ["費目", "金額"],
    # 配信元（RESAS 国内観光消費分析）のCSVをそのまま通す種別。
    # 最終列だけは旅行種類で変わる（単価（宿泊中）/ 単価（日帰り））ので、
    # sources/resas_tourism_domestic.py が実際の見出しを write() に渡す。
    "spend-per-trip": [
        "集計年",
        "集計時期",
        "大分類コード",
        "大分類名",
        "中分類コード",
        "中分類名",
        "単価（宿泊中）",
    ],
}

FILENAMES: dict[str, str] = {
    "stay": "resas-stay.csv",
    "fromto": "resas-fromto.csv",
    "lodging": "resas-lodging.csv",
    "cc-area": "resas-cc-area.csv",
    "cc-category": "resas-cc-category.csv",
    "consumption-domestic": "resas-consumption-domestic.csv",
    "spend-per-trip": "resas-spend-per-trip.csv",
}


def write(
    kind: str,
    rows: Iterable[Sequence[object]],
    out_dir: Path,
    *,
    header: Sequence[str] | None = None,
) -> Path:
    """kind に応じた列名でCSVを出力する（対応する kind は HEADERS を参照）。

    ``header`` を渡せるのは**配信元のCSVをそのまま通す種別だけ**で、
    列の一部が取得条件で変わるものに限る（`spend-per-trip` の最終列）。
    それ以外は HEADERS が単一の情報源なので指定しないこと。
    列数が HEADERS と違えば弾く。

    Excelでの文字化けを避けるため UTF-8 BOM 付きで書き出す。
    """
    spec = HEADERS[kind]
    header = list(header) if header is not None else spec
    if len(header) != len(spec):
        raise ValueError(
            f"{kind}: 見出しの列数が{len(header)}、期待は{len(spec)} -> {header!r}"
        )
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
