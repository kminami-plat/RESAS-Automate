# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

リポジトリの記述言語は日本語（docstring・ログ・出力ノートすべて）。合わせること。

## 何をするツールか

山形県米沢市の「滞在人口 / From-to（流入元）/ 宿泊者数 / クレジットカード消費額 / 旅行単価」を
公的オープンデータから取得し、**列名が固定された7つのCSV**
（`resas-stay.csv` / `resas-fromto.csv` / `resas-lodging.csv` /
`resas-cc-area.csv` / `resas-cc-category.csv` / `resas-consumption-domestic.csv` /
`resas-spend-per-trip.csv`）を
`data/raw/resas/` に生成する（出力先は `cli.DEFAULT_OUT`。`--out` で変更可）。
同名の `.md`（出典・制約）も同じディレクトリに並べて出す。
列仕様は `writers.HEADERS` が単一の情報源。

外部依存ゼロ（標準ライブラリのみ）。この方針は維持すること。

## コマンド

```bash
# セットアップ（venvのpythonを明示的に叩く。`python3`はanaconda等を拾って壊れることがある）
/usr/local/bin/python3 -m venv .venv
.venv/bin/python -m pip install -e .

# 実行
.venv/bin/resas-automate fetch                        # 7種まとめて
.venv/bin/resas-automate -v fetch --kind fromto       # 種別を絞る／詳細ログ（-v は fetch の前）
.venv/bin/resas-automate fetch --kind lodging --lodging-area city   # 米沢市の月次宿泊者数
.venv/bin/resas-automate fetch --kind cc-category --cc-year 2025 --cc-period all  # 2025年通年
.venv/bin/resas-automate estat-meta                   # API側の分類コードを一覧
.venv/bin/resas-automate estat-files                  # ファイル配布側の収録年月を一覧
.venv/bin/resas-automate resas-visa-meta              # クレカ消費額の収録年・四半期を一覧
.venv/bin/resas-automate fetch --kind spend-per-trip --spend-transition year  # 旅行単価の推移

# 構文チェック（テストスイートは無い）
.venv/bin/python -m compileall -q src
```

### この環境では `.venv/bin/resas-automate` が動かないことがある

`pip install -e .` 後に **新しいモジュールを追加したら再インストール不要**（`.pth` 参照）。
ただし本リポジトリは iCloud同期下の Desktop にあり、**同期が `.venv` 配下に
macOSの `hidden` フラグ（`UF_HIDDEN`）を付け直す**。
Python 3.13以降の `site.addpackage` は **hidden な `.pth` を黙って無視する**ため、
editable install が効かず `ModuleNotFoundError: No module named 'resas_automate'` になる。

`chflags nohidden` は同期で戻されるので恒久策にならない。**venvを作り直しても直らない。**
確実に動かすなら次のどれか:

```bash
PYTHONPATH=src .venv/bin/python -m resas_automate fetch   # 一番手軽。CIでもこれ
.venv/bin/python -m pip install .                         # 非editable（.pth不要）にする
```

リポジトリを iCloud 同期外（例 `~/src/`）へ移すのが根本解決。

## 前提：RESAS本体から取れるものと取れないもの

これがこの設計全体の理由なので、最初に理解すること。

- **旧RESAS-API（opendata.resas-portal.go.jp）は 2025-03-24 に提供終了**。復活しない。
- 現行 resas.go.jp は Next.js の完全クライアントレンダリングで、サーバサイドHTMLは無い。
- **現行RESASに From-to分析（滞在人口）機能そのものが存在しない**。
  滞在人口・宿泊者数・From-to をRESASから取る道は無いので、
  この3種のソースは**代替物**であり、RESASと同じ値は出ない。

**ただし2026-06-18のRESAS刷新で「クレジットカード消費地分析／消費額分析」が新設された。**
これだけは現行RESASそのものの値を直接取れる（次節）。
つまり「RESASからは何も取れない」は**もう正しくない**。新しい分析メニューが増えたときは
JSバンドルを見て `api.resas.go.jp` を叩いているか確かめること。

### 現行RESASの内部APIの調べ方

`resas.go.jp` は Akamai 相当のbot対策が入っていて、**素の `curl -A "..."` は403を返す**
（403でもSPAのシェルHTMLが返るので、雑に走査すると「0ヒット」という誤った結論が出る。
以前この誤りをやっている）。次のヘッダが揃うと通る:

```
User-Agent: <ブラウザのUA>   Referer: https://resas.go.jp/
Sec-Fetch-Dest: script       Sec-Fetch-Mode: no-cors   Sec-Fetch-Site: same-origin
```

- ルート一覧・チャンク一覧は `https://resas.go.jp/<route>/index.txt`（App RouterのRSCペイロード）。
- 遅延チャンクのハッシュ表は `webpack-*.js` の `r.u=e=>...` に入っている。
- 分析メニューのキー一覧（`tourism-credit-consumption-amount` など）は共通チャンクに
  `{key:...,value:...}` の配列で入っている。

## アーキテクチャ

`cli.py` がオーケストレーション、`sources/*` が取得元ごとのアダプタ、`writers.py` が出力の門番。
sources は互いに独立で、`cli.cmd_fetch` だけが束ねる。

`xlsx.py` は標準ライブラリだけのxlsx読み取り（openpyxl を入れないため）。
書式を解釈せず結合セルも展開しないので、**見出しは `xlsx.norm()` を通して
名称マッチで解決する**（政府統計のセルにはルビと注記がくっついてくる）。

各 source は「取れる全期間を返す」だけで、**期間の絞り込みはしない**（`cli` の責務）。
`mlit_jinryu.load()` / `estat_lodging.fetch_lodging()` が期間引数を持たないのは意図的。

### 取得元ごとに収録範囲の天井が違う

| source | 収録範囲 | 鍵 | 経路 |
|---|---|---|---|
| `mlit_jinryu` | 2019-01〜2021-12 | 不要 | 配信ZIP |
| `estat_census_fromto` | 2020年（国勢調査） | 要 | e-Stat API |
| `estat_lodging` | 2014-01〜2016-12 | 要 | e-Stat API（統計データベース） |
| `estat_lodging_files`（宿泊者数） | 2015年〜**最新月** | 不要 | e-Stat ファイル配布のExcel |
| `estat_lodging_files`（From-to） | 2015-04〜**最新月** | 不要 | 同上（参考第2表・居住地別） |
| `resas_visa`（クレカ消費額） | 2025年〜**最新四半期** | 不要 | 現行RESASの内部API |
| `resas_tourism_domestic`（旅行単価） | 2023〜2025年 | 不要 | 同上（ダウンロードZIP） |

天井がバラバラなので `cli.select_year_months()` が**ソースごとに独立して**
「今年 → 無ければ収録最新年」へフォールバックする。既定実行では stay=2021年、
lodging=最新年 と別々の年が出るのが正常。

### 列名と中身が一致しない箇所がある（意図的）

`resas-fromto.csv` の列は仕様上 `滞在人口` だが、既定の `census` 経路で入る値は
**国勢調査の通勤・通学者数**（2020年時点）。月次の滞在人口を自治体名単位まで
分解した公的データは存在しないため、これが自動取得の上限。

このギャップを埋めるのが `writers.sidecar_note()`。CSV本体は列仕様どおりに保ち、
**出典・集計条件・制約は同名の `.md` に必ず書き出す**。
新しい source を足すときも sidecar は必須。CSVだけ出して済ませないこと。

### クレジットカード消費額（`resas_visa`）は現行RESASの内部APIから取る

`resas-cc-area.csv` / `resas-cc-category.csv` / `resas-consumption-domestic.csv` の3つは、
2026-06-18に新設された **RESAS「クレジットカード消費額分析」**（`/tourism-credit-consumption-amount`）
そのものの値。画面が `https://api.resas.go.jp/v2/tourism/visa-spending/…` を叩いており、
**鍵もログインもCookieも不要**。Seleniumも要らない。

- データ提供は **Visa Consulting & Analytics**。JCB／ナウキャストだった旧・消費マップとは
  **別系列で、値は接続しない**。sidecar に必ずそう書くこと。
- `api.resas.go.jp` は **ブラウザのUA・`Origin`・`Referer` の3つが揃わないと403**。
  どれか1つでも欠けると通らない（`resas_visa._HEADERS`）。この用途のために
  `http.fetch` / `fetch_json` に `headers` 引数を足してある。
- 使うエンドポイントは2つだけ。
  `category-circle-bar`（費目大分類の円グラフ）と `to-bar`（消費地別ランキングの棒グラフ）。
  他に `from-bar`（居住地別）・`*-line`（推移）・`day/time-*`（平日土日・時間帯）や、
  `visa-flow/*`（入込人数・滞在時間・前後経路＝消費地分析側）もある。

#### 返るのはデータ表ではなくECharts の option

画面がグラフ描画に `optionUrl` を渡す作りなので、レスポンスは描画設定そのもの。

- 円グラフ: `series[0].data[i].value` が実数、`name` は
  `"宿泊費 14,090万円 (14.98%)"` という**表示用文字列**。分類名は最初の空白より前。
- 棒グラフ: `xAxis.data` がラベル、`series[0].data[i].value` が値。
- 単位は円グラフなら `title[0].text`、棒グラフなら `yAxis.name`（`（万円）`）。
  **市区町村は万円、全国は百万円**とレベルで変わるので、必ず応答から読むこと。
- **データが無い期間は 404 ではなく 200 + `{"option": {}}`**。ここを見落とすと空振りに気付けない。

分類名・単位をハードコードしないのは e-Stat 側と同じ方針。

#### パラメータは FastAPI の enum で守られている

不正値を送ると 422 と一緒に **許容値の一覧が返ってくる**ので、仕様調査はこれが一番速い。

```
{"detail":[{"loc":["query","parameter"],"msg":"value is not a valid enumeration member;
  permitted: 'value', 'transition'", ...}]}
```

`country` は必須で、全体なら `00`（全国籍・地域）。忘れると 422。

#### 収録範囲と既定の期間

四半期更新。実測（2026-09）で **2025年（月次・四半期・通年）と2026年 第1四半期のみ**、
2024年以前は空。天井が動くので `resas_visa.resolve_year()` が実際に叩いて確かめる。

`--cc-period auto`（既定）は **4四半期そろっていれば通年、欠けていれば収録最新の四半期**を選ぶ。
`month=all` は「収録済みの四半期の合計」を返すだけなので、そのまま通年扱いすると
1四半期分を1年分に見せてしまう。この分岐はそのために入っている。

#### 消費地は市区町村までで、市内の地区別は無い

`resas-cc-area.csv` の `消費地` は**決済が行われた市区町村**（既定は山形県内の全32市区町村）。
「小野川温泉」「上杉神社周辺」のような市内の地区別内訳は**Visaデータに存在しない**。
`--cc-area-level pref` にすると47都道府県になる。

#### cc-category と consumption-domestic は同じAPIの別ラベル

どちらも費目大分類の消費総額。違いは2点だけ:

- `cc-category` は `--cc-visitor` で 国内／訪日 を切替、費目はRESASの正式名称
  （宿泊費・飲食費・交通費・娯楽等サービス費・買物代・その他）。
- `consumption-domestic` は**国内旅行に固定**、費目はダッシュボードの短縮表記
  （宿泊・飲食・交通・娯楽・体験・土産・買物・その他）。対応表は `cli._SHORT_CATEGORY`。

`--cc-visitor domestic` のとき両者の数値は同じになる。これは意図した重複で、
ダッシュボード側が別パネルとして読むため。1回のAPI呼び出しを使い回している。

### 旅行単価（`resas_tourism_domestic`）は配信元CSVをそのまま通す

`resas-spend-per-trip.csv` は RESAS「国内観光消費分析」（`/tourism-domestic`）の
**ダウンロードボタンと同じもの**。元データは **観光庁「旅行・観光消費動向調査」**で、
Visaのクレカデータ（`resas_visa`）とは**別系列**。混同しないこと。

- 経路は `https://api.resas.go.jp/v2/download{downloadUri}`。接続は `resas_api` 共通。
- **応答はJSONではなくZIP**（`application/zip`）。中身は **Shift_JIS（cp932）のCSV1本**。
  ZIP内のファイル名はUTF-8フラグ付きなので `zipfile` がそのまま復号する。
- この種別だけ**値を組み立て直さない**。列は配信元のまま、UTF-8 BOMに置き直すだけ。
  そのため `writers.write()` に `header=` を渡せるようにしてある
  （**最終列だけが `単価（宿泊中）` / `単価（日帰り）` と旅行種類で変わる**ため）。
  他の種別では使わないこと。HEADERS が単一の情報源という原則は崩していない。
- **範囲外の年でも422にならず、見出しだけのCSV（289バイト・1行）が返る。**
  データ行が0件かどうかでしか未収録を判定できない。`fetch_latest()` がそれをやる
  （取得と判定を分けると同じZIPを2回落とすので1回にまとめてある）。
- `--spend-transition year|quarter` にすると単年ではなく**収録全年の推移**が1本で返る。

同じ画面の「都道府県別」タブには別のダウンロードURIがある（未実装）:
`/tourism/tourism-domestic/prefecture/transition?pref=06&itemType=2` で
**山形県の訪問目的×費目×年の旅行消費額（億円）**が返る。
`itemType` は 0=訪問者数 / 1=消費単価 / 2=旅行消費額。
`scripts/resas-process.py` の `build_pref()` が読む `resas-yamagata-*.csv` はこれ。

### e-Stat には「統計データベース」と「ファイル配布」の2経路がある

これを混同すると「宿泊者数は2016年まで」という誤った結論に戻るので注意。

- **統計データベース（`getStatsData` / `estat_lodging.py`）**
  `statsDataId` は**期間固有で、時系列の通し番号ではない**。`0003314421` は2014-2016しか持たない。
  「同じ表の最新版」が別IDで存在するとは限らず、宿泊旅行統計調査の2017年以降は
  そもそもDB登録が無い（`getStatsList` に `statsCode=00601020` を投げても
  32表・全て2014-2016）。**APIをどう叩いても2017年以降は出てこない。**

- **ファイル配布（`estat_files.py` + `estat_lodging_files.py`）**
  2017年以降はExcelでのみ配布されている。ファイル検索画面はJSレンダリングだが、
  画面自身が叩いている **`/retrieve/api_file` がプレーンHTTPでJSONを返す**
  （ログイン・トークン不要）。よって**Seleniumは不要**で標準ライブラリだけで辿れる。
  階層は 政府統計(toukei) → 提供統計(tstat) → 提供周期(cycle) → 調査年月 → statInfId。
  `month` は `11010302` のような8桁コードなので、**組み立てず一覧のリンクから読む**。

  ブラウザ経路（`sources/browser.py`・Selenium）は配信元がJS専用に変わった場合の
  逃げ道として `--use-browser` の裏にだけ置いてある。**任意依存**（`pip install -e ".[browser]"`）で、
  本体の「外部依存ゼロ」は崩していない。
- **分類コードはハードコードせず、`getMetaInfo` の名称マッチで解決する**
  （`_find_obj` / `_find_code`）。表の改訂で番号が変わっても壊れにくくするため。
  解決に失敗したら `estat-meta` で実際の分類を見るのが定石。
- レスポンスの封筒は `GET_META_INFO` / `GET_STATS_DATA` の下。`_call()` が剥がして
  `RESULT.STATUS != 0` を `EstatError` に変換する。生の `fetch_json` を直接使わないこと。

### Excelの表は「番号」ではなく「タイトルの語」で特定する

宿泊旅行統計調査は改訂のたびに表番号も集計区分も変わる。実測（2026-09）:

| | 施設規模の区分 | 全施設 | 市区町村別 |
|---|---|---|---|
| 年確定値（〜2025年） | 従業者数 | 第2表 | **無い** |
| 第2次速報値（2015-04〜） | 客室数 | 第2表 | 参考第6表（総数）／参考第8表（外国人） |
| 第1次速報値 | ― | **全国計のみ** | 無い |

`estat_lodging_files._SHEET_RULES` は必須語・除外語でシートを選ぶ。
シート名や番号でマッチさせないこと（`参考第3表` は年確定値では
「従業者数10人以上の延べ宿泊者数」だが、第2次速報値では
「居住地別延べ宿泊者数」で全く別物）。

**第1次速報値は候補に入れない**（`PREFER_PREF` / `PREFER_CITY`）。
全国計しか無いので、最新月ほしさに入れると毎回ダウンロードして必ず空振りする。

### From-to：公的統計側に手は無い。ただしRESASの新APIに有力な候補がある

「宿泊と同じ要領で fromto も最新化できないか」は検討済み。**公的統計側は行き止まり**:

- **国勢調査 従業地・通学地集計は令和2年（2020年）が最新**。令和7年調査は実施済みだが
  この集計は未公表（令和2年は調査の約2年後に公表）。DBにもファイル配布にも無い。
- **人流オープンデータは凍結**。山形県リソースの `last_modified` は 2022-01-13 で、
  中身は 2019〜2021 のまま（CKAN で確認済み）。2022年以降は存在しない。

**未実装の有力候補（2026-09に発見）**: `resas_visa` で使っている同じRESAS内部APIに
`v2/tourism/visa-flow/from-bar`（クレジットカード消費地分析の「居住地別 入込人数」）がある。
実測で **米沢市・2025年通年・843市区町村・単位は人**が返る
（山形市76,736 / 仙台市57,992 / 福島市48,068）。**自治体名単位・四半期更新**で、
現状の census 経路（2020年の通勤・通学者数）より遥かに新しく、観光の人流に近い。

ただし乗り換えるなら次を確認してから。値の性質が census とまったく違う:
Visa会員の決済に基づく**推定居住地**であり、`滞在人口` でも実人数でもない。
既知の不変条件（合計10,423）は当然崩れるので、検証値も差し替えること。
パラメータは `visa-spending` と同じ（`country=00` 必須）。

唯一「最新の流入元らしきもの」は宿泊旅行統計の**参考第2表（居住地47区分別延べ宿泊者数）**で、
`--fromto-source lodging` で取れる。ただし**既定にはしない**。実測（2026年）での制約:

| 制約 | 中身 |
|---|---|
| 地域 | **山形県**単位。米沢市の居住地内訳は存在しない |
| 対象施設 | 大規模施設のみ。しかも**月で変わる**（2〜4月=20室以上 / 5〜6月=200室以上） |
| カバー率 | 県の全施設 延べ宿泊者数の数%（2026-06 は 12,098 / 383,880 ＝ 3.2%） |
| 居住地不詳 | 総数の **0〜30%**（2026-04 は 30.3% で不詳が最大カテゴリ） |
| 確報 | 年確定値に居住地47区分の表は無いので、確報に置き換わらない |

運輸局の列（北海道運輸局〜沖縄総合事務局）は都道府県の積み上げなので**必ず落とす**。
`read_fromto()` は内訳合計が総数を**超えたら例外**（＝集計列を拾ったバグ）、
**下回ったら差分を「不詳」行に立てる**（＝実データの仕様）。この非対称は意図的。

### 米沢市の月次宿泊者数は取れる（README の旧記述は誤り）

第2次速報値の参考第6表・第8表が「施設所在地（主な市区町村）」別で、
**山形県米沢市が載っている**（例 2026-06: 延べ15,163人泊 / うち外国人191）。
`--lodging-area city` がこれを使う。ただし:

- 掲載は「主な市区町村」のみで、**掲載される市区町村は月によって変わる**
  （2026-03 は米沢市の行が無い）。月が飛ぶのは異常ではない。取れた月だけ出し、
  抜けは sidecar の「欠測」に書く。
- 年確定値に市区町村別は無いので、**この値が確報に置き換わることはない**。

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

宿泊（2つの経路が同じ値を出すことが一番効く検証）:

- **API経路とファイル配布経路は2016年で一致する**。
  `fetch --kind lodging --lodging-source api --year 2016` と
  `--lodging-source files --lodging-table over10 --year 2016` の出力は**完全一致**する
  （2016-01 山形県 = 301,410 / 9,080）。ここがズレたらExcel側の列解決のバグ。
- 同じ2016-01でも `--lodging-table all`（全施設）は **393,130 / 12,330** で、
  `over10` とは別系列。**違って正しい**。
- 2026-06 山形県（第2次速報・全施設）= **383,880 / 12,660**、
  同月の米沢市 = **15,163 / 191**。

旅行単価（`resas_tourism_domestic`）:

- 既定（2025年・すべての期間・宿泊旅行・旅行単価）は **48行**。
  先頭 `2025,すべての期間,A,年齢,A01,9歳以下,48670`、末尾 `…,F,宿泊数,F08,8泊以上,86662`。
- `--spend-cost-type 1`（購入者単価）は **36行**、`--spend-transition year` は **144行**（3年分）。
- 2026年を指定すると未収録判定で2025年に落ちる（エラーにはならない）。

クレジットカード消費額（`resas_visa`。ここは合計の一致が3重に効く）:

- **費目別の合計 ＝ 消費地別ランキングの米沢市の値**（別エンドポイント同士の突き合わせ）。
  2025年通年・国内旅行で **425,118万円**。ここがズレたら option の読み取りバグ。
- **4四半期の合計 ＝ 通年**、**12か月の合計 ＝ 通年**。どちらも 425,118 になる
  （四半期は 68,889 / 117,771 / 134,933 / 103,525）。
- 2026年 1-3月期・国内旅行・米沢市 = **94,090万円**
  （宿泊費14,090 / 飲食費16,943 / 交通費4,110 / 娯楽等サービス費16,600 / 買物代42,338 / その他9）。
- 同期間の消費地別（山形県内32市区町村）は 山形市351,525 / 天童市115,946 / 米沢市94,090。
- 2025年通年・**訪日旅行**・米沢市 = 12,021万円。RESAS画面の表示は 12,022万円 で、
  **行ごとに整数へ丸めた差**。sidecar に自動で注記が入る（`Provenance.reported_total`）。
  この1単位差は仕様であってバグではない。

From-to:

- `--fromto-source census`（既定）の合計は **10,423**（上記のとおり）。
- `--fromto-source lodging --fromto-month 2026-06` は
  47都道府県＋国外の48件で合計 **12,098**（不詳0）。山形県3,190 / 宮城県1,805 / 東京都974。
  `--fromto-top` で絞っても「その他」込みの合計はこの値。
- 同 `--fromto-month 2026-04` は不詳 **9,572（30.3%）** が立ち、合計 **31,588**。
  不詳行が消えていたら差分処理のバグ。

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
