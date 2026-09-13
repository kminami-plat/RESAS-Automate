"""現行RESASの「国内観光消費分析」からCSVをそのまま落とす。

出力は `resas-spend-per-trip.csv`（一人一回当たりの旅行単価／購入者単価）。
画面の**ダウンロードボタンと同じもの**を取る:

    https://api.resas.go.jp/v2/download/tourism/tourism-domestic/graph?…

元データは **観光庁「旅行・観光消費動向調査」**（RESASが集計・整形したもの）。
Visaのクレジットカードデータ（`resas_visa`）とは別系列なので混同しないこと。

## 他のsourceと違って「配信元のCSVをそのまま通す」

滞在人口や宿泊者数のように値を組み立て直すのではなく、
RESASが出すCSVの列をそのまま `data/raw/resas/` に置く。
やっていることは3つだけ:

1. ZIPを開く（応答は ``application/zip``。中身はShift_JISのCSV1本）
2. 見出しが `writers.HEADERS["spend-per-trip"]` と一致するか検証する
3. UTF-8 BOM付きで書き直す（ダッシュボード側が `utf-8-sig` で読むため）

**見出しの最後の列だけは可変**（`単価（宿泊中）` / `単価（日帰り）`）。
旅行種類の選択で変わるので、そこだけ「`単価` で始まること」しか見ない。

## 収録範囲（実測 2026-09）

**2023〜2025年**。範囲外の年を指定しても**エラーにはならず、見出しだけのCSVが返る**
（289バイト・1行）。空振りに気付けるよう、データ行が0件なら未収録として扱う。

## パラメータ

- ``year``       2023 / 2024 / 2025
- ``quarter``    ``year``（すべての期間）/ ``1-3`` / ``4-6`` / ``7-9`` / ``10-12``
- ``travelType`` ``during_day_price``（宿泊旅行）/ ``day_trip_price``（日帰り旅行）
- ``costType``   ``0``（一人一回当たり旅行単価）/ ``1``（一人一回当たり購入者単価）

``transition`` 経路（``frequency=year|quarter``）にすると、単年ではなく
**2023〜2025年をまとめた推移**が1本のCSVで返る。年をまたいだ比較をしたいときはこちら。

不正値には422と一緒に許容値の一覧が返るので、仕様確認はそれが速い。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import resas_api

log = logging.getLogger(__name__)

MENU = "国内観光消費分析"
SOURCE_URL = "https://resas.go.jp/tourism-domestic"
PROVIDER = "観光庁「旅行・観光消費動向調査」"

# 画面の選択肢。値はJSバンドルの定義に合わせてある（勝手な値を送ると422）。
YEARS: tuple[str, ...] = ("2023", "2024", "2025")
QUARTERS: dict[str, str] = {
    "year": "すべての期間",
    "1-3": "1-3月期",
    "4-6": "4-6月期",
    "7-9": "7-9月期",
    "10-12": "10-12月期",
}
TRAVEL_TYPES: dict[str, str] = {
    "during_day_price": "宿泊旅行",
    "day_trip_price": "日帰り旅行",
}
COST_TYPES: dict[str, str] = {
    "0": "一人一回当たり旅行単価",
    "1": "一人一回当たり購入者単価",
}
FREQUENCIES: dict[str, str] = {"year": "年次ごと", "quarter": "四半期ごと"}

# 画面の「データについて」に出る公式注記。CSVには入れず .md に転記する。
OFFICIAL_NOTES: list[str] = [
    "旅行単価：旅行１回当たりの支出金額",
    "購入者単価：ある品目を購入した人を分母として算出される、"
    "その品目を購入する際に支払った支出金額の平均値",
]

# 見出しの最後の列だけは旅行種類で変わる（単価（宿泊中）/ 単価（日帰り））。
_VALUE_COLUMN_PREFIX = "単価"


class TourismDomesticError(resas_api.ResasApiError):
    """国内観光消費分析のCSVが期待した形で取れなかった。"""


@dataclass
class Provenance:
    """sidecar（.md）に書く出典・集計条件。"""

    menu: str = MENU
    url: str = SOURCE_URL
    provider: str = PROVIDER
    source_file: str = ""
    travel_label: str = ""
    cost_label: str = ""
    period_label: str = ""
    years: list[str] = None  # type: ignore[assignment]
    rows: int = 0

    def __post_init__(self) -> None:
        if self.years is None:
            self.years = []


def _check(name: str, value: str, allowed) -> str:
    if value not in allowed:
        raise TourismDomesticError(
            f"{name} に指定できるのは {'・'.join(allowed)} のみです（指定値: {value!r}）"
        )
    return value


def _download(uri: str) -> tuple[str, list[list[str]]]:
    """ダウンロードURIを叩いて (ZIP内のファイル名, 行のリスト) を1本だけ返す。"""
    files = resas_api.download_csvs(uri)
    if len(files) != 1:
        raise TourismDomesticError(
            f"CSVが1本である想定ですが {len(files)}本ありました: "
            + "・".join(name for name, _ in files)
        )
    return files[0]


def _split(
    rows: list[list[str]], expected_header: list[str], source_file: str
) -> tuple[list[str], list[list[str]]]:
    """見出しを検証して (見出し, データ行) に分ける。

    列の並びが変わったら黙って通さない。ダッシュボード側が列名で引いているので、
    ここで止めないと「読めているのに中身が違う」状態になる。
    """
    if not rows:
        raise TourismDomesticError(f"CSVが空です: {source_file}")
    header = [c.strip() for c in rows[0]]
    if len(header) != len(expected_header):
        raise TourismDomesticError(
            f"列数が {len(header)} で、期待は {len(expected_header)}: {header}"
        )
    fixed, last = expected_header[:-1], header[-1]
    if header[:-1] != fixed:
        raise TourismDomesticError(
            f"見出しが期待と違います。期待: {fixed} / 実際: {header[:-1]}"
        )
    if not last.startswith(_VALUE_COLUMN_PREFIX):
        raise TourismDomesticError(
            f"最終列が『{_VALUE_COLUMN_PREFIX}…』ではありません: {last!r}"
        )
    return header, [row for row in rows[1:] if any(c.strip() for c in row)]


def fetch_spend_per_trip(
    *,
    expected_header: list[str],
    year: str = "2025",
    quarter: str = "year",
    travel_type: str = "during_day_price",
    cost_type: str = "0",
    transition: str | None = None,
) -> tuple[list[str], list[list[str]], Provenance]:
    """一人一回当たりの単価CSVを取得して (見出し, データ行, 出典) を返す。

    ``transition`` に ``year`` / ``quarter`` を渡すと単年ではなく
    2023〜2025年の推移が1本で返る（このとき ``year`` / ``quarter`` は使われない）。
    """
    _check("travel_type", travel_type, TRAVEL_TYPES)
    _check("cost_type", cost_type, COST_TYPES)
    if transition:
        _check("transition", transition, FREQUENCIES)
        uri = (
            "/tourism/tourism-domestic/transition?"
            f"&frequency={transition}&travelType={travel_type}&costType={cost_type}"
        )
        period_label = f"2023〜2025年の推移（{FREQUENCIES[transition]}）"
    else:
        _check("quarter", quarter, QUARTERS)
        uri = (
            "/tourism/tourism-domestic/graph?"
            f"year={year}&quarter={quarter}"
            f"&travelType={travel_type}&costType={cost_type}"
        )
        period_label = f"{year}年 {QUARTERS[quarter]}"

    source_file, raw = _download(uri)
    header, data = _split(raw, expected_header, source_file)
    if not data:
        raise TourismDomesticError(
            f"{period_label} のデータは未収録です（収録範囲は {YEARS[0]}〜{YEARS[-1]}年）"
        )
    prov = Provenance(
        source_file=source_file,
        travel_label=TRAVEL_TYPES[travel_type],
        cost_label=COST_TYPES[cost_type],
        period_label=period_label,
        years=sorted({row[0].strip() for row in data if row and row[0].strip()}),
        rows=len(data),
    )
    log.info("%s: %s（%d行）", MENU, source_file, len(data))
    return header, data, prov


def fetch_latest(
    *,
    year: int,
    expected_header: list[str],
    quarter: str = "year",
    travel_type: str = "during_day_price",
    cost_type: str = "0",
    back: int = 3,
) -> tuple[list[str], list[list[str]], Provenance, str]:
    """指定年から遡って、最初に値が取れた年のCSVを返す。

    範囲外の年でもエラーではなく**見出しだけのCSV**が返るので、
    「データ行が0件かどうか」でしか判定できない。取得と判定を分けると
    同じZIPを2回落とすことになるので、ここで一度に済ませる。
    戻り値の最後は実際に採用した年。
    """
    last: TourismDomesticError | None = None
    for candidate in range(year, year - back - 1, -1):
        try:
            header, data, prov = fetch_spend_per_trip(
                expected_header=expected_header,
                year=str(candidate),
                quarter=quarter,
                travel_type=travel_type,
                cost_type=cost_type,
            )
        except TourismDomesticError as exc:
            last = exc
            continue
        if candidate != year:
            log.warning(
                "%d年の国内観光消費分析は未収録のため、%d年に切り替えます", year, candidate
            )
        return header, data, prov, str(candidate)
    raise TourismDomesticError(
        f"{year - back}〜{year}年に国内観光消費分析のデータが見つかりません"
        f"（収録範囲は {YEARS[0]}〜{YEARS[-1]}年）"
    ) from last
