"""現行RESASの「クレジットカード消費額分析」を取得する（Visa加工データ）。

旧 RESAS-API（opendata.resas-portal.go.jp）は2025-03-24に終了しているが、
**2026-06-18のRESAS刷新で「クレジットカード消費地分析／消費額分析」が新設され**、
その画面は ``https://api.resas.go.jp/v2/`` のJSONを直接叩いている。
接続まわり（403を避けるヘッダ・応答の剥がし方）は ``resas_api`` にある。

つまりこの3種（消費地別／費目別／国内旅行者の費目別）だけは
**代替物ではなく現行RESASそのものの値**である。他のsourceとはそこが違う。

## 返ってくるのはグラフ（ECharts option）であってデータ表ではない

画面はグラフ描画ライブラリに ``optionUrl`` を渡す作りなので、
レスポンスは ``{"option": {...}}`` という描画設定である。値そのものは入っているが、

- 円グラフ（費目別）は ``series[0].data[i]`` の ``value`` が実数、
  ``name`` が ``"宿泊費 14,090万円 (14.98%)"`` という表示用文字列。
  **ラベルは name の先頭（最初の空白まで）から取る**。
- 棒グラフ（消費地別）は ``xAxis.data`` がラベル、``series[0].data[i].value`` が実数。
- 単位は円グラフなら ``title[0].text``（"94,090万円"）、棒グラフなら ``yAxis.name``（"（万円）"）。
- **データが無い期間は 200 で ``{"option": {}}`` が返る**（404ではない）。

分類名や単位をハードコードせず毎回レスポンスから読むのは、この方針のため。

## 収録範囲（実測 2026-09）

2025年（1-12月・四半期・通年）と2026年 第1四半期（1-3月）のみ。
2024年以前は空。四半期ごとの更新なので、天井は実行のたびに動く。
``available_periods()`` が実際に叩いて確かめる。

## 出典表記

データは「Visa Consulting & Analytics により加工・分析されたデータ」であり、
JCB／ナウキャストの旧・消費マップとは別物。旧RESASの値とは接続しない。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import resas_api

log = logging.getLogger(__name__)

SOURCE_URL = "https://resas.go.jp/tourism-credit-consumption-amount"
PLACE_URL = "https://resas.go.jp/tourism-credit-consumption-place"
PROVIDER = "Visa Consulting & Analytics により加工・分析されたデータ"

# 画面の選択肢。値はJSバンドルの定義に合わせてある（勝手な値を送ると422）。
VISITORS: dict[str, str] = {"domestic": "国内旅行", "overseas": "訪日旅行"}
CALC_TYPES: dict[str, str] = {"sum": "消費総額", "unit": "消費単価"}
AREA_LEVELS: dict[str, str] = {
    "all": "全国",
    "pref": "都道府県単位",
    "city": "市区町村単位",
}
QUARTERS: dict[str, str] = {
    "quarter1": "1-3月期",
    "quarter2": "4-6月期",
    "quarter3": "7-9月期",
    "quarter4": "10-12月期",
}
PERIODS: dict[str, str] = {"all": "通年", **QUARTERS}
PERIODS.update({str(m): f"{m}月" for m in range(1, 13)})

# 「全国籍・地域」。訪日旅行では国・地域を絞れるが、既定は合算。
ALL_COUNTRIES = "00"


class VisaError(resas_api.ResasApiError):
    """RESAS クレジットカード分析APIから期待した形の値が取れなかった。"""


@dataclass
class Provenance:
    """sidecar（.md）に書く出典・集計条件。"""

    menu: str = ""
    url: str = ""
    area_label: str = ""
    visitor_label: str = ""
    period_label: str = ""
    calc_label: str = ""
    unit: str = ""
    year: str = ""
    total: int = 0
    # RESAS画面が表示している総額。CSVは行ごとに丸めた整数なので、
    # 各行の和（total）とは1単位ずれることがある。ずれたらsidecarに明記する。
    reported_total: int | None = None
    covered_periods: list[str] = field(default_factory=list)


def _check(name: str, value: str, allowed: dict[str, str]) -> str:
    if value not in allowed:
        raise VisaError(
            f"{name} に指定できるのは {'・'.join(allowed)} のみです（指定値: {value!r}）"
        )
    return value


def _query(**params: str) -> str:
    return "&".join(f"{k}={v}" for k, v in params.items())


_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")
_UNIT_RE = re.compile(r"[\d,.]+\s*(\S+)$")


def _parse_total(text: str) -> tuple[float, str]:
    """円グラフ中央の "94,090万円" を (94090.0, "万円") に分解する。"""
    num = _NUM_RE.search(text)
    unit = _UNIT_RE.search(text.strip())
    if not num or not unit:
        raise VisaError(f"合計値を読み取れません: {text!r}")
    return float(num.group().replace(",", "")), unit.group(1)


def _axis_unit(option: dict) -> str:
    """棒グラフのy軸名 "（万円）" から単位を取り出す。"""
    name = str(option.get("yAxis", {}).get("name", ""))
    return name.strip("（）()") or "円"


def _pie_items(option: dict) -> list[tuple[str, float]]:
    """円グラフの系列を [(分類名, 値)] にする。

    ``name`` は "宿泊費 14,090万円 (14.98%)" という表示文字列なので、
    **最初の空白より前**を分類名として採る。分類名自体に空白は入らない。
    """
    try:
        data = option["series"][0]["data"]
    except (KeyError, IndexError) as exc:
        raise VisaError("円グラフの系列が見つかりません") from exc
    out: list[tuple[str, float]] = []
    for item in data:
        label = str(item.get("name", "")).split(" ", 1)[0].strip()
        if not label:
            raise VisaError(f"分類名を読み取れません: {item!r}")
        out.append((label, float(item["value"])))
    return out


def _bar_items(option: dict) -> list[tuple[str, float]]:
    """棒グラフを [(ラベル, 値)] にする。"""
    labels = option.get("xAxis", {}).get("data")
    try:
        data = option["series"][0]["data"]
    except (KeyError, IndexError) as exc:
        raise VisaError("棒グラフの系列が見つかりません") from exc
    if not labels or len(labels) != len(data):
        raise VisaError(
            f"ラベル数({len(labels or [])})と値の数({len(data)})が一致しません"
        )
    out: list[tuple[str, float]] = []
    for label, item in zip(labels, data):
        value = item["value"] if isinstance(item, dict) else item
        out.append((str(label).replace("\n", ""), float(value)))
    return out


def _round_keeping_total(
    items: list[tuple[str, float]], total: float
) -> tuple[list[tuple[str, int]], int]:
    """各行を整数に丸め、丸め後の合計も返す。

    合計は「丸めた行の総和」であって元の総額の丸めではない。
    こうしておかないと「その他」で辻褄を合わせたときに合計がずれる。
    """
    rows = [(name, int(round(value))) for name, value in items]
    return rows, sum(v for _, v in rows)


def _assert_consistent(items: list[tuple[str, float]], total: float, what: str) -> None:
    """内訳の合計が総額と一致することを確かめる（集計列を拾ったバグの検出）。"""
    if total <= 0:
        return
    got = sum(v for _, v in items)
    if abs(got - total) / total > 0.005:
        raise VisaError(
            f"{what}: 内訳合計 {got:,.0f} が総額 {total:,.0f} と一致しません"
            "（APIの応答仕様が変わった可能性）"
        )


# --------------------------------------------------------------- 費目別（大分類）


def category_rows(
    *,
    year: str,
    period: str,
    visitor: str = "domestic",
    area_level: str = "city",
    pref_code: str,
    city_code: str,
    calc_type: str = "sum",
) -> tuple[list[tuple[str, int]], Provenance]:
    """費目（大分類）別の消費総額を返す。

    RESAS「クレジットカード消費額分析」の円グラフ（費目別）と同じ値。
    大分類は 宿泊費／飲食費／交通費／娯楽等サービス費／買物代／その他 の6区分だが、
    **区分名はレスポンスから読む**（表の改訂で増減しても壊れないようにするため）。
    """
    _check("visitor", visitor, VISITORS)
    _check("period", period, PERIODS)
    _check("area_level", area_level, AREA_LEVELS)
    _check("calc_type", calc_type, CALC_TYPES)
    option = resas_api.fetch_option(
        "tourism/visa-spending/category-circle-bar?"
        + _query(
            visitor=visitor,
            areaLevel=area_level,
            pref=pref_code,
            city=city_code,
            year=year,
            month=period,
            dayType="all",
            timeZone="all",
            categoryLevel="broad",
            broadCategory="0",
            middleCategory="0",
            country=ALL_COUNTRIES,
            calcType=calc_type,
        )
    )
    if not option:
        raise VisaError(
            f"{year}年 {PERIODS[period]} のデータは未収録です"
            "（四半期ごとの更新。resas-visa-meta で収録範囲を確認できます）"
        )
    items = _pie_items(option)
    total, unit = _parse_total(str(option["title"][0]["text"]))
    _assert_consistent(items, total, "費目別")
    rows, rounded_total = _round_keeping_total(items, total)
    prov = Provenance(
        menu="クレジットカード消費額分析（費目別）",
        url=SOURCE_URL,
        visitor_label=VISITORS[visitor],
        period_label=PERIODS[period],
        calc_label=CALC_TYPES[calc_type],
        unit=unit,
        year=year,
        total=rounded_total,
        reported_total=int(round(total)),
    )
    return rows, prov


# --------------------------------------------------------------------- 消費地別


def place_rows(
    *,
    year: str,
    period: str,
    visitor: str = "domestic",
    area_level: str = "city",
    pref_code: str,
    city_code: str,
    calc_type: str = "sum",
) -> tuple[list[tuple[str, int]], Provenance]:
    """消費地別（市区町村または都道府県）の消費総額を降順で返す。

    ``area_level="city"`` だと ``pref_code`` の県内全市区町村、
    ``"pref"`` だと47都道府県が返る（画面のランキング棒グラフと同じ）。
    APIの並びは対象地域を先頭に置いた表示順なので、ここで降順に並べ替える。
    """
    _check("visitor", visitor, VISITORS)
    _check("period", period, PERIODS)
    _check("area_level", area_level, AREA_LEVELS)
    _check("calc_type", calc_type, CALC_TYPES)
    option = resas_api.fetch_option(
        "tourism/visa-spending/to-bar?"
        + _query(
            visitor=visitor,
            areaLevel=area_level,
            pref=pref_code,
            city=city_code,
            year=year,
            month=period,
            dayType="all",
            timeZone="all",
            categoryLevel="broad",
            broadCategory="0",
            middleCategory="0",
            country=ALL_COUNTRIES,
            calcType=calc_type,
        )
    )
    if not option:
        raise VisaError(
            f"{year}年 {PERIODS[period]} のデータは未収録です"
            "（四半期ごとの更新。resas-visa-meta で収録範囲を確認できます）"
        )
    items = sorted(_bar_items(option), key=lambda kv: kv[1], reverse=True)
    rows, total = _round_keeping_total(items, sum(v for _, v in items))
    prov = Provenance(
        menu="クレジットカード消費額分析（消費地別）",
        url=SOURCE_URL,
        visitor_label=VISITORS[visitor],
        period_label=PERIODS[period],
        calc_label=CALC_TYPES[calc_type],
        unit=_axis_unit(option),
        year=year,
        total=total,
    )
    return rows, prov


# ------------------------------------------------------------------ 収録範囲の探索


def has_data(
    *, year: str, period: str, visitor: str, pref_code: str, city_code: str
) -> bool:
    """その年・期間に値があるか（空の ``option`` が返らないか）を確かめる。"""
    option = resas_api.fetch_option(
        "tourism/visa-spending/category-circle-bar?"
        + _query(
            visitor=visitor,
            areaLevel="city",
            pref=pref_code,
            city=city_code,
            year=year,
            month=period,
            dayType="all",
            timeZone="all",
            categoryLevel="broad",
            broadCategory="0",
            middleCategory="0",
            country=ALL_COUNTRIES,
            calcType="sum",
        )
    )
    return bool(option)


def available_periods(
    *, year: str, visitor: str, pref_code: str, city_code: str
) -> list[str]:
    """その年に収録されている四半期を返す（1回の判定につき1リクエスト）。

    四半期しか見ないのは、月まで確かめると1年で12回叩くことになるため。
    月次が要るときは ``--cc-period 8`` のように直接指定すれば足りる。
    """
    return [
        q
        for q in QUARTERS
        if has_data(
            year=year,
            period=q,
            visitor=visitor,
            pref_code=pref_code,
            city_code=city_code,
        )
    ]


def resolve_year(
    *, year: int, visitor: str, pref_code: str, city_code: str, back: int = 3
) -> tuple[str, list[str]]:
    """指定年に値が無ければ収録最新年まで遡る。(年, 収録四半期) を返す。

    他のsourceの ``select_year_months()`` と同じ考え方だが、
    こちらは収録年の一覧を返すAPIが無いので実際に叩いて確かめる。
    """
    for candidate in range(year, year - back - 1, -1):
        quarters = available_periods(
            year=str(candidate),
            visitor=visitor,
            pref_code=pref_code,
            city_code=city_code,
        )
        if quarters:
            if candidate != year:
                log.warning(
                    "%d年のクレジットカード消費額データは未収録のため、%d年に切り替えます",
                    year,
                    candidate,
                )
            return str(candidate), quarters
    raise VisaError(
        f"{year - back}〜{year}年にクレジットカード消費額データが見つかりません"
        f"（対象: {pref_code}/{city_code}）"
    )


def describe(*, pref_code: str, city_code: str, back: int = 3) -> str:
    """収録年・四半期を一覧する（`resas-visa-meta` の出力）。"""
    import datetime

    lines = [
        "RESAS クレジットカード消費額分析（Visa加工データ）の収録範囲",
        f"  対象: pref={pref_code} city={city_code}",
        f"  出典: {PROVIDER}",
        f"  画面: {SOURCE_URL}",
        "",
    ]
    this_year = datetime.date.today().year
    for visitor, label in VISITORS.items():
        lines.append(f"[{label}]")
        found = False
        for year in range(this_year, this_year - back - 1, -1):
            quarters = available_periods(
                year=str(year),
                visitor=visitor,
                pref_code=pref_code,
                city_code=city_code,
            )
            if quarters:
                found = True
                lines.append(
                    f"  {year}年: " + "・".join(QUARTERS[q] for q in quarters)
                )
        if not found:
            lines.append("  収録なし")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ sidecar 用の注記

# 画面の「データについて」に出る公式注記。CSVには入れず .md に転記する。
OFFICIAL_NOTES: list[str] = [
    "訪日客も含め、日本円に統一している。",
    "対象は各目的地での対面決済のみで、タッチ決済・差し込み決済等の手法は問わない。",
    "消費総額について、VISAによる決済の割合を補正するため、入込人数と同等の補正をかけている。",
    "入込人数について、「平日と土日」別、「時間帯」別、「分類」別に数える場合は重複が発生する。",
    "国内旅行者：日本国内在住者で居住地から片道30km以上離れた決済を対象とする。"
    "なお、一定頻度の決済がある場合は、生活圏の決済と見做し、対象外としている。",
    "訪日外国人旅行者：日本以外の居住者で27の国・地域が対象となる。"
    "地域によって滞在日数の上限値を定めている。",
    "国内旅行者について、居住地は推定居住地である。決済の頻度等から推定している。",
    "VISAによる決済の割合を補正するため、訪日旅行者は「JNTO 訪日外客統計」、"
    "国内旅行者は「観光庁 旅行・観光消費動向調査」を参考値として利用している。",
    "本メニューのデータは Visa Consulting & Analytics により推計された値であり、"
    "カード発行会社ではないため本データに個人情報は含まれない。",
]
