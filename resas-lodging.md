# resas-lodging.csv の出典

- 対象: 山形県（延べ宿泊者数 / うち外国人）
- 対象期間: 2016-01〜2016-12（年月の昇順）
- 出典: 観光庁 宿泊旅行統計調査 参考第３表（e-Stat） https://www.e-stat.go.jp/stat-search/database?layout=dataset&statdisp_id=0003314421
- API取得範囲: 2014-01〜2016-12（このstatsDataIdはこの期間のみ「統計データベース」として登録されている）

## 注意
宿泊旅行統計調査の公表単位は都道府県であり、
山形県米沢市単独の月次宿泊者数は全国統計としては公表されていない。

2017年以降の値はe-Stat APIから取得できない（ファイル配布のみ）。必要な場合は https://www.e-stat.go.jp/stat-search/files?toukei=00601020 から対象月のファイルを手動DLし、
市町村別が必要な場合とあわせて、
`python -m resas_automate manual --kind lodging` で変換すること。

※ 2026年は未公表のため、公表最新年 2016年を出力している。
