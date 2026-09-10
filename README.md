# RESAS-Automate

**山形県米沢市**の **滞在人口・From-to（流入元）・宿泊者数** を公的オープンデータから取得し、
決められた列仕様のCSV（`resas-stay.csv` / `resas-fromto.csv` / `resas-lodging.csv`）を
`data/raw/resas/` に生成する。

- **対象地域は山形県米沢市で固定**（市区町村コード `06202`）。
- **年月は昇順**で出力する。
- 既定では**対象年（今年）のうち収録されている範囲**を取得する。
  その年がまだ未収録・未公表なら、収録されている最新年へ自動で切り替える。

外部パッケージ不要（Python 3.10+ の標準ライブラリのみ）。Excelの読み取りも自前実装で、
openpyxl も pandas も要らない。Selenium だけは任意依存だが、通常経路では使わない。

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
| 〃（`--fromto-source lodging`） | 宿泊旅行統計 参考第2表（居住地別・[ファイル配布](https://www.e-stat.go.jp/stat-search/files?toukei=00601020)） | ✅ 鍵不要 | 2015-04〜**最新月** | **山形県**・月次（大規模施設のみ） |
| `resas-lodging.csv` | 観光庁 宿泊旅行統計調査（[e-Stat ファイル配布](https://www.e-stat.go.jp/stat-search/files?toukei=00601020)のExcel）※既定 | ✅ 鍵不要 | **2015年〜最新の第2次速報月** | **都道府県**・月次 |
| 〃（`--lodging-area city`） | 上の速報値 参考第6表・第8表 | ✅ 鍵不要 | 同上（掲載月のみ） | **米沢市**・月次 |
| 〃（`--lodging-source api`） | 宿泊旅行統計調査 参考第３表（[e-Stat API](https://www.e-stat.go.jp/api/)） | ✅ 要APIキー（無料） | **2014-01〜2016-12** | 都道府県・月次 |

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

3. **宿泊者数はe-Stat APIでは2016年12月までしか取れない**。「統計データベース」に
   登録されているのは2014-01〜2016-12分だけで、2017年以降は
   [ファイル配布](https://www.e-stat.go.jp/stat-search/files?toukei=00601020)のExcelしか無い
   （`getStatsData` にも `getStatsList` にも出てこない）。
   本ツールは**この配布Excelを自動でダウンロードして変換する**ので、既定で最新月まで取れる。
   手動DLは不要（手元のExcelを使いたい場合は `manual` でも変換できる）。

4. **米沢市の月次宿泊者数は取れる**。第2次速報値の参考第6表・第8表が
   「施設所在地（主な市区町村）」別になっており、山形県米沢市が掲載されている。
   `--lodging-area city` で出力できる。ただし掲載対象は「主な市区町村」に限られ、
   **どの市区町村が載るかは月によって変わる**ため月が飛ぶことがある
   （抜けは `resas-lodging.md` の「欠測」に記録される）。
   年確定値には市区町村別の表が無いので、この値が確報に置き換わることはない。

5. **From-toだけは最新化できない**。国勢調査の従業地・通学地集計は令和2年（2020年）が最新で、
   令和7年調査ぶんは未公表（前回は調査の約2年後）。人流オープンデータも2021-12で凍結
   （配信元リソースの最終更新は2022-01）。

   代用として `--fromto-source lodging`（宿泊旅行統計 参考第2表＝宿泊者の居住地）を
   用意しているが、**既定にはしていない**。以下のとおり別物だからである。

   | | census（既定） | lodging |
   |---|---|---|
   | 地域 | **米沢市** | 山形県 |
   | 流入元の粒度 | 市区町村名 | 都道府県＋国外 |
   | 意味 | 通勤・通学者数（人） | 延べ宿泊者数（人泊） |
   | 時点 | 2020年（5年に1度） | **最新月**（月次） |
   | 対象 | 全数 | 大規模施設のみ・**月で範囲が変わる**（20室以上/200室以上） |
   | 欠測 | なし | 居住地不詳が**0〜30%**（「不詳」行に立てる） |

   2026-06 の山形県は総数12,098人泊で、同月の県全体（383,880人泊）の3.2%にすぎない。
   ダッシュボードで使う場合は「山形県の大型施設に泊まった人の居住地」と明記すること。

6. **宿泊者数には系列が2本ある**。`--lodging-table all`（既定・全施設）と
   `over10`（従業者数10人以上の施設＝API経路と同じ系列）は同じ月でも値が違う。
   例: 2016-01 山形県は all=393,130 / over10=301,410。
   `over10` は集計区分が廃止されたため新しい速報値には存在しない。

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

> **iCloud同期下（Desktop / Documents）に置くと editable install が効かない**
>
> macOSのiCloud同期は `.venv` 配下に `hidden` フラグ（`UF_HIDDEN`）を付ける。
> Python 3.13以降の `site` は **hidden な `.pth` を黙って無視する**ため、
> `pip install -e .` しても `ModuleNotFoundError: No module named 'resas_automate'`
> になる。`chflags nohidden` は同期で戻され、venvを作り直しても再発する。
>
> 対処は次のいずれか（上ほど手軽）:
>
> ```bash
> PYTHONPATH=src .venv/bin/python -m resas_automate fetch   # 毎回これで実行する
> .venv/bin/python -m pip install .                         # 非editableにする（.pth不要）
> ```
>
> リポジトリを同期対象外（例 `~/src/`）へ移すのが根本解決。

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

# 流入元を「宿泊者の居住地」に切り替える（最新月・都道府県単位）
resas-automate fetch --kind fromto --fromto-source lodging
resas-automate fetch --kind fromto --fromto-source lodging --fromto-month 2026-04

# 休日・昼の滞在人口
resas-automate fetch --kind stay --dayflag 2 --timezone 1

# 置賜8市町（参考）
resas-automate fetch --areas okitama --kind stay

# ── 宿泊者数 ──
# 米沢市の月次宿泊者数（第2次速報値の市区町村別参考表）
resas-automate fetch --kind lodging --lodging-area city

# 山形県・特定年（年確定値があればそちら、無ければ第2次速報値）
resas-automate fetch --kind lodging --year 2025

# API経路と同じ系列（従業者数10人以上の施設）で出す
resas-automate fetch --kind lodging --year 2016 --lodging-table over10

# 旧来のAPI経路（要APIキー・2014-2016のみ）
resas-automate fetch --kind lodging --lodging-source api --year 2016

# ファイル配布側に何年分あるか見る（鍵不要・ダウンロードしない）
resas-automate estat-files
```

`--lodging-source` は既定 `auto`（対象年がAPIの収録範囲2014-2016なら `api`、
外なら `files`）。`files` はAPIキー不要で、Excelは `cache/` に残るので2回目以降は速い。

`--year` を省略すると今年（実行時のシステム年）が対象になる。
人流オープンデータは2021年で更新が止まっているため、既定では自動的に2021年の全12ヶ月が出力される。

主なオプション:

| オプション | 既定 | 説明 |
|---|---|---|
| `--kind {stay,fromto,lodging}` | 全部 | 複数指定可 |
| `--year 2021` | 今年 | その年の収録済み月を昇順で取得。未収録なら収録最新年に自動切替 |
| `--months 2021-01:2021-12` | （`--year` を使う） | 年指定より優先。`2021-07,2021-08` のような列挙も可 |
| `--areas {yonezawa,okitama}` | `yonezawa` | 米沢市固定。`okitama` は置賜8市町の参考用 |
| `--fromto-source {census,jinryu,lodging}` | `census` | `census`=自治体名単位（国勢調査2020）／`jinryu`=発地4区分（人流・2019-2021）／`lodging`=宿泊者の居住地（山形県・月次・最新月まで） |
| `--fromto-top N` | `10` | 上位件数。残りは「その他」に集約。`0`で全件 |
| `--fromto-month 2026-06` | 最新月 | `jinryu` / `lodging` 時の対象年月 |
| `--dayflag {0,1,2}` | `0` | 0=全日 1=平日 2=休日 |
| `--timezone {0,1,2}` | `0` | 0=終日 1=昼 2=夜 |
| `--pref 山形県` | 山形県 | 宿泊統計の対象都道府県 |
| `--lodging-source {auto,api,files}` | `auto` | `api`=e-Stat統計データベース（2014-2016・要キー）／`files`=配布Excel（2015年〜最新月・鍵不要）／`auto`=年で自動判定 |
| `--lodging-area {pref,city}` | `pref` | `city` は米沢市（`files` 経路のみ） |
| `--lodging-table {all,over10}` | `all` | `files`経路の集計対象。`over10`=従業者数10人以上（API経路と同系列） |
| `--use-browser` | off | 配布ファイルの取得をSelenium経由にする（通常不要） |
| `--out DIR` | `data/raw/resas` | CSV出力先（同名の `.md` も同じ場所） |

### 手元のCSV/Excelを変換する（RESAS画面の出力・県や市の観光統計など）

1. [RESAS](https://resas.go.jp/) で対象マップ・地域（米沢市／置賜）・期間を指定して表示
2. CSVダウンロード
3. ダウンロードしたCSVを `input/` に置く（ファイル名は任意。`fromto` などを含めると自動判別されやすい）
4. 変換

```bash
resas-automate manual --kind fromto
resas-automate manual --file input/任意の名前.csv --kind stay
resas-automate manual --file input/宿泊統計.xlsx --kind lodging   # Excelも可
```

Shift_JIS / UTF-8、前置きの説明行、`"9,800"` のような桁区切り、`2025年8月` 形式の年月を自動で処理する。
`滞在人口率` と `滞在人口` のように似た見出しが並ぶ場合は、より一致度の高い列を選ぶ。
`.xlsx` / `.xlsm` は全シートを縦に連結してから同じ見出し探索にかける（openpyxl 不要）。

なお宿泊旅行統計調査のExcelは `fetch` が自動でダウンロードするので、この手順は不要。

### そのほか

```bash
# 宿泊旅行統計調査の分類コードを確認する（列の解決に失敗したとき）
resas-automate estat-meta

# 列仕様どおりのサンプル（ダミー）CSVを生成する
resas-automate sample --kind lodging
```

---

## 出力仕様

いずれも `data/raw/resas/` に出力される（`--out` で変更可）。

```
data/raw/resas/resas-stay.csv     年月,エリア,滞在人口        例) 2021-08,米沢市,88834
data/raw/resas/resas-fromto.csv   流入元,滞在人口             例) 高畠町,3411
data/raw/resas/resas-lodging.csv  年月,宿泊者数,うち外国人    例) 2026-06,383880,12660
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
├── cli.py                     サブコマンド（fetch / manual / estat-meta / estat-files / sample）
├── areas.py                   対象市区町村（米沢市 06202 固定）・コードラベル定義
├── http.py                    取得・リトライ・レート制限・キャッシュ
├── xlsx.py                    xlsx読み取り（標準ライブラリのみ・openpyxl不要）
├── writers.py                 列仕様の固定とCSV出力、出典 .md の生成
└── sources/
    ├── mlit_jinryu.py         人流オープンデータ（滞在人口 / From-to発地4区分）
    ├── estat_census_fromto.py 国勢調査 従業地・通学地集計（自治体名単位の流入元）
    ├── estat_lodging.py       宿泊旅行統計調査（e-Stat API・2014-2016）
    ├── estat_files.py         e-Stat ファイル配布の一覧・ダウンロード
    ├── estat_lodging_files.py 配布Excel → 宿泊者数の行（都道府県／市区町村）
    ├── browser.py             Selenium フォールバック（任意依存・通常は未使用）
    └── manual_import.py       手動DL CSV/Excelの正規化

data/raw/resas/                生成物の置き場（--out の既定）
├── resas-stay.csv / .md
├── resas-fromto.csv / .md
└── resas-lodging.csv / .md
input/                         manual サブコマンドが読む手動DLファイル
cache/                         配信元ZIP・Excelのキャッシュ
```

市区町村コードは実行時に統計表の公式名称と突き合わせて検証する。
取り違え（例: `06206` は寒河江市であって長井市ではない）はエラーで止まる。

`cache/` に配信元のZIP・Excelをキャッシュする（人流の山形県分で約230KB、
宿泊の年確定値Excelが1年あたり約2.7MB、月次速報が約0.4MB）。再取得したい場合は削除する。
政府系サイトへの負荷を避けるため、リクエスト間隔は1秒以上空けている。

### ブラウザ（Selenium）について

**通常は不要**。e-Stat のファイル配布は、ファイル検索画面自身が使っている
JSON経路（`/retrieve/api_file`）をプレーンHTTPで叩けるため、標準ライブラリだけで
一覧からダウンロードまで完結する。

配信元がJSレンダリング専用に変わった場合の逃げ道として Selenium 経路を用意してあり、
`--use-browser` で切り替わる。任意依存なので使うときだけ入れる：

```bash
pip install -e ".[browser]"
resas-automate fetch --kind lodging --use-browser
```

## 出典表記

- 国土交通省「全国の人流オープンデータ（1kmメッシュ、市区町村単位発地別）」（G空間情報センター）
- 観光庁「宿泊旅行統計調査」（e-Stat 政府統計の総合窓口）
- 総務省統計局「令和2年国勢調査 従業地・通学地集計」（e-Stat 政府統計の総合窓口）
