# RESAS-Automate

**山形県米沢市**の **滞在人口・From-to（流入元）・宿泊者数** を公的オープンデータから取得し、
決められた列仕様のCSV（`resas-stay.csv` / `resas-fromto.csv` / `resas-lodging.csv`）を生成する。

- **対象地域は山形県米沢市で固定**（市区町村コード `06202`）。
- **年月は昇順**で出力する。
- 既定では**対象年（今年）のうち収録されている範囲**を取得する。
  その年がまだ未収録・未公表なら、収録されている最新年へ自動で切り替える。

外部パッケージ不要（Python 3.10+ の標準ライブラリのみ）。

---

## 重要：RESAS-API は終了している

**RESAS-API は 2025年3月24日をもって提供を終了した**（アカウントも自動削除）。
現行の [RESAS](https://resas.go.jp/) は Next.js の画面アプリで、公開APIもサーバサイドHTMLも持たない。
そのため「RESASから自動でAPI取得」は現在成立しない。

本ツールは代わりに **一次データの配信元から直接取得**し、RESASと同じ指標を組み立てる。
RESAS画面からのCSV手動ダウンロードにも対応する（`manual` サブコマンド）。

## 取得元と実際に取れるもの

| 出力 | 取得元 | 自動取得 | 収録範囲 | 粒度 |
|---|---|---|---|---|
| `resas-stay.csv` | 国交省 [全国の人流オープンデータ](https://www.geospatial.jp/ckan/dataset/mlit-1km-fromto)（市区町村単位発地別） | ✅ 鍵不要 | **2019-01〜2021-12** | 市区町村・月次 |
| `resas-fromto.csv` | 国勢調査 従業地・通学地集計（[e-Stat API](https://www.e-stat.go.jp/api/)）※既定 | ✅ 要APIキー（無料） | **2020年** | **自治体名単位** |
| 〃（`--fromto-source jinryu`） | 上の人流オープンデータ | ✅ 鍵不要 | 2019-01〜2021-12 | 発地4区分のみ・月次 |
| `resas-lodging.csv` | 観光庁 宿泊旅行統計調査 参考第３表（[e-Stat API](https://www.e-stat.go.jp/api/)） | ✅ 要APIキー（無料） | **2014-01〜2016-12** | **都道府県**・月次 |

### 制約（重要）

1. **滞在人口は2021年12月まで**。国交省の人流オープンデータは2019-01〜2021-12で更新が止まっており、
   2022年以降の月次滞在人口を無償・自動で取れる全国データは存在しない。

2. **自治体名単位のFrom-toは「通勤・通学者数」である**。
   月次の滞在人口を自治体名単位で流入元まで分解した公的オープンデータは存在しない。
   - 人流オープンデータ（`--fromto-source jinryu`）の `from_area` 列は
     `0=同市区町村 / 1=同都道府県 / 2=同地方 / 3=それ以外` の4区分どまり。
   - 旧RESASの「From-to分析（滞在人口）」は提供終了。**現行RESASにFrom-to分析機能は無い**
     （2026-09時点でフロントのJSバンドルを全チャンク走査して確認済み）。
   - そのため既定（`--fromto-source census`）では国勢調査 従業地・通学地集計を使い、
     **2020年時点の通勤・通学者数**を自治体名つき・降順で出力する。
     観光目的の人流ではない点に注意（出力される `.md` にも毎回明記される）。

3. **宿泊者数は都道府県単位**。宿泊旅行統計調査の公表単位は47都道府県であり、
   米沢市単独の月次宿泊者数は全国統計としては公表されていない。既定では山形県の値を出力する。
   市町村別が必要な場合は山形県／米沢市の観光統計を手動取得し `manual` で変換する。

4. **宿泊者数は2016年12月まで**。宿泊旅行統計調査のうち「統計データベース」として
   e-Stat APIから取得できるのは2014-01〜2016-12分のみ。2017年以降の値（速報値・確定値）は
   [ファイル配布](https://www.e-stat.go.jp/stat-search/files?toukei=00601020)のみで、
   getStatsData APIには存在しない。最新の月次値が必要な場合は当該ページからExcel/CSVを手動DLし、
   `manual` で変換する。

これらの制約に触れる部分は、出力CSVと同名の `.md`（例 `resas-stay.md`）に出典・集計条件として毎回書き出される。

---

## セットアップ

```bash
cd RESAS-Automate
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # e-Stat のアプリケーションIDを書く（下記）
```

`pip install -e .` で `resas-automate` コマンドが使えるようになる（`PYTHONPATH` の設定は不要）。
`.env` は起動時に自動で読み込まれる。`.env` に書く内容は以下の1行だけ：

```
ESTAT_APP_ID=あなたのアプリケーションID
```

e-Stat のアプリケーションIDは <https://www.e-stat.go.jp/api/> で無料登録できる。
登録フォームの「URL」欄は実在確認されない申請メタデータなので、公開先が無ければ
`https://example.com/`（IANAが例示用に予約している実在ドメイン）を入れておけば通る
（`localhost` やIPアドレスは形式チェックで弾かれることがある）。

`.env` を使わず一時的にキーを渡したい場合は環境変数が優先される：

```bash
ESTAT_APP_ID=xxxx resas-automate fetch --kind lodging
```

以降の例は `pip install -e .` 済みの前提で `resas-automate` コマンドを使う。
インストールせず直接実行する場合は `python3 -m resas_automate ...`
（この場合のみリポジトリ直下から `PYTHONPATH=src` が必要）。

## 使い方

### 自動取得

```bash
# 3種類まとめて（米沢市・対象年の取れる範囲を昇順で）
resas-automate fetch

# 年を明示する
resas-automate fetch --kind stay --year 2019

# 年をまたぐ任意期間（--year より優先）
resas-automate fetch --kind stay --months 2020-11:2021-02

# 流入元を全件出す（既定は上位10件＋その他）
resas-automate fetch --kind fromto --fromto-top 0

# 流入元を月次の発地4区分に切り替える（対象月も指定可）
resas-automate fetch --kind fromto --fromto-source jinryu --fromto-month 2021-08

# 休日・昼の滞在人口
resas-automate fetch --kind stay --dayflag 2 --timezone 1

# 置賜8市町（参考）
resas-automate fetch --areas okitama --kind stay
```

`--year` を省略すると今年（実行時のシステム年）が対象になる。
人流オープンデータは2021年で更新が止まっているため、既定では自動的に2021年の全12ヶ月が出力される。

主なオプション:

| オプション | 既定 | 説明 |
|---|---|---|
| `--kind {stay,fromto,lodging}` | 全部 | 複数指定可 |
| `--year 2021` | 今年 | その年の収録済み月を昇順で取得。未収録なら収録最新年に自動切替 |
| `--months 2021-01:2021-12` | （`--year` を使う） | 年指定より優先。`2021-07,2021-08` のような列挙も可 |
| `--areas {yonezawa,okitama}` | `yonezawa` | 米沢市固定。`okitama` は置賜8市町の参考用 |
| `--fromto-source {census,jinryu}` | `census` | `census`=自治体名単位（国勢調査）／`jinryu`=発地4区分（人流・月次） |
| `--fromto-top N` | `10` | `census`時の上位件数。残りは「その他」に集約。`0`で全件 |
| `--dayflag {0,1,2}` | `0` | 0=全日 1=平日 2=休日 |
| `--timezone {0,1,2}` | `0` | 0=終日 1=昼 2=夜 |
| `--pref 山形県` | 山形県 | 宿泊統計の対象都道府県 |
| `--out DIR` | リポジトリ直下 | CSV出力先 |

### RESAS画面からのCSVを変換する（自治体名単位のFrom-to等）

1. [RESAS](https://resas.go.jp/) で対象マップ・地域（米沢市／置賜）・期間を指定して表示
2. CSVダウンロード
3. ダウンロードしたCSVを `input/` に置く（ファイル名は任意。`fromto` などを含めると自動判別されやすい）
4. 変換

```bash
resas-automate manual --kind fromto
resas-automate manual --file input/任意の名前.csv --kind stay
```

Shift_JIS / UTF-8、前置きの説明行、`"9,800"` のような桁区切り、`2025年8月` 形式の年月を自動で処理する。
`滞在人口率` と `滞在人口` のように似た見出しが並ぶ場合は、より一致度の高い列を選ぶ。

### そのほか

```bash
# 宿泊旅行統計調査の分類コードを確認する（列の解決に失敗したとき）
resas-automate estat-meta

# 列仕様どおりのサンプル（ダミー）CSVを生成する
resas-automate sample --kind lodging
```

---

## 出力仕様

```
resas-stay.csv     年月,エリア,滞在人口        例) 2021-08,米沢市,88834
resas-fromto.csv   流入元,滞在人口             例) 高畠町,3411
resas-lodging.csv  年月,宿泊者数,うち外国人    例) 2016-08,443960,3030
```

年月を持つ `resas-stay.csv` / `resas-lodging.csv` は**年月の昇順**。
`resas-fromto.csv` は年月列を持たないため**値の降順**で並べ、末尾に「その他」を置く
（`--fromto-source jinryu` の場合は対象期間の最新月のスナップショット）。
Excel でそのまま開けるよう UTF-8 BOM 付きで書き出す。

滞在人口は「1か月間における1日あたりの平均値」（配信元の定義）。
`resas-stay.csv` の値は発地4区分（同市区町村〜それ以外）を**合算した総滞在人口**。

## 構成

```
src/resas_automate/
├── cli.py                     サブコマンド（fetch / manual / estat-meta / sample）
├── areas.py                   対象市区町村（米沢市 06202 固定）・コードラベル定義
├── http.py                    取得・リトライ・レート制限・キャッシュ
├── writers.py                 列仕様の固定とCSV出力、出典 .md の生成
└── sources/
    ├── mlit_jinryu.py         人流オープンデータ（滞在人口 / From-to発地4区分）
    ├── estat_census_fromto.py 国勢調査 従業地・通学地集計（自治体名単位の流入元）
    ├── estat_lodging.py       宿泊旅行統計調査（e-Stat API）
    └── manual_import.py       手動DL CSVの正規化
```

市区町村コードは実行時に統計表の公式名称と突き合わせて検証する。
取り違え（例: `06206` は寒河江市であって長井市ではない）はエラーで止まる。

`cache/` に配信元のZIPをキャッシュする（山形県分で約230KB）。再取得したい場合は削除する。
政府系サイトへの負荷を避けるため、リクエスト間隔は1秒以上空けている。

## 出典表記

- 国土交通省「全国の人流オープンデータ（1kmメッシュ、市区町村単位発地別）」（G空間情報センター）
- 観光庁「宿泊旅行統計調査」（e-Stat 政府統計の総合窓口）
- 総務省統計局「令和2年国勢調査 従業地・通学地集計」（e-Stat 政府統計の総合窓口）
