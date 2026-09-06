"""国勢調査「従業地・通学地集計」（e-Stat API）から、自治体名単位の流入元を取り出す。

使用する統計表:
  「人口 男女，就業・通学，常住地（全国，都道府県，市区町村）別就業者・通学者数
    －全国［総数］，都道府県，市区町村（従業地・通学地）」
  statsDataId = 0003454525（令和2年国勢調査 従業地・通学地集計）

`area`（従業地・通学地）に米沢市を指定し、`cat03`（常住地）を市区町村単位で
展開することで、「どの自治体から米沢市へ来ているか」を自治体名つきで得られる。

## この指標が何であるか（重要）

得られる値は **通勤・通学者数**（2020年10月時点、5年に1度）であって、
観光を含む月次の「滞在人口」ではない。
RESAS旧サイトの「From-to分析（滞在人口）」は提供終了しており、
現行RESASにFrom-to分析機能は存在しない（2026-09時点でバンドルを確認済み）。
自治体名単位の流入元を公的APIから自動取得できるのは、現状この国勢調査系のみ。

月次の滞在人口が必要な場合は mlit_jinryu（発地4区分どまり）か、
外部で入手したCSVを manual_import で取り込むこと。

## 二重計上の回避

`cat03` の分類は階層になっている:
  level 1 = 全国 / level 2 = 都道府県 / level 4 = 市・特別区部・特別区・政令市
  level 5 = 政令市の区 / level 6 = 町村
市区町村として採用するのは「level が 4 か 6 かつ 親が都道府県(level 2)」のみ。
これにより政令市の区（level 5）と、東京23区（level 4 だが親が特別区部）が除かれ、
合算値が「他市区町村に常住」の総数と完全に一致する。
"""

from __future__ import annotations

import logging

from .estat_lodging import EstatError, _call, _class_objs, _classes

log = logging.getLogger(__name__)

STATS_DATA_ID = "0003454525"
SOURCE_URL = (
    "https://www.e-stat.go.jp/stat-search/database"
    f"?layout=dataset&statdisp_id={STATS_DATA_ID}"
)
SURVEY_LABEL = "令和2年（2020年）国勢調査 従業地・通学地集計"

# 分類コード（このstatsDataId固有）
_CAT_SEX_TOTAL = "0"  # 男女総数
_CAT_RESIDENCE_OTHER = "02"  # 他市区町村に常住
_CAT_WORKSTUDY_TOTAL = "0"  # 就業・通学 総数
_TOTAL_CODE = "00000"  # cat03 の「全国」= 他市区町村に常住の総数

# e-Statの表記が分かりにくいものだけ読みやすい名前に寄せる
_LABEL_OVERRIDES = {"13100": "東京都区部"}


def _municipality_filter(cat03_classes: list[dict]):
    """市区町村として採用してよいコードの判定関数と、コード→名称の辞書を返す。"""
    info = {c["@code"]: c for c in cat03_classes}
    level = {c["@code"]: c.get("@level") for c in cat03_classes}

    def is_municipality(code: str) -> bool:
        c = info.get(code)
        if c is None or c.get("@level") not in ("4", "6"):
            return False
        # 親が都道府県(level 2)のものだけ。東京23区は親が特別区部(level 4)なので除外。
        return level.get(c.get("@parentCode")) == "2"

    names = {
        code: _LABEL_OVERRIDES.get(code, c["@name"]) for code, c in info.items()
    }
    return is_municipality, names


def fetch_inflow(
    app_id: str, city_code: str
) -> tuple[dict[str, int], int]:
    """1自治体分の流入元を取得する。

    戻り値は ({常住地コード: 人数}, 他市区町村に常住の総数)。
    """
    meta = _call("getMetaInfo", {"appId": app_id, "statsDataId": STATS_DATA_ID})
    objs = _class_objs(meta)
    if "cat03" not in objs:
        raise EstatError("常住地(cat03)の分類が見つかりません。統計表の構成が変わった可能性があります。")
    is_municipality, names = _municipality_filter(_classes(objs["cat03"]))

    data = _call(
        "getStatsData",
        {
            "appId": app_id,
            "statsDataId": STATS_DATA_ID,
            "cdArea": city_code,
            "cdCat01": _CAT_SEX_TOTAL,
            "cdCat02": _CAT_RESIDENCE_OTHER,
            "cdCat04": _CAT_WORKSTUDY_TOTAL,
            "metaGetFlg": "N",
            "cntGetFlg": "N",
            "limit": "100000",
        },
    )
    values = data["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    by_code: dict[str, int] = {}
    total_other = 0
    for v in values:
        code = v.get("@cat03", "")
        try:
            num = int(v["$"])
        except (KeyError, ValueError):  # "-" は該当なし
            continue
        if code == _TOTAL_CODE:
            total_other = num
        elif is_municipality(code) and num > 0:
            by_code[code] = num

    if not by_code:
        raise EstatError(
            f"{city_code} の流入元が取得できませんでした。estat-meta で分類を確認してください。"
        )

    covered = sum(by_code.values())
    if total_other and covered != total_other:
        log.warning(
            "市区町村合算 %d が総数 %d と一致しません（差 %d）",
            covered,
            total_other,
            total_other - covered,
        )
    return by_code, total_other or covered


def fromto_rows(
    app_id: str,
    target_areas: dict[str, str],
    *,
    top: int = 10,
) -> tuple[list[tuple[str, int]], int]:
    """resas-fromto.csv 用の (流入元, 人数) を降順で返す。

    top > 0 のとき上位top件に絞り、残りを「その他」に畳む。
    複数自治体を対象にした場合は合算し、対象自治体自身は流入元から除く。
    戻り値は (行, 対象自治体の流入合計)。
    """
    meta = _call("getMetaInfo", {"appId": app_id, "statsDataId": STATS_DATA_ID})
    _, names = _municipality_filter(_classes(_class_objs(meta)["cat03"]))

    # 設定した市区町村コードと公式名称の突き合わせ。
    # コードの取り違え（例: 06206は寒河江市であって長井市ではない）を黙って通さない。
    for code, label in target_areas.items():
        official = names.get(code)
        if official is None:
            raise EstatError(f"市区町村コード {code}（{label}）が統計表に存在しません。")
        if official != label:
            raise EstatError(
                f"市区町村コードの不一致: {code} は公式には「{official}」だが "
                f"「{label}」として設定されている。areas.py を修正すること。"
            )

    merged: dict[str, int] = {}
    grand_total = 0
    for code in target_areas:
        by_code, total_other = fetch_inflow(app_id, code)
        grand_total += total_other
        for origin, num in by_code.items():
            merged[origin] = merged.get(origin, 0) + num

    # 対象自治体どうしの往来は「流入元」から外す（置賜など複数指定時）
    for code in target_areas:
        removed = merged.pop(code, 0)
        if removed:
            grand_total -= removed

    ranked = sorted(
        ((names.get(c, c), n) for c, n in merged.items()),
        key=lambda kv: (-kv[1], kv[0]),
    )
    if top and top > 0 and len(ranked) > top:
        head = ranked[:top]
        rest = grand_total - sum(n for _, n in head)
        if rest > 0:
            head.append(("その他", rest))
        ranked = head
    return ranked, grand_total
