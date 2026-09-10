"""e-Stat「ファイル配布」からExcel/CSVを見つけて落とす（統計データベース未登録の表）。

## なぜ要るか

`getStatsData`（`estat_lodging.py`）で取れるのは「統計データベース」に
登録された表だけで、宿泊旅行統計調査は **2014-01〜2016-12 しか登録がない**。
2017年以降はファイル配布（Excel）のみで、APIからは1件も返らない
（`getStatsList` に statsCode=00601020 を投げても32表・全て2014-2016）。
最新値を取るにはファイル配布側を辿るしかない。

## Seleniumは要らない（2026-09時点）

ファイル検索画面 https://www.e-stat.go.jp/stat-search/files はJSレンダリングだが、
その画面自身が叩いている **`/retrieve/api_file`** はプレーンHTTPのGETで
JSONを返す。ログインもCSRFトークンも不要。よってブラウザは不要で、
標準ライブラリだけで完結する。

配信側がこの経路を塞いだ場合に備えて `browser.py`（Selenium・任意依存）を
用意してある。`use_browser=True` を渡すとそちらを通る。

## 画面の階層

e-Statのファイル配布は4階層あり、上から順に辿らないとファイルIDが出てこない。

    政府統計 (toukei=00601020)
      └ 提供統計 (tstat)      … 年確定値 / 第1次速報値 / 第2次速報値 …
          └ 提供周期 (cycle)  … 1=月次 / 7=年次
              └ 調査年月 (year, month)
                  └ 統計表 (statInfId) → file-download

`month` は ``11010302`` のような8桁コード（上半期/四半期/四半期の開始月/
終了月/当月）で、**自分で組み立てず一覧のリンクから読む**。
表番号と同じく、コード体系の改訂で壊れるのを避けるため。
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from ..http import fetch, fetch_cached

log = logging.getLogger(__name__)

API_FILE = "https://www.e-stat.go.jp/retrieve/api_file"
DOWNLOAD = "https://www.e-stat.go.jp/stat-search/file-download"
FILES_PAGE = "https://www.e-stat.go.jp/stat-search/files"

# 政府統計コード: 宿泊旅行統計調査
TOUKEI_LODGING = "00601020"

CYCLE_MONTHLY = "1"
CYCLE_YEARLY = "7"

# fileKind: 0=データ本体（Excel/CSV） 4=閲覧用Excel
FILE_KIND_DATA = "0"


class EstatFilesError(RuntimeError):
    pass


@dataclass(frozen=True)
class Dataset:
    """提供統計（年確定値・第1次速報値 …）。"""

    tstat: str
    name: str
    count: int


@dataclass(frozen=True)
class Cycle:
    """提供周期（月次・年次）。"""

    code: str
    label: str
    count: int


@dataclass(frozen=True)
class Period:
    """調査年月。``month`` は年次表なら None。"""

    year: int
    month: int | None
    year_code: str
    month_code: str

    @property
    def label(self) -> str:
        return f"{self.year}年" if self.month is None else f"{self.year}年{self.month}月"


@dataclass(frozen=True)
class RemoteFile:
    """ダウンロードできる統計表ファイル1件。"""

    stat_inf_id: str
    file_kind: str
    file_type: str
    title: str

    @property
    def url(self) -> str:
        return f"{DOWNLOAD}?statInfId={self.stat_inf_id}&fileKind={self.file_kind}"


# --------------------------------------------------------------------- 取得

def _items(params: dict[str, str], *, use_browser: bool = False) -> str:
    """`/retrieve/api_file` を叩いて結果一覧のHTML断片を返す。"""
    query = urllib.parse.urlencode(params)
    if use_browser:
        from . import browser  # 任意依存。使うときだけ読み込む

        # page_source は属性がエスケープ済み（href の & が &amp;）なので、
        # JSON経路と同じ「素のHTML」に揃えてから正規表現にかける
        return html.unescape(
            browser.render(f"{FILES_PAGE}?{query}", wait_selector=".js-items")
        )
    payload = fetch(f"{API_FILE}?{query}")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EstatFilesError(
            f"e-Statのファイル一覧がJSONで返りませんでした（画面仕様の変更かもしれません）: {exc}"
        ) from exc
    items = data.get("items")
    if items is None:
        raise EstatFilesError(f"応答に items がありません: {sorted(data)}")
    return html.unescape(items)


def _base(**extra: str) -> dict[str, str]:
    params = {"page": "1", "toukei": TOUKEI_LODGING}
    params.update({k: v for k, v in extra.items() if v})
    return params


def list_datasets(*, toukei: str = TOUKEI_LODGING, use_browser: bool = False) -> list[Dataset]:
    """提供統計（年確定値／速報値）の一覧。"""
    items = _items(_base(toukei=toukei), use_browser=use_browser)
    out: list[Dataset] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'data-key="tstat"\s+data-value="(\d+)">(.*?)<span class="stat-pc">\[(\d+)件\]',
        re.S,
    )
    for tstat, blob, count in pattern.findall(items):
        if tstat in seen:
            continue
        # blob には入れ子のタグが混ざるので、最後のテキストだけ拾う
        text = re.sub(r"<[^>]*>", "\n", blob)
        name = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not name:
            continue
        seen.add(tstat)
        out.append(Dataset(tstat=tstat, name=name[-1], count=int(count)))
    if not out:
        raise EstatFilesError(
            "提供統計の一覧を取得できませんでした。e-Statの画面仕様が変わった可能性があります。"
        )
    return out


def list_cycles(tstat: str, *, use_browser: bool = False) -> list[Cycle]:
    """提供周期（月次=1 / 年次=7）の一覧。"""
    items = _items(_base(tstat=tstat), use_browser=use_browser)
    out: list[Cycle] = []
    seen: set[str] = set()
    for code, label, count in re.findall(
        r'cycle=(\d+)[^>]*>.*?>\s*([^<>\[\]]+?)\s*<span class="stat-pc">\[(\d+)件\]', items, re.S
    ):
        if code in seen:
            continue
        seen.add(code)
        out.append(Cycle(code=code, label=label.strip(), count=int(count)))
    return out


def list_periods(tstat: str, cycle: str, *, use_browser: bool = False) -> list[Period]:
    """収録されている調査年月の一覧（新しい順）。"""
    items = _items(
        _base(tstat=tstat, cycle=cycle, layout="datalist"), use_browser=use_browser
    )
    out: list[Period] = []
    seen: set[tuple[int, int | None]] = set()
    for href, year_code, blob in re.findall(
        r'<a[^>]*href="([^"]*year=(\d{4})0[^"]*)"[^>]*>(.*?)</a>', items, re.S
    ):
        month_code = _param(href, "month")
        text = re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", blob))
        m = re.search(r"(\d{1,2})月", text)
        month = int(m.group(1)) if m else None
        key = (int(year_code), month)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Period(
                year=int(year_code),
                month=month,
                year_code=f"{year_code}0",
                month_code=month_code or "0",
            )
        )
    out.sort(key=lambda p: (p.year, p.month or 0), reverse=True)
    return out


def list_files(
    tstat: str, cycle: str, period: Period, *, use_browser: bool = False
) -> list[RemoteFile]:
    """指定の調査年月にぶら下がる統計表ファイルの一覧。"""
    items = _items(
        _base(
            tstat=tstat,
            cycle=cycle,
            layout="datalist",
            year=period.year_code,
            month=period.month_code,
        ),
        use_browser=use_browser,
    )
    titles = [
        re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", blob))
        for blob in re.findall(r'class="[^"]*js-data"[^>]*>(.*?)</a>', items, re.S)
    ]
    title = titles[0] if titles else period.label
    out: list[RemoteFile] = []
    seen: set[tuple[str, str]] = set()
    for block in re.findall(
        r'href="[^"]*file-download\?statInfId=(\d+)&fileKind=(\d+)"(.*?)</a>', items, re.S
    ):
        stat_inf_id, file_kind, attrs = block
        if (stat_inf_id, file_kind) in seen:
            continue
        seen.add((stat_inf_id, file_kind))
        m = re.search(r'data-file_type="([^"]*)"', attrs)
        out.append(
            RemoteFile(
                stat_inf_id=stat_inf_id,
                file_kind=file_kind,
                file_type=(m.group(1) if m else ""),
                title=title,
            )
        )
    return out


def download(
    remote: RemoteFile,
    cache_dir: Path,
    *,
    use_browser: bool = False,
    label: str = "",
) -> Path:
    """統計表ファイルを落としてキャッシュのパスを返す。

    ``label``（例 ``"lodging_2026-06"``）を渡すと cache/ の中で見分けが付く。
    URLは ``file-download?statInfId=…`` でパスに情報が無いため。
    """
    if use_browser:
        from . import browser

        return browser.download(remote.url, cache_dir)
    name = f"{label}_{remote.stat_inf_id}" if label else remote.stat_inf_id
    return fetch_cached(remote.url, cache_dir, name=name, suffix=".xlsx")


# ------------------------------------------------------------------- 補助

def _param(url: str, key: str) -> str:
    query = urllib.parse.urlparse(url).query
    return urllib.parse.parse_qs(query).get(key, [""])[0]


def find_dataset(datasets: list[Dataset], *keywords: str) -> Dataset | None:
    """名称に keywords のいずれかを含む提供統計を返す（先頭一致優先）。"""
    for keyword in keywords:
        for ds in datasets:
            if keyword in ds.name:
                return ds
    return None


def describe(*, use_browser: bool = False) -> str:
    """提供統計・周期・収録年月を人が読める形で返す（デバッグの起点）。"""
    lines: list[str] = [f"政府統計 {TOUKEI_LODGING}（宿泊旅行統計調査）のファイル配布"]
    for ds in list_datasets(use_browser=use_browser):
        lines.append(f"\n[{ds.tstat}] {ds.name}（{ds.count}件）")
        for cycle in list_cycles(ds.tstat, use_browser=use_browser):
            periods = list_periods(ds.tstat, cycle.code, use_browser=use_browser)
            span = f"{periods[-1].label}〜{periods[0].label}" if periods else "（なし）"
            lines.append(f"    cycle={cycle.code} {cycle.label}: {len(periods)}期 {span}")
    return "\n".join(lines)
