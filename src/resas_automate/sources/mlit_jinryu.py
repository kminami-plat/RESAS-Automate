"""国交省「全国の人流オープンデータ（1kmメッシュ、市区町村単位発地別）」から
滞在人口とFrom-to（発地区分別）を取り出す。

配布元: G空間情報センター (https://www.geospatial.jp/ckan/dataset/mlit-1km-fromto)
対象期間: 2019年1月〜2021年12月（この期間より新しいデータは公開されていない）
滞在人口は「1か月間における1日あたりの平均値」。

市区町村単位発地別データの ``from_area`` は
0=同市区町村 / 1=同都道府県 / 2=同地方 / 3=それ以外 の4区分であり、
「山形市から何人」といった自治体名単位の流入元は含まれない。
自治体名単位のFrom-toが必要な場合は manual_import（RESAS画面からのCSV出力）を使う。
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path

from .. import areas
from ..http import fetch_cached, fetch_json

log = logging.getLogger(__name__)

CKAN_PACKAGE = "mlit-1km-fromto"
CKAN_API = "https://www.geospatial.jp/ckan/api/3/action/package_show?id=" + CKAN_PACKAGE
SOURCE_URL = "https://www.geospatial.jp/ckan/dataset/" + CKAN_PACKAGE

AVAILABLE_FROM = "2019-01"
AVAILABLE_TO = "2021-12"


def resource_url(pref_code: str) -> str:
    """CKAN APIから monthly_fromto_city_<pref> のダウンロードURLを解決する。

    リソースUUIDは更新で変わりうるのでハードコードしない。
    """
    payload = fetch_json(CKAN_API)
    if not payload.get("success"):
        raise RuntimeError("CKAN package_show が失敗しました")
    want = f"monthly_fromto_city_{pref_code}"
    for res in payload["result"]["resources"]:
        if want in res.get("url", "") or want in res.get("name", ""):
            return res["url"]
    raise RuntimeError(f"リソースが見つかりません: {want}")


def _iter_month_files(zip_path: Path, pref_code: str):
    """外側zip内の <pref>/<YYYY>/<MM>/monthly_fromto_city.csv.zip を列挙する。"""
    with zipfile.ZipFile(zip_path) as outer:
        for name in sorted(outer.namelist()):
            parts = name.split("/")
            if len(parts) != 4 or not name.endswith(".csv.zip"):
                continue
            pref, year, month, _ = parts
            if pref != pref_code or not (year.isdigit() and month.isdigit()):
                continue
            yield f"{year}-{month}", name


def _read_month_rows(zip_path: Path, inner_name: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(zip_path) as outer:
        inner_bytes = outer.read(inner_name)
    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
        csv_name = next(n for n in inner.namelist() if n.endswith(".csv"))
        text = inner.read(csv_name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def load(
    *,
    cache_dir: Path,
    pref_code: str = areas.PREF_CODE,
    dayflag: str = "0",
    timezone: str = "0",
) -> dict[str, list[dict[str, str]]]:
    """収録されている全月の人流データを {'YYYY-MM': [row, ...]} で昇順に返す。

    dayflag/timezone の既定は 全日 / 終日。
    期間の絞り込みは呼び出し側（cli）が行う。
    """
    url = resource_url(pref_code)
    zip_path = fetch_cached(url, cache_dir)

    out: dict[str, list[dict[str, str]]] = {}
    for ym, inner in _iter_month_files(zip_path, pref_code):
        out[ym] = [
            r
            for r in _read_month_rows(zip_path, inner)
            if r["dayflag"] == dayflag and r["timezone"] == timezone
        ]
    if not out:
        raise RuntimeError(
            "人流オープンデータを読み取れませんでした。提供期間は "
            f"{AVAILABLE_FROM}〜{AVAILABLE_TO} です。"
        )
    return dict(sorted(out.items()))


def stay_rows(
    monthly: dict[str, list[dict[str, str]]], target_areas: dict[str, str]
) -> list[tuple[str, str, int]]:
    """resas-stay.csv 用の (年月, エリア, 滞在人口)。from_area を合算した総滞在人口。"""
    rows: list[tuple[str, str, int]] = []
    for ym, records in monthly.items():
        totals: dict[str, int] = {}
        for r in records:
            code = r["citycode"]
            if code in target_areas:
                totals[code] = totals.get(code, 0) + int(r["population"])
        for code, name in target_areas.items():
            if code in totals:
                rows.append((ym, name, totals[code]))
            else:
                log.warning("%s: %s(%s) のデータがありません", ym, name, code)
    # 年月の昇順（同一年月内はエリア定義順）で返す
    order = list(target_areas.values())
    return sorted(rows, key=lambda r: (r[0], order.index(r[1])))


def fromto_rows(
    monthly: dict[str, list[dict[str, str]]],
    target_areas: dict[str, str],
    *,
    month: str | None = None,
) -> tuple[list[tuple[str, int]], str]:
    """resas-fromto.csv 用の (流入元, 滞在人口)。

    列仕様に年月が無いため、単月のスナップショットを出力する。
    month 未指定なら取得できた最新月。同市区町村（域内滞在）も行として残す。
    """
    ym = month or max(monthly)
    if ym not in monthly:
        raise RuntimeError(f"{ym} のデータがありません。利用可能: {', '.join(monthly)}")
    totals: dict[str, int] = {}
    for r in monthly[ym]:
        if r["citycode"] not in target_areas:
            continue
        label = areas.FROM_AREA_LABELS.get(r["from_area"], f"区分{r['from_area']}")
        totals[label] = totals.get(label, 0) + int(r["population"])
    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return ordered, ym
