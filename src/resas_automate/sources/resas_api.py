"""現行RESAS（resas.go.jp）の内部APIへの共通アクセス。

旧RESAS-API（opendata.resas-portal.go.jp）は2025-03-24に終了しているが、
現行サイトの画面は ``https://api.resas.go.jp/v2/`` を叩いており、
**鍵もログインもCookieも不要**で取得できる。Seleniumも要らない。

このモジュールが持つのは次の2つだけ:

- **403を避けるためのヘッダ**。api.resas.go.jp は
  **ブラウザのUA・``Origin``・``Referer`` の3つが揃わないと403**を返す。
  どれか1つでも欠けると通らない（実測で確認済み）。この知識をここ1か所に閉じ込める。
- **2種類の応答の剥がし方**。グラフ用のJSON（``{"option": {...}}``）と、
  ダウンロードボタンのZIP（中身はShift_JISのCSV）。

パラメータはFastAPIのenumで守られていて、不正値を送ると422と一緒に
**許容値の一覧が返る**。仕様を調べるときはこれが一番速い。
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile

from .. import http

log = logging.getLogger(__name__)

API_BASE = "https://api.resas.go.jp/v2"
SITE = "https://resas.go.jp"

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": SITE,
    "Referer": f"{SITE}/",
}

# 配信元CSVの文字コード。RESASのダウンロードはShift_JIS（cp932）で出てくる。
_CSV_ENCODINGS = ("cp932", "utf-8-sig", "utf-8")


class ResasApiError(RuntimeError):
    """現行RESASの内部APIから期待した形の値が取れなかった。"""


def fetch_option(path: str) -> dict:
    """グラフ用エンドポイントを叩いて ``option`` を返す。

    画面はグラフ描画ライブラリに ``optionUrl`` を渡す作りなので、
    返るのはECharts の描画設定である。値そのものは入っている。
    **データが無い条件では404ではなく 200 + ``{"option": {}}``** が返るので、
    呼び出し側は空dictを「未収録」として扱うこと。
    """
    payload = http.fetch_json(f"{API_BASE}/{path}", headers=HEADERS)
    if "option" not in payload:
        raise ResasApiError(f"想定外のレスポンスです: {str(payload)[:200]}")
    return payload["option"] or {}


def download_csvs(uri: str) -> list[tuple[str, list[list[str]]]]:
    """ダウンロードボタンのURIを叩き、[(ファイル名, 行のリスト)] を返す。

    ``uri`` は画面の ``downloadUri`` そのまま（先頭スラッシュ込み。
    例 ``/tourism/tourism-domestic/graph?year=2025&…``）。
    応答はZIPで、中身は1つ以上のShift_JIS CSV。
    ZIP内のファイル名はUTF-8フラグ付きなので zipfile がそのまま復号する。
    """
    body = http.fetch(f"{API_BASE}/download{uri}", headers=HEADERS)
    if not body.startswith(b"PK"):
        raise ResasApiError(
            f"ZIPが返りませんでした（{len(body)}バイト）: {body[:200]!r}"
        )
    out: list[tuple[str, list[list[str]]]] = []
    with zipfile.ZipFile(io.BytesIO(body)) as book:
        for info in book.infolist():
            if info.is_dir():
                continue
            out.append((info.filename, _parse_csv(book.read(info), info.filename)))
    if not out:
        raise ResasApiError(f"ZIPが空です: {uri}")
    return out


def _parse_csv(raw: bytes, name: str) -> list[list[str]]:
    for encoding in _CSV_ENCODINGS:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ResasApiError(f"文字コードを判別できません: {name}")
    return [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]
