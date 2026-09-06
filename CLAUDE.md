# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

リポジトリの記述言語は日本語（docstring・ログ・出力ノートすべて）。合わせること。

## 何をするツールか

山形県米沢市の「滞在人口 / From-to（流入元）/ 宿泊者数」を公的オープンデータから取得し、
**列名が固定された3つのCSV**（`resas-stay.csv` / `resas-fromto.csv` / `resas-lodging.csv`）を
リポジトリ直下に生成する。列仕様は `writers.HEADERS` が単一の情報源。

外部依存ゼロ（標準ライブラリのみ）。この方針は維持すること。

## コマンド

```bash
# セットアップ（venvのpythonを明示的に叩く。`python3`はanaconda等を拾って壊れることがある）
/usr/local/bin/python3 -m venv .venv
.venv/bin/python -m pip install -e .

# 実行
.venv/bin/resas-automate fetch                        # 3種まとめて
.venv/bin/resas-automate fetch --kind fromto -v       # 種別を絞る／詳細ログ
.venv/bin/resas-automate estat-meta                   # e-Stat統計表の分類コードを一覧（デバッグの起点）

# 構文チェック（テストスイートは無い）
.venv/bin/python -m compileall -q src
```

`pip install -e .` 後に **新しいモジュールを追加したら再インストール不要**（`.pth` 参照）だが、
`.venv/bin/python -c "import resas_automate"` が通らなくなったら venv が壊れている。作り直す。

## 前提：RESAS本体からは何も取れない

これがこの設計全体の理由なので、最初に理解すること。

- **RESAS-API は 2025-03-24 に提供終了**。
- 現行 resas.go.jp は Next.js の完全クライアントレンダリングで、公開APIもサーバサイドHTMLも無い。
- **現行RESASに From-to分析機能そのものが存在しない**（JSバンドルを全チャンク走査して
  `fromto` / `流入` / `発地` / `滞在人口` すべて0ヒットを確認済み、2026-09時点）。

したがって全ソースは**代替物**であり、RESASと同じ値は出ない。
「RESASから取る」方向の実装を新たに足そうとしないこと。時間の無駄になる。

## アーキテクチャ

`cli.py` がオーケストレーション、`sources/*` が取得元ごとのアダプタ、`writers.py` が出力の門番。
sources は互いに独立で、`cli.cmd_fetch` だけが束ねる。

各 source は「取れる全期間を返す」だけで、**期間の絞り込みはしない**（`cli` の責務）。
`mlit_jinryu.load()` / `estat_lodging.fetch_lodging()` が期間引数を持たないのは意図的。

### 取得元ごとに収録範囲の天井が違う

| source | 収録範囲 | 鍵 |
|---|---|---|
| `mlit_jinryu` | 2019-01〜2021-12 | 不要 |
| `estat_census_fromto` | 2020年（国勢調査） | 要 |
| `estat_lodging` | 2014-01〜2016-12 | 要 |

天井がバラバラなので `cli.select_year_months()` が**ソースごとに独立して**
「今年 → 無ければ収録最新年」へフォールバックする。既定実行では stay=2021年、
lodging=2016年 と別々の年が出るのが正常。

### 列名と中身が一致しない箇所がある（意図的）

`resas-fromto.csv` の列は仕様上 `滞在人口` だが、既定の `census` 経路で入る値は
**国勢調査の通勤・通学者数**（2020年時点）。月次の滞在人口を自治体名単位まで
分解した公的データは存在しないため、これが自動取得の上限。

このギャップを埋めるのが `writers.sidecar_note()`。CSV本体は列仕様どおりに保ち、
**出典・集計条件・制約は同名の `.md` に必ず書き出す**。
新しい source を足すときも sidecar は必須。CSVだけ出して済ませないこと。

### e-Stat API の扱い

- **`statsDataId` は期間固有で、時系列の通し番号ではない**。`0003314421` は2014-2016しか持たない。
  「同じ表の最新版」は別IDで存在するとは限らず、宿泊旅行統計調査の2017年以降は
  そもそもDB登録されておらずファイル配布のみ（`getStatsData` から取得不可）。
- **分類コードはハードコードせず、`getMetaInfo` の名称マッチで解決する**
  （`_find_obj` / `_find_code`）。表の改訂で番号が変わっても壊れにくくするため。
  解決に失敗したら `estat-meta` で実際の分類を見るのが定石。
- レスポンスの封筒は `GET_META_INFO` / `GET_STATS_DATA` の下。`_call()` が剥がして
  `RESULT.STATUS != 0` を `EstatError` に変換する。生の `fetch_json` を直接使わないこと。

### 市区町村コードは必ず検証する

過去に `06206` を長井市としていた実バグがある（正しくは寒河江市、長井市は `06209`）。
`estat_census_fromto.fromto_rows()` が実行時に `areas.py` のラベルと統計表の公式名称を
突き合わせ、不一致なら `EstatError` で停止する。この検証を外さないこと。

国勢調査の常住地分類は階層構造（level 1=全国 / 2=都道府県 / 4=市・特別区部・特別区・政令市 /
5=政令市の区 / 6=町村）。市区町村として採用するのは
**level が 4 か 6 かつ親が都道府県(level 2)** のみ。
これで政令市の区と東京23区（親が特別区部＝level 4）が落ち、二重計上が消える。

## 検証のしかた

テストスイートは無い。政府APIを実際に叩いて**合計の整合性**で確かめる。既知の不変条件:

- `resas-fromto.csv`（米沢市・全件）の合計 = **10,423**（「他市区町村に常住」の総数と一致）。
  `--fromto-top` で絞っても「その他」を含めた合計はこの値。
- `resas-stay.csv` 2021-08 の米沢市 = **88,834**。
- `mlit_jinryu` は発地4区分の合算が総滞在人口。

コードを変えたら該当する数値が動いていないか確認する。ズレたら集計ロジックのバグ。

## .env はツール経由で触れない

`.claude/settings.json` のフックが `.env` / `.env.*` / `.envrc` への
Read/Edit/Write と、**Bash内に `.env` という文字列を含むコマンド全般**をブロックする
（ヒアドキュメントでファイルを書く場合も引っかかる）。

キーが必要な処理は `cli.load_dotenv()` に読ませる。アドホック検証も同じ:

```python
import sys; sys.path.insert(0, 'src')
from resas_automate import cli; cli.load_dotenv()
import os; app_id = os.environ["ESTAT_APP_ID"]
```

`load_dotenv()` は既存の環境変数を上書きしない（シェル側の指定が優先）。

## HTTP

`http.fetch*` を必ず経由する。政府系サイト向けに**1秒以上のスロットリング**と
リトライが入っている。`urllib` を直に呼ばないこと。
配信元ZIPは `cache/` にキャッシュされる（山形県分で約230KB）。取り直したいときは消す。
