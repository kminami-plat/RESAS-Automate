"""観光庁「宿泊旅行統計調査」（e-Stat API）から月次の延べ宿泊者数を取り出す。

使用する統計表: 参考第３表
  「年、月（12区分）、施設所在地(47区分及び運輸局等)、従業者数(3区分)、
    宿泊目的割合(2区分)別延べ宿泊者数」  statsDataId = 0003314421
この表は「従業者数」の分類に「延べ宿泊者数」（総数）と
「うち外国人延べ宿泊者数」を両方含むため、1回の取得で揃えられる。

重要な制約: このstatsDataIdで**API取得できるのは2014年〜2016年分のみ**。
2017年以降の宿泊旅行統計調査（速報値・確定値）はe-Statに
「統計データベース」として登録されておらず、Excel/CSVのファイル配布
（https://www.e-stat.go.jp/stat-search/files?toukei=00601020）のみで、
getStatsData API からは取得できない。
2017年以降の値が必要な場合は、上記ページから対象月のファイルを手動DLし、
`python -m resas_automate manual --kind lodging` で変換すること。

前提: 宿泊旅行統計調査の公表単位は都道府県（47区分）であり、
      米沢市など市区町村単位の月次宿泊者数は全国統計としては公表されていない。
      したがって既定では山形県の値を出力する。

APIキー: https://www.e-stat.go.jp/api/ で無料登録し、環境変数 ESTAT_APP_ID に設定する。
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from ..http import fetch_json

log = logging.getLogger(__name__)

API_BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"
STATS_DATA_ID = "0003314421"
SOURCE_URL = (
    "https://www.e-stat.go.jp/stat-search/database"
    f"?layout=dataset&statdisp_id={STATS_DATA_ID}"
)
FILES_URL = "https://www.e-stat.go.jp/stat-search/files?toukei=00601020"

# 実測（2026-09時点）: このstatsDataIdの時間軸分類は2014-01〜2016-12のみ。
AVAILABLE_FROM = "2014-01"
AVAILABLE_TO = "2016-12"

_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


class EstatError(RuntimeError):
    pass


def _call(endpoint: str, params: dict[str, str]) -> dict:
    url = f"{API_BASE}/{endpoint}?" + urllib.parse.urlencode(params)
    payload = fetch_json(url)
    result = payload.get("GET_META_INFO") or payload.get("GET_STATS_DATA")
    if result is None:
        raise EstatError(f"想定外のレスポンス: {list(payload)}")
    status = result["RESULT"]["STATUS"]
    if status != 0:
        raise EstatError(f"e-Stat APIエラー status={status}: {result['RESULT']['ERROR_MSG']}")
    return result


def _class_objs(meta: dict) -> dict[str, dict]:
    """CLASS_OBJ を {@id: obj} で返す。"""
    objs = meta["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"]
    if isinstance(objs, dict):
        objs = [objs]
    return {o["@id"]: o for o in objs}


def _classes(obj: dict) -> list[dict]:
    cls = obj.get("CLASS", [])
    return [cls] if isinstance(cls, dict) else list(cls)


def _find_obj(objs: dict[str, dict], *keywords: str) -> dict | None:
    for obj in objs.values():
        name = obj.get("@name", "")
        if any(k in name for k in keywords):
            return obj
    return None


def _find_code(obj: dict, *keywords: str) -> str | None:
    for c in _classes(obj):
        if any(k in c.get("@name", "") for k in keywords):
            return c["@code"]
    return None


def dump_meta(app_id: str) -> str:
    """統計表の分類項目を人が読める形で返す（コード確認用）。"""
    meta = _call("getMetaInfo", {"appId": app_id, "statsDataId": STATS_DATA_ID})
    lines = [f"statsDataId={STATS_DATA_ID}"]
    for obj_id, obj in _class_objs(meta).items():
        lines.append(f"\n[{obj_id}] {obj.get('@name')}")
        for c in _classes(obj)[:60]:
            lines.append(f"    {c['@code']}\t{c['@name']}")
    return "\n".join(lines)


def fetch_lodging(
    app_id: str, *, pref_name: str = "山形県"
) -> list[tuple[str, int, int]]:
    """(年月, 宿泊者数, うち外国人) を年月の昇順で、API取得できる全期間分（2014-01〜2016-12）返す。

    期間の絞り込みは呼び出し側（cli）が行う。
    """
    meta = _call("getMetaInfo", {"appId": app_id, "statsDataId": STATS_DATA_ID})
    objs = _class_objs(meta)

    area_obj = _find_obj(objs, "施設所在地", "地域")
    if area_obj is None:
        raise EstatError("施設所在地の分類が見つかりません。estat-meta で確認してください。")
    area_code = _find_code(area_obj, pref_name)
    if area_code is None:
        raise EstatError(f"{pref_name} のコードが見つかりません。estat-meta で確認してください。")

    # 「従業者数」区分に総数（延べ宿泊者数）と「うち外国人延べ宿泊者数」が同居している。
    emp_obj = _find_obj(objs, "従業者数")
    if emp_obj is None:
        raise EstatError("従業者数の分類が見つかりません。estat-meta で確認してください。")
    total_code = _find_code(emp_obj, "延べ宿泊者数")
    foreign_code = _find_code(emp_obj, "外国人")
    if total_code is None or foreign_code is None:
        raise EstatError("延べ宿泊者数／外国人区分の解決に失敗しました。estat-meta で確認してください。")

    purpose_obj = _find_obj(objs, "宿泊目的割合")
    purpose_total = _find_code(purpose_obj, "合計", "総数", "全体") if purpose_obj else None

    params: dict[str, str] = {
        "appId": app_id,
        "statsDataId": STATS_DATA_ID,
        "cdArea": area_code,
        "metaGetFlg": "N",
        "cntGetFlg": "N",
        "limit": "100000",
    }
    if purpose_obj and purpose_total:
        params[f"cd{purpose_obj['@id'].capitalize()}"] = purpose_total

    data = _call("getStatsData", params)
    values = data["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    emp_key = "@" + emp_obj["@id"]

    time_names = {}
    time_obj = _find_obj(objs, "時間軸")
    if time_obj:
        time_names = {c["@code"]: c["@name"] for c in _classes(time_obj)}

    # {年月: {従業者数区分コード: 値}}
    table: dict[str, dict[str, int]] = {}
    for v in values:
        label = time_names.get(v.get("@time", ""), v.get("@time", ""))
        m = _MONTH_RE.search(label)
        if not m:  # 年計の行は落とす
            continue
        ym = f"{m.group(1)}-{int(m.group(2)):02d}"
        raw = v.get("$", "")
        try:
            num = int(float(raw))
        except ValueError:  # "-" や "***" などの秘匿・欠測値
            continue
        code = v.get(emp_key, "")
        if code not in (total_code, foreign_code):
            continue  # 従業者数規模別の内訳行は使わない
        table.setdefault(ym, {})[code] = num

    rows: list[tuple[str, int, int]] = []
    for ym in sorted(table):  # 年月の昇順
        b = table[ym]
        if total_code not in b:
            continue
        rows.append((ym, b[total_code], b.get(foreign_code, 0)))

    if not rows:
        raise EstatError("該当する月次データが得られませんでした。")
    return rows
