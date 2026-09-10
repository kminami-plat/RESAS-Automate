"""RESASの画面からCSV出力したファイルを input/ に置いて正規化する経路。

RESAS-API は2025年3月24日に提供を終了しており、
新RESAS（https://resas.go.jp/）は画面からのCSVダウンロードのみを提供する。
自治体名単位のFrom-to（例: 流入元「山形市」）はこの経路でしか取得できないため、
手動DLしたCSVをそのまま所定の列仕様へ変換する。

使い方:
  1. RESASで対象マップ・地域（米沢市／置賜）・期間を指定して表示
  2. CSVダウンロード
  3. input/ にそのまま置く（ファイル名は任意）
  4. python -m resas_automate manual --kind fromto
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "cp932", "utf-8")

# 列見出しの表記ゆれを吸収するためのキーワード
COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "yearmonth": ("年月", "対象年月", "月次"),
    "year": ("年度", "年"),
    "month": ("月",),
    "area": ("市区町村", "エリア", "地域", "自治体", "都市"),
    "stay": ("滞在人口", "滞在者数", "人口"),
    "from": ("流入元", "発地", "居住地", "出発地", "From"),
    "lodging": ("延べ宿泊者数", "宿泊者数", "延べ宿泊"),
    "foreign": ("外国人",),
}

_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"文字コードを判別できません: {path}")


def _rows(path: Path) -> list[list[str]]:
    """CSV/TSV/Excel のどれでも「行のリスト」に揃えて返す。

    e-Statのファイル配布はExcelしか置いていない表があるため、
    手動DLしたブックもそのまま input/ に置けるようにしている。
    宿泊旅行統計調査のブックは自動取得できる（`fetch --lodging-source files`）ので、
    この経路は「県や市の観光統計をExcelでもらった」場合が主な用途。
    """
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return _xlsx_rows(path)
    return [r for r in csv.reader(io.StringIO(_decode(path))) if any(c.strip() for c in r)]


def _xlsx_rows(path: Path) -> list[list[str]]:
    """Excelの全シートを縦に連結して返す（見出し行の探索は既存ロジックに任せる）。"""
    from .. import xlsx

    with xlsx.Workbook(path) as book:
        out: list[list[str]] = []
        for name in book.sheets:
            rows = [r for r in book.rows(name) if any(str(c).strip() for c in r)]
            if rows:
                log.debug("%s: シート '%s' から %d行", path.name, name, len(rows))
                out.extend(rows)
        return out


# 「滞在人口率」「構成比」など、実数ではない派生列を掴まないための減点語
NEGATIVE_HINTS: tuple[str, ...] = ("率", "割合", "構成比", "前年", "増減", "指数", "順位")


def _score(cell: str, key: str) -> int:
    """見出しセルがkeyらしいほど高い点を返す。0なら不一致。"""
    cell = cell.strip()
    if not cell:
        return 0
    best = 0
    for hint in COLUMN_HINTS[key]:
        if cell == hint:
            best = max(best, 100)
        elif cell.endswith(hint):
            best = max(best, 60)
        elif hint in cell:
            best = max(best, 40)
    if best and any(n in cell for n in NEGATIVE_HINTS):
        best -= 35
    return max(best, 0)


def _find_header(rows: list[list[str]], required: tuple[str, ...]) -> tuple[int, dict[str, int]]:
    """必要な項目をすべて含む見出し行を探し、(行番号, {key: 列番号}) を返す。

    同じ見出し語を含む列が複数ある場合（例: 「滞在人口率」と「滞在人口」）は
    スコアの高い列を採用する。
    """
    for i, row in enumerate(rows[:20]):
        # (スコア, key, 列番号) を強い順に割り当てる
        cand = [
            (_score(cell, key), key, j)
            for key in required
            for j, cell in enumerate(row)
            if _score(cell, key) > 0
        ]
        cand.sort(key=lambda t: (-t[0], t[2]))
        found: dict[str, int] = {}
        used: set[int] = set()
        for _, key, j in cand:
            if key in found or j in used:
                continue
            found[key] = j
            used.add(j)
        if len(found) == len(required):
            return i, found
    raise RuntimeError(
        "見出し行を特定できません。必要な列: "
        + " / ".join("・".join(COLUMN_HINTS[k]) for k in required)
    )


def _num(cell: str) -> int | None:
    m = _NUM_RE.search(cell.replace("　", " "))
    if not m:
        return None
    try:
        return int(float(m.group().replace(",", "")))
    except ValueError:
        return None


def _yearmonth(cell: str) -> str | None:
    cell = cell.strip()
    m = re.search(r"(\d{4})\s*[-/年]\s*(\d{1,2})", cell)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{4})(\d{2})", cell)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def import_stay(path: Path) -> list[tuple[str, str, int]]:
    rows = _rows(path)
    hi, cols = _find_header(rows, ("yearmonth", "area", "stay"))
    out: list[tuple[str, str, int]] = []
    for row in rows[hi + 1 :]:
        if max(cols.values()) >= len(row):
            continue
        ym = _yearmonth(row[cols["yearmonth"]])
        value = _num(row[cols["stay"]])
        area = row[cols["area"]].strip()
        if ym and area and value is not None:
            out.append((ym, area, value))
    return sorted(out)


def import_fromto(path: Path) -> list[tuple[str, int]]:
    rows = _rows(path)
    try:
        hi, cols = _find_header(rows, ("from", "stay"))
        from_key = "from"
    except RuntimeError:
        # 「市区町村」見出しで流入元を表す出力にも対応する
        hi, cols = _find_header(rows, ("area", "stay"))
        from_key = "area"
    out: list[tuple[str, int]] = []
    for row in rows[hi + 1 :]:
        if max(cols.values()) >= len(row):
            continue
        name = row[cols[from_key]].strip()
        value = _num(row[cols["stay"]])
        if name and value is not None:
            out.append((name, value))
    return sorted(out, key=lambda kv: kv[1], reverse=True)


def import_lodging(path: Path) -> list[tuple[str, int, int]]:
    rows = _rows(path)
    hi, cols = _find_header(rows, ("yearmonth", "lodging", "foreign"))
    out: list[tuple[str, int, int]] = []
    for row in rows[hi + 1 :]:
        if max(cols.values()) >= len(row):
            continue
        ym = _yearmonth(row[cols["yearmonth"]])
        total = _num(row[cols["lodging"]])
        foreign = _num(row[cols["foreign"]])
        if ym and total is not None and foreign is not None:
            out.append((ym, total, foreign))
    return sorted(out)


IMPORTERS = {"stay": import_stay, "fromto": import_fromto, "lodging": import_lodging}


INPUT_SUFFIXES = ("*.csv", "*.xlsx", "*.xlsm")


def find_input(input_dir: Path, kind: str) -> Path:
    """input/ から対象ファイルを1つ選ぶ。kind名を含むファイルを優先する。"""
    candidates = sorted(
        p for pattern in INPUT_SUFFIXES for p in input_dir.glob(pattern) if p.is_file()
    )
    if not candidates:
        raise RuntimeError(
            f"{input_dir} にCSV/Excelがありません。RESASやe-Statから出力して置いてください。"
        )
    named = [p for p in candidates if kind in p.name.lower()]
    chosen = (named or candidates)[0]
    if len(named or candidates) > 1:
        log.warning(
            "候補が複数あります。%s を使用します（--file で明示できます）", chosen.name
        )
    return chosen
