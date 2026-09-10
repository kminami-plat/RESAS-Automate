"""xlsx（OOXML SpreadsheetML）の最小読み取り。標準ライブラリのみ。

openpyxl を入れずに済ませるための自前実装。政府統計のExcelを読むのに必要な
機能だけを持つ:

- シート名の列挙（ブック内の並び順を保つ）
- シート → 行（セルを文字列のリストで返す）
- 共有文字列（sharedStrings.xml）の解決

割り切っている点:

- **書式（styles.xml）は解釈しない**。数値はシート上の生の値（文字列）で返す。
  日付セルはシリアル値のまま出る。宿泊統計の表は整数と "-" しか使わないため問題ない。
- **結合セルは展開しない**。左上のセルだけが値を持ち、他は空文字になる。
  見出しは行をまたいで書かれるため、列の特定は :func:`find_column` の
  「複数の見出し行を縦に走査して名称マッチ」で吸収する。

政府統計のExcelは見出しセルにルビ（カタカナ）と注記（``1)``）が
くっついてくるので、比較の前に必ず :func:`norm` を通すこと。
例: ``"延べ\\n宿泊者数\\n1)ノシュクハクシャスウ"`` → ``"延べ宿泊者数"``
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PKGREL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOCREL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# 見出しの末尾に付くルビ（カタカナ）と注記番号
_RUBY_RE = re.compile(r"[ァ-ー]+$")
_NOTE_RE = re.compile(r"\d+\)$")


def norm(cell: object) -> str:
    """見出しセルを比較可能な形に正規化する（空白・ルビ・注記を落とす）。"""
    s = str(cell or "").replace("　", " ")
    s = re.sub(r"\s+", "", s)
    # 「…1)ノシュクハクシャスウ」のようにルビと注記が両方付くので2回まわす
    for _ in range(2):
        s = _RUBY_RE.sub("", s)
        s = _NOTE_RE.sub("", s)
    return s


def _col_index(ref: str) -> int:
    """セル参照 'BC12' の列部分を0始まりの列番号にする。"""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


class Workbook:
    """xlsxを開いてシート単位で行を取り出す。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._zip = zipfile.ZipFile(self.path)
        self._shared = self._read_shared_strings()
        self.sheets = self._read_sheet_map()

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "Workbook":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----------------------------------------------------------------- 内部

    def _read_shared_strings(self) -> list[str]:
        try:
            raw = self._zip.read("xl/sharedStrings.xml")
        except KeyError:
            return []
        root = ET.fromstring(raw)
        # <si> の下の <t> をすべて連結する（<r> で分割されていても1文字列になる）
        return [
            "".join(t.text or "" for t in si.iter(f"{_MAIN}t"))
            for si in root.iter(f"{_MAIN}si")
        ]

    def _read_sheet_map(self) -> dict[str, str]:
        """{シート名: zip内パス} をブックの並び順で返す。"""
        rels = ET.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        targets = {
            r.get("Id"): (r.get("Target") or "") for r in rels.iter(f"{_PKGREL}Relationship")
        }
        book = ET.fromstring(self._zip.read("xl/workbook.xml"))
        out: dict[str, str] = {}
        for sheet in book.iter(f"{_MAIN}sheet"):
            name = sheet.get("name") or ""
            target = targets.get(sheet.get(f"{_DOCREL}id") or "", "")
            if not name or not target:
                continue
            target = target.lstrip("/")
            out[name] = target if target.startswith("xl/") else f"xl/{target}"
        return out

    # ----------------------------------------------------------------- 公開

    def rows(self, sheet_name: str) -> list[list[str]]:
        """シートの行を返す。空行は詰めず、各行は最終セルまでの文字列リスト。"""
        try:
            path = self.sheets[sheet_name]
        except KeyError:
            raise KeyError(
                f"シート '{sheet_name}' がありません: {sorted(self.sheets)[:10]}…"
            ) from None
        root = ET.fromstring(self._zip.read(path))
        out: list[list[str]] = []
        for row in root.iter(f"{_MAIN}row"):
            cells: dict[int, str] = {}
            for c in row.iter(f"{_MAIN}c"):
                value = self._cell_value(c)
                if value:
                    cells[_col_index(c.get("r") or "")] = value
            if cells:
                width = max(cells) + 1
                out.append([cells.get(i, "") for i in range(width)])
        return out

    def _cell_value(self, c: ET.Element) -> str:
        kind = c.get("t")
        if kind == "inlineStr":
            return "".join(t.text or "" for t in c.iter(f"{_MAIN}t"))
        v = c.find(f"{_MAIN}v")
        if v is None or v.text is None:
            return ""
        if kind == "s":  # 共有文字列
            try:
                return self._shared[int(v.text)]
            except (ValueError, IndexError):
                return ""
        if kind == "e":  # #REF! などのエラー値
            return ""
        return v.text


def sheet_title(rows: list[list[str]], *, scan: int = 3) -> str:
    """表タイトル（先頭付近の最初の非空セル）を正規化して返す。

    宿泊旅行統計調査のシートは1〜2行目にタイトルが入り、続き行に
    「並びに…」が続く。改訂で表番号が変わるため、表の特定は
    シート名や番号ではなく**このタイトル文字列のキーワード**で行う。
    """
    parts: list[str] = []
    for row in rows[:scan]:
        for cell in row:
            if str(cell).strip():
                parts.append(norm(cell))
                break
    return "".join(parts)


def find_column(rows: list[list[str]], names: tuple[str, ...], *, scan: int = 12) -> int:
    """見出し行群を走査して、``names`` のいずれかと一致する列番号を返す。

    見出しは結合セルで複数行に散らばるため、先頭 ``scan`` 行すべてを
    候補として見る。完全一致のみ（部分一致だと「延べ宿泊者数」が
    「うち外国人延べ宿泊者数」を掴んでしまう）。見つからなければ -1。
    """
    for row in rows[:scan]:
        for j, cell in enumerate(row):
            if norm(cell) in names:
                return j
    return -1
