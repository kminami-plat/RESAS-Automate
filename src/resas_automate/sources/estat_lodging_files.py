"""宿泊旅行統計調査のExcel（ファイル配布）から月次の宿泊者数を取り出す。

`estat_lodging.py`（API経路）が 2014-01〜2016-12 で頭打ちになるのを埋める経路。
`estat_files.py` が落としてきたExcelを読み、
`writers.HEADERS["lodging"]`（年月 / 宿泊者数 / うち外国人）の行に変換する。

## 表の選び方：番号ではなく**タイトルの語**で決める

宿泊旅行統計調査は改訂で表番号も集計区分も変わる。実測（2026-09）:

- 年確定値（〜2025年）: 施設規模の区分は **従業者数**。全施設＝第2表、
  従業者数10人以上＝参考第3表。市区町村別の表は**無い**。
- 速報値（2015-04〜）: 施設規模の区分は **客室数**。全施設＝第2表。
  さらに **参考第6表＝市区町村別 延べ宿泊者数 / 参考第8表＝市区町村別
  外国人延べ宿泊者数** があり、**米沢市の月次宿泊者数がここで取れる**。

そのため `_SHEET_RULES` はシート名や番号を見ず、タイトル文字列の
必須語・除外語で表を特定する。`xlsx.sheet_title()` 参照。

## 系列が2本あることに注意

- ``all``    … 全施設（第2表）。2014年から現在まで一貫して存在する。
- ``over10`` … 従業者数10人以上の施設（参考第3表）。**API経路と同じ系列**だが、
  2026年の速報値では区分自体が廃止されており取得できない。

同じ年月でも値は違う（2016-01 山形県: all=393,130 / over10=301,410）。
どちらを使ったかは必ず sidecar の .md に書き出すこと。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import xlsx
from . import estat_files

log = logging.getLogger(__name__)

# 施設規模の区分に関わらず、延べ宿泊者数の総数列と外国人列はこの見出しを持つ
_TOTAL_NAMES = ("延べ宿泊者数",)
_FOREIGN_NAMES = ("うち外国人延べ宿泊者数", "外国人延べ宿泊者数")

# 他の表を掴まないための除外語（実宿泊者数・稼働率・施設数・タイプ別 …）
_COMMON_EXCLUDE = (
    "実宿泊者数",
    "稼働率",
    "利用客室数",
    "別施設数",
    "回収施設数",
    "宿泊施設タイプ",
    "居住地",
    "国籍",
)


@dataclass(frozen=True)
class SheetRule:
    """表を特定するためのタイトル条件。"""

    label: str
    require: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    reject_reference: bool = False  # 「参考第N表」を除外するか
    require_reference: bool = False


# table（系列）× 値の種類 → 表の特定条件
_SHEET_RULES: dict[str, dict[str, SheetRule]] = {
    "all": {
        "both": SheetRule(
            label="全施設（第2表）",
            require=("47区分", "別延べ宿泊者数"),
            exclude=_COMMON_EXCLUDE,
            reject_reference=True,  # 参考表は10人以上/20室以上などの部分集計
        )
    },
    "over10": {
        "both": SheetRule(
            label="従業者数10人以上の施設（参考第3表・API経路と同じ系列）",
            require=("47区分", "別延べ宿泊者数", "従業者数10人以上"),
            exclude=_COMMON_EXCLUDE,
            require_reference=True,
        )
    },
    "city": {
        "total": SheetRule(
            label="主な市区町村別 延べ宿泊者数（参考第6表）",
            require=("主な市区町村", "別延べ宿泊者数"),
            exclude=_COMMON_EXCLUDE + ("外国人",),
        ),
        "foreign": SheetRule(
            label="主な市区町村別 外国人延べ宿泊者数（参考第8表）",
            require=("主な市区町村", "別外国人延べ宿泊者数"),
            exclude=("実宿泊者数", "稼働率", "利用客室数", "別施設数", "回収施設数"),
        ),
    },
}

TABLES = tuple(_SHEET_RULES)
# 地域粒度が都道府県のときに選べる系列（"city" は --lodging-area で選ぶので除く）
PREF_TABLES = ("all", "over10")

# ------------------------------------------------------------------ From-to

# 「居住地（47区分）別延べ宿泊者数」＝ 宿泊者がどこから来たか。
# `_COMMON_EXCLUDE` は「居住地」を除外語に持つ（宿泊者数の表を選ぶときは邪魔なので）が、
# こちらは逆に必須語なので、除外語を別に組み直す。
_FROMTO_EXCLUDE = (
    "実宿泊者数",
    "稼働率",
    "利用客室数",
    "別施設数",
    "回収施設数",
    "宿泊施設タイプ",
    "国籍",
    "主な市区町村",
    "観光目的の宿泊者が50％",  # 参考第3表/第4表（目的別の内訳）を外す
)

_FROMTO_RULE = SheetRule(
    label="居住地（47区分）別延べ宿泊者数（客室数200室以上の施設）",
    # 「居住地（2区分）」の表（参考第21表など）を掴まないよう区分数まで含めて要求する
    require=("47区分", "居住地（47区分", "別延べ宿泊者数"),
    exclude=_FROMTO_EXCLUDE,
    require_reference=True,
)

# この表は第2次速報値にしか無い（年確定値の居住地表は2区分どまり）
PREFER_FROMTO = ("第2次速報値",)

# 居住地の列には47都道府県・国外のほかに運輸局の集計列が混ざる。
# 足すと二重計上になるので落とす（合計＝総数 で検証する）。
_AGGREGATE_SUFFIXES = ("運輸局", "総合事務局")

# 提供統計を選ぶ優先順。
#
# **第1次速報値は入れない**。実測（2026-09）では第1次速報値のブックは
# 全国計しか載っておらず（第1表＝延べ宿泊者数及び外国人延べ宿泊者数の1行）、
# 都道府県別も市区町村別も存在しない。入れると最新月ほしさに毎回ダウンロードして
# 必ず空振りする。最新月が第1次速報値にしか無い時期は、その月は取れないのが正しい。
#
# 市区町村別（参考第6表・第8表）は第2次速報値にしか無い。年確定値には無い。
PREFER_PREF = ("年確定値", "第2次速報値")
PREFER_CITY = ("第2次速報値",)

_MONTH_SHEET_RE = re.compile(r"[(（](\d{1,2})月[)）]")
_TIME_CODE_RE = re.compile(r"^(\d{4})(\d{4})(\d{2})$")


class LodgingFileError(RuntimeError):
    pass


@dataclass
class Provenance:
    """出典を sidecar に書くための記録。"""

    dataset: str = ""
    cycle: str = ""
    table_label: str = ""
    area_label: str = ""
    year_month: str = ""  # From-to はスナップショットなので対象年月を持つ
    files: list[str] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 表の特定

def _matches(title: str, rule: SheetRule) -> bool:
    if rule.reject_reference and title.startswith("参考"):
        return False
    if rule.require_reference and not title.startswith("参考"):
        return False
    if any(word not in title for word in rule.require):
        return False
    return not any(word in title for word in rule.exclude)


def _find_sheet(book: xlsx.Workbook, month: int, rule: SheetRule) -> tuple[str, list[list[str]]]:
    """条件に合うシートを1枚返す。複数該当したら表番号の小さい方を採る。"""
    hits: list[tuple[int, str, list[list[str]]]] = []
    for name in book.sheets:
        m = _MONTH_SHEET_RE.search(name)
        if not m or int(m.group(1)) != month:
            continue
        rows = book.rows(name)
        if not rows:
            continue
        if _matches(xlsx.sheet_title(rows), rule):
            number = re.search(r"(\d+)表", name)
            hits.append((int(number.group(1)) if number else 999, name, rows))
    if not hits:
        raise LodgingFileError(
            f"{month}月の「{rule.label}」に該当するシートがありません"
            f"（{book.path.name}）。表の構成が変わった可能性があります。"
        )
    hits.sort()
    if len(hits) > 1:
        log.warning(
            "%d月の「%s」に複数該当（%s）。%s を使用します。",
            month, rule.label, "・".join(h[1] for h in hits), hits[0][1],
        )
    return hits[0][1], hits[0][2]


# ------------------------------------------------------------------ 値の取り出し

def _area_key(cell: object) -> str:
    """地域セルを比較用に整える（先頭の都道府県コードを落とす）。"""
    return re.sub(r"^\d+", "", xlsx.norm(cell))


def _number(cell: object) -> int | None:
    """統計値を整数にする。``-`` や ``***``（秘匿・皆無）は None。"""
    text = str(cell or "").strip().replace(",", "")
    if not text or text in {"-", "‐", "－", "…", "***", "x", "X"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _row_for_area(rows: list[list[str]], area: str) -> list[str]:
    for row in rows:
        if row and _area_key(row[0]) == area:
            return row
    raise LodgingFileError(
        f"地域『{area}』の行が見つかりません。"
        "対象がこの表の掲載対象（主な市区町村は一部のみ）か確認してください。"
    )


def _check_period(rows: list[list[str]], year: int, month: int, sheet: str) -> None:
    """シート内の時間軸コードと、こちらが想定する年月が食い違わないか確かめる。"""
    for row in rows[:6]:
        for cell in row:
            m = _TIME_CODE_RE.match(str(cell).strip())
            if not m:
                continue
            got_year, got_month = int(m.group(1)), int(m.group(2))
            if (got_year, got_month) != (year, month):
                raise LodgingFileError(
                    f"{sheet} の時間軸コードは {got_year}年{got_month}月ですが、"
                    f"{year}年{month}月として読もうとしました。ファイルの対応付けが誤っています。"
                )
            return


def _series(rows: list[list[str]], sheet: str, names: tuple[str, ...]) -> int:
    col = xlsx.find_column(rows, names)
    if col < 0:
        raise LodgingFileError(
            f"{sheet} に列『{names[0]}』が見つかりません。見出しの表記が変わった可能性があります。"
        )
    return col


def read_month(
    book: xlsx.Workbook,
    *,
    year: int,
    month: int,
    area: str,
    table: str,
    sheets_out: set[str] | None = None,
) -> tuple[int, int]:
    """1か月分の (宿泊者数, うち外国人) を返す。

    ``sheets_out`` を渡すと、実際に読んだシート名を書き込む（sidecar用）。
    """
    rules = _SHEET_RULES[table]
    if table == "city":
        # 市区町村別は総数と外国人が別々の参考表に分かれている
        total = _read_single(
            book, year, month, area, rules["total"], _TOTAL_NAMES, sheets_out
        )
        foreign = _read_single(
            book, year, month, area, rules["foreign"], _FOREIGN_NAMES, sheets_out
        )
        if total is None:
            raise LodgingFileError(f"『{area}』に延べ宿泊者数がありません（秘匿・欠測）")
        return total, (foreign or 0)

    sheet, rows = _find_sheet(book, month, rules["both"])
    _check_period(rows, year, month, sheet)
    if sheets_out is not None:
        sheets_out.add(sheet)
    row = _row_for_area(rows, area)
    total = _number(_at(row, _series(rows, sheet, _TOTAL_NAMES)))
    foreign = _number(_at(row, _series(rows, sheet, _FOREIGN_NAMES)))
    if total is None:
        raise LodgingFileError(f"{sheet} の『{area}』に延べ宿泊者数がありません（秘匿・欠測）")
    return total, (foreign or 0)


def _read_single(
    book: xlsx.Workbook,
    year: int,
    month: int,
    area: str,
    rule: SheetRule,
    names: tuple[str, ...],
    sheets_out: set[str] | None = None,
) -> int | None:
    sheet, rows = _find_sheet(book, month, rule)
    _check_period(rows, year, month, sheet)
    if sheets_out is not None:
        sheets_out.add(sheet)
    row = _row_for_area(rows, area)
    # 市区町村別の外国人は「-」（皆無）が普通にあるので、Noneは呼び出し側で0にする
    return _number(_at(row, _series(rows, sheet, names)))


def _at(row: list[str], index: int) -> str:
    return row[index] if 0 <= index < len(row) else ""


def months_in(book: xlsx.Workbook) -> list[int]:
    """ブックに含まれる月（年計シートは除く）。"""
    out = {
        int(m.group(1))
        for name in book.sheets
        if (m := _MONTH_SHEET_RE.search(name))
    }
    return sorted(out)


# ------------------------------------------------------------------ 取得の本体

def _table_label(table: str) -> str:
    return "／".join(rule.label for rule in _SHEET_RULES[table].values())


@dataclass(frozen=True)
class _Candidate:
    """ある年をカバーしうる「提供統計 × 提供周期」1組。"""

    dataset: estat_files.Dataset
    cycle: estat_files.Cycle
    periods: list[estat_files.Period]
    rank: int  # PREFER_* の並び順（小さいほど確報に近い）


def _candidates(
    year: int, prefer: tuple[str, ...], use_browser: bool
) -> list[_Candidate]:
    """指定年を含む「提供統計 × 提供周期」を、試す順に並べて返す。

    **その年を最も広くカバーするものを1つだけ選ぶ**（同数なら確報を優先）。
    系列の異なるファイルを月単位で混ぜると、3列しかないCSVでは
    月ごとの確報/速報の別を表現できないため、月単位の寄せ集めはしない。
    """
    datasets = estat_files.list_datasets(use_browser=use_browser)
    out: list[_Candidate] = []
    for rank, keyword in enumerate(prefer):
        dataset = estat_files.find_dataset(datasets, keyword)
        if dataset is None:
            continue
        for cycle in estat_files.list_cycles(dataset.tstat, use_browser=use_browser):
            periods = [
                p
                for p in estat_files.list_periods(
                    dataset.tstat, cycle.code, use_browser=use_browser
                )
                if p.year == year
            ]
            if periods:
                out.append(_Candidate(dataset, cycle, periods, rank))
    # 年次ファイルは1期で12か月分なので、月数は「期の数」ではなく実質の広さで測る
    out.sort(key=lambda c: (-_coverage(c), c.rank))
    return out


def _coverage(candidate: _Candidate) -> int:
    if any(p.month is None for p in candidate.periods):
        return 12  # 年次ファイル1本＝12か月分
    return len(candidate.periods)


def fetch_lodging(
    year: int,
    *,
    pref_name: str,
    city_name: str | None = None,
    table: str = "all",
    cache_dir: Path,
    use_browser: bool = False,
) -> tuple[list[tuple[str, int, int]], Provenance]:
    """指定年の (年月, 宿泊者数, うち外国人) を昇順で返す。

    ``city_name`` を渡すと市区町村別（速報値の参考表）を読む。
    その場合 ``table`` は無視して ``city`` 系列になる。
    """
    if city_name:
        table = "city"
    if table not in _SHEET_RULES:
        raise LodgingFileError(f"未知の表指定 '{table}'。指定可能: {', '.join(TABLES)}")

    area = f"{pref_name}{city_name}" if city_name else pref_name
    prefer = PREFER_CITY if city_name else PREFER_PREF
    prov = Provenance(table_label=_table_label(table), area_label=area)

    candidates = _candidates(year, prefer, use_browser)
    if not candidates:
        raise LodgingFileError(
            f"{year}年の宿泊旅行統計調査ファイルが見つかりませんでした"
            f"（探した提供統計: {' / '.join(prefer)}）。"
            "`resas-automate estat-files` で収録年月を確認してください。"
        )

    errors: list[str] = []
    for candidate in candidates:
        log.info(
            "宿泊者数の取得元: %s / %s（%d期）",
            candidate.dataset.name, candidate.cycle.label, len(candidate.periods),
        )
        try:
            rows = _collect(candidate, year, area, table, cache_dir, use_browser, prov)
        except LodgingFileError as exc:
            errors.append(f"{candidate.dataset.name}: {exc}")
            continue
        if rows:
            return rows, prov
        errors.append(f"{candidate.dataset.name}: 読み取れた月がありません")

    raise LodgingFileError(
        f"{year}年の『{area}』を取得できませんでした。\n  " + "\n  ".join(errors)
    )


def _collect(
    candidate: _Candidate,
    year: int,
    area: str,
    table: str,
    cache_dir: Path,
    use_browser: bool,
    prov: Provenance,
) -> list[tuple[str, int, int]]:
    """1つの提供統計から、その年の全月を集める。"""
    found: dict[str, tuple[int, int]] = {}
    sheets: set[str] = set()
    for period in sorted(candidate.periods, key=lambda p: p.month or 0):
        files = estat_files.list_files(
            candidate.dataset.tstat, candidate.cycle.code, period, use_browser=use_browser
        )
        data = [f for f in files if f.file_kind == estat_files.FILE_KIND_DATA]
        if not data:
            log.warning("%s にダウンロード可能なデータファイルがありません", period.label)
            continue
        label = f"lodging_{year:04d}" + (f"-{period.month:02d}" if period.month else "")
        path = estat_files.download(
            data[0], cache_dir, use_browser=use_browser, label=label
        )
        with xlsx.Workbook(path) as book:
            # 年次ファイルは12か月分、月次ファイルは1か月分を含む
            wanted = [period.month] if period.month else months_in(book)
            for month in wanted:
                try:
                    total, foreign = read_month(
                        book,
                        year=year,
                        month=month,
                        area=area,
                        table=table,
                        sheets_out=sheets,
                    )
                except LodgingFileError as exc:
                    log.warning("%d-%02d を読めませんでした: %s", year, month, exc)
                    continue
                found[f"{year:04d}-{month:02d}"] = (total, foreign)
        prov.files.append(path.name)

    if not found:
        return []
    prov.dataset = candidate.dataset.name
    prov.cycle = candidate.cycle.label
    prov.sheets = sorted(sheets)
    return [(ym, *found[ym]) for ym in sorted(found)]


# ------------------------------------------------------------------ From-to の読み取り


def _fromto_header(rows: list[list[str]]) -> tuple[int, list[str]]:
    """居住地の見出し行を探して (行番号, 正規化済みの列名) を返す。

    「総数」が出てくる行が居住地の見出し行（その右に47都道府県が並ぶ）。
    """
    for i, row in enumerate(rows[:12]):
        names = [xlsx.norm(c) for c in row]
        if "総数" in names:
            return i, names
    raise LodgingFileError(
        "居住地の見出し行（『総数』を含む行）が見つかりません。表の構成が変わった可能性があります。"
    )


@dataclass(frozen=True)
class FromTo:
    """居住地別の内訳ひとまとめ。"""

    rows: list[tuple[str, int]]  # (居住地, 人泊) の降順。不詳があれば含む
    total: int  # 表の「総数」
    unknown: int  # 総数 − 都道府県内訳の合計（居住地不詳）
    scope: str  # 「客室数200室以上の施設」など。月によって変わる
    sheet: str


_SCOPE_RE = re.compile(r"客室数\d+室以上の施設")


def read_fromto(
    book: xlsx.Workbook, *, year: int, month: int, area: str
) -> FromTo:
    """居住地別の内訳を返す。

    運輸局の集計列は落とす（都道府県の積み上げなので足すと二重計上になる）。

    **内訳の合計は総数と一致しないことが多い**。実測（2026年）では総数の
    12〜30%が都道府県に割り当てられておらず、これは居住地不詳とみなすほかない
    （表に不詳列は無い）。差分は「不詳」として1行に立て、
    CSVの合計が総数と一致する状態を保つ。

    逆に合計が総数を**超えた**場合は、集計列を拾っている＝実装のバグなので停止する。
    """
    sheet, rows = _find_sheet(book, month, _FROMTO_RULE)
    _check_period(rows, year, month, sheet)
    header_row, names = _fromto_header(rows)
    total_col = names.index("総数")
    row = _row_for_area(rows[header_row + 1 :], area)

    out: list[tuple[str, int]] = []
    for j in range(total_col + 1, len(names)):
        name = names[j]
        if not name or name.endswith(_AGGREGATE_SUFFIXES):
            continue
        out.append((name, _number(_at(row, j)) or 0))

    total = _number(_at(row, total_col))
    if total is None:
        raise LodgingFileError(f"{sheet} の『{area}』に総数がありません（秘匿・欠測）")

    got = sum(v for _, v in out)
    if got > total:
        raise LodgingFileError(
            f"{sheet} の『{area}』で内訳の合計 {got:,} が総数 {total:,} を超えました"
            "（運輸局などの集計列を二重計上しています）"
        )
    unknown = total - got
    if unknown:
        share = unknown / total * 100
        log.warning(
            "%s: %s の居住地内訳は総数の %.1f%%（%s人泊）が不詳です",
            sheet, area, share, f"{unknown:,}",
        )
        out.append(("不詳", unknown))

    scope_match = _SCOPE_RE.search(xlsx.sheet_title(rows))
    out.sort(key=lambda kv: kv[1], reverse=True)
    return FromTo(
        rows=out,
        total=total,
        unknown=unknown,
        scope=scope_match.group() if scope_match else "（対象施設の記載なし）",
        sheet=sheet,
    )


def fetch_fromto(
    *,
    pref_name: str,
    month: str | None = None,
    cache_dir: Path,
    use_browser: bool = False,
) -> tuple[FromTo, Provenance]:
    """宿泊者の居住地別内訳を取る。``month`` は 'YYYY-MM'、省略時は最新月。

    「どこから来た人が泊まっているか」という意味でのFrom-toであり、
    国勢調査経路（通勤・通学者数）とは別物。詳細は cli 側の sidecar に書く。
    """
    datasets = estat_files.list_datasets(use_browser=use_browser)
    dataset = estat_files.find_dataset(datasets, *PREFER_FROMTO)
    if dataset is None:
        raise LodgingFileError(
            f"提供統計『{' / '.join(PREFER_FROMTO)}』が見つかりません。"
            "`resas-automate estat-files` で一覧を確認してください。"
        )

    periods: list[estat_files.Period] = []
    cycle_label = ""
    for cycle in estat_files.list_cycles(dataset.tstat, use_browser=use_browser):
        found = estat_files.list_periods(
            dataset.tstat, cycle.code, use_browser=use_browser
        )
        if found:
            periods, cycle_label = found, cycle.label
            cycle_code = cycle.code
            break
    periods = [p for p in periods if p.month is not None]
    if not periods:
        raise LodgingFileError(f"{dataset.name} に月次の収録がありません")

    if month:
        want = [p for p in periods if f"{p.year:04d}-{p.month:02d}" == month]
        if not want:
            newest = f"{periods[0].year:04d}-{periods[0].month:02d}"
            raise LodgingFileError(
                f"{month} は{dataset.name}に未収録です（最新は {newest}）"
            )
        period = want[0]
    else:
        period = periods[0]  # list_periods は新しい順

    files = estat_files.list_files(
        dataset.tstat, cycle_code, period, use_browser=use_browser
    )
    data = [f for f in files if f.file_kind == estat_files.FILE_KIND_DATA]
    if not data:
        raise LodgingFileError(f"{period.label} にダウンロード可能なファイルがありません")

    label = f"lodging_{period.year:04d}-{period.month:02d}"
    path = estat_files.download(data[0], cache_dir, use_browser=use_browser, label=label)
    with xlsx.Workbook(path) as book:
        result = read_fromto(
            book, year=period.year, month=period.month, area=pref_name
        )

    prov = Provenance(
        dataset=dataset.name,
        cycle=cycle_label,
        # 対象施設は月によって変わる（20室以上/200室以上）ので実物の記載を使う
        table_label=f"居住地（47区分）別延べ宿泊者数（{result.scope}）",
        area_label=pref_name,
        files=[path.name],
        sheets=[result.sheet],
    )
    prov.year_month = f"{period.year:04d}-{period.month:02d}"
    return result, prov


def available_fromto_months(*, use_browser: bool = False) -> list[str]:
    """居住地別From-toが取れる年月（新しい順）。"""
    datasets = estat_files.list_datasets(use_browser=use_browser)
    dataset = estat_files.find_dataset(datasets, *PREFER_FROMTO)
    if dataset is None:
        return []
    out: list[str] = []
    for cycle in estat_files.list_cycles(dataset.tstat, use_browser=use_browser):
        for p in estat_files.list_periods(
            dataset.tstat, cycle.code, use_browser=use_browser
        ):
            if p.month is not None:
                out.append(f"{p.year:04d}-{p.month:02d}")
    return sorted(set(out), reverse=True)


def available_years(*, city: bool = False, use_browser: bool = False) -> list[int]:
    """ファイル配布で取得できる年の一覧（降順）。"""
    datasets = estat_files.list_datasets(use_browser=use_browser)
    years: set[int] = set()
    for keyword in PREFER_CITY if city else PREFER_PREF:
        dataset = estat_files.find_dataset(datasets, keyword)
        if dataset is None:
            continue
        for cycle in estat_files.list_cycles(dataset.tstat, use_browser=use_browser):
            years.update(
                p.year
                for p in estat_files.list_periods(
                    dataset.tstat, cycle.code, use_browser=use_browser
                )
            )
    return sorted(years, reverse=True)
