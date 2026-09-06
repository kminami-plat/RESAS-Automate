"""市区町村コードと地域定義（山形県・置賜地域）。

市区町村コードは総務省の全国地方公共団体コード（5桁、検査数字なし）。
国交省 人流オープンデータの ``citycode`` 列と同じ体系。
"""

from __future__ import annotations

PREF_CODE = "06"
PREF_NAME = "山形県"

# 置賜地域の8市町（米沢市を含む）
# コードは総務省 全国地方公共団体コードで検証済み（06206は寒河江市であり長井市ではない）。
OKITAMA: dict[str, str] = {
    "06202": "米沢市",
    "06209": "長井市",
    "06213": "南陽市",
    "06381": "高畠町",
    "06382": "川西町",
    "06401": "小国町",
    "06402": "白鷹町",
    "06403": "飯豊町",
}

# 対象地域は「山形県米沢市」で固定。
TARGET_CITY_CODE = "06202"
TARGET_CITY_NAME = "米沢市"
TARGET_AREA_LABEL = f"{PREF_NAME}{TARGET_CITY_NAME}"

YONEZAWA: dict[str, str] = {TARGET_CITY_CODE: TARGET_CITY_NAME}

# 既定は米沢市固定。置賜8市町は参考用の明示オプション（--areas okitama）。
AREA_PRESETS: dict[str, dict[str, str]] = {
    "yonezawa": YONEZAWA,
    "okitama": OKITAMA,
}

# 人流オープンデータ from_area コードの意味（定義書より）
FROM_AREA_LABELS: dict[str, str] = {
    "0": "同市区町村",
    "1": "同都道府県（山形県内）",
    "2": "同地方（東北地方）",
    "3": "それ以外",
}

# dayflag / timezone コード
DAYFLAG_LABELS = {"0": "全日", "1": "平日", "2": "休日"}
TIMEZONE_LABELS = {"0": "終日", "1": "昼", "2": "夜"}


def resolve_areas(preset: str) -> dict[str, str]:
    try:
        return AREA_PRESETS[preset]
    except KeyError:
        raise SystemExit(
            f"未知のエリア指定 '{preset}'。指定可能: {', '.join(AREA_PRESETS)}"
        ) from None
