#!/usr/bin/env python3
# RESAS由来 地域統計（観光）CSV → JSON 変換。
# data/raw/resas/*.csv を読み、data/resas/*.json を生成する。依存なし（標準ライブラリのみ）。
# 使い方: python3 scripts/resas-process.py
import csv, json, os, datetime, glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "data", "raw", "resas")
OUT = os.path.join(BASE, "data", "resas")
os.makedirs(OUT, exist_ok=True)
STAMP = datetime.date.today().isoformat()

def read_csv(name):
    p = os.path.join(RAW, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if any((v or "").strip() for v in row.values())]

def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0

def write(name, obj):
    obj["updatedAt"] = STAMP
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("wrote", os.path.join("data", "resas", name), "(", len(json.dumps(obj)), "bytes )")

# 滞在人口（月次・エリア別）
rows = read_csv("resas-stay.csv")
months = sorted({r["年月"].strip() for r in rows})
areas = []
for area in sorted({r["エリア"].strip() for r in rows}):
    by = {r["年月"].strip(): num(r["滞在人口"]) for r in rows if r["エリア"].strip() == area}
    areas.append({"name": area, "values": [by.get(m, None) for m in months]})
write("stay.json", {"months": months, "areas": areas})

# From-to（流入元・構成比を算出）
rows = read_csv("resas-fromto.csv")
origins = sorted([{"name": r["流入元"].strip(), "value": num(r["滞在人口"])} for r in rows],
                 key=lambda x: x["value"], reverse=True)
total = sum(o["value"] for o in origins) or 1
for o in origins:
    o["share"] = round(o["value"] / total, 4)
write("fromto.json", {"origins": origins, "total": total})

# 宿泊者数（月次・総数/外国人）
rows = read_csv("resas-lodging.csv")
rows = sorted(rows, key=lambda r: r["年月"].strip())
write("lodging.json", {
    "months": [r["年月"].strip() for r in rows],
    "total": [num(r["宿泊者数"]) for r in rows],
    "foreign": [num(r.get("うち外国人", 0)) for r in rows],
})
# 汎用：「名称,値」CSV → 構成比つきの降順リスト
def cat_csv(fname, name_key, val_key):
    rows = read_csv(fname)
    items = sorted([{"name": r[name_key].strip(), "value": num(r[val_key])} for r in rows if r.get(name_key)],
                   key=lambda x: x["value"], reverse=True)
    tot = sum(i["value"] for i in items) or 1
    for i in items:
        i["share"] = round(i["value"] / tot, 4)
    return {"items": items, "total": tot}

# 国内観光消費分析（宿泊旅行・一人一回当たりの旅行単価／全国・属性別）
# RESAS 観光マップ 国内観光消費分析の実CSVをそのまま取込。大分類（年齢/男女/…）ごとにグループ化。
sp = read_csv("resas-spend-per-trip.csv")
if sp:
    VALKEY = "単価（宿泊中）"
    groups, order = {}, []
    for r in sp:
        code, gname = r["大分類コード"].strip(), r["大分類名"].strip()
        if code not in groups:
            groups[code] = {"code": code, "name": gname, "items": []}
            order.append(code)
        groups[code]["items"].append({"name": r["中分類名"].strip(), "value": int(num(r[VALKEY]))})
    allv = [i["value"] for g in groups.values() for i in g["items"]]
    write("spend-per-trip.json", {
        "label": "宿泊旅行 一人一回当たりの旅行単価（宿泊中）",
        "scope": "全国（参考）",
        "scopeNote": "全国値（都道府県別内訳なし）。米沢ダッシュボードでは参考値。",
        "year": (sp[0]["集計年"].strip() if sp else ""),
        "period": (sp[0]["集計時期"].strip() if sp else ""), "unit": "円",
        "source": "RESAS 観光マップ 国内観光消費分析",
        "min": min(allv) if allv else 0, "max": max(allv) if allv else 0,
        "groups": [groups[c] for c in order],
    })
# 費目別（クレジットカード消費額分析・国内旅行／米沢市）。
# spend-per-trip.csv とは別データなので、両方あれば両方書く（以前は else で排他だった）。
if read_csv("resas-consumption-domestic.csv"):
    write("consumption-domestic.json", {"category": cat_csv("resas-consumption-domestic.csv", "費目", "金額")["items"]})
# ※ consumption-inbound.json は RESAS『クレジットカード分析（消費額）』訪日旅行の
#   画面キャプチャから手作業で転記した実データ（費目別総額・市町村/国別ランキング・時間帯/平日土日）。
#   旧・国籍別CSVサンプル生成ブロックはダミー値で実データを上書きしてしまうため削除した。
# クレジットカード分析（消費地・消費額費目別）
write("creditcard.json", {
    "area": cat_csv("resas-cc-area.csv", "消費地", "消費額")["items"],
    "category": cat_csv("resas-cc-category.csv", "費目", "消費額")["items"],
})
# ── 国内観光消費分析（統合：旅行単価＋購入者単価 × 宿泊/日帰り × 2023-2025） ──
# RESAS 観光マップ 国内観光消費分析の推移CSVを取込。全国値（都道府県別内訳なし＝参考）。
def numN(s):
    """空欄は None（該当なし）で保持。それ以外は int。"""
    t = str(s or "").replace(",", "").strip()
    if t == "":
        return None
    try:
        return int(float(t))
    except ValueError:
        return None

def build_trip(fname):
    """旅行単価：大分類（年齢/男女/…）→中分類（各値）を年次配列に。全空の大分類は除外。"""
    rows = read_csv(fname)
    if not rows:
        return None, []
    valkey = list(rows[0].keys())[-1]  # 単価（宿泊中）/（日帰り）
    years = sorted({r["集計年"].strip() for r in rows})
    attrs, order = {}, []
    for r in rows:
        code = r["大分類コード"].strip()
        if code not in attrs:
            attrs[code] = {"code": code, "name": r["大分類名"].strip(), "_items": {}, "_order": []}
            order.append(code)
        mid = r["中分類名"].strip()
        if mid not in attrs[code]["_items"]:
            attrs[code]["_items"][mid] = {y: None for y in years}
            attrs[code]["_order"].append(mid)
        attrs[code]["_items"][mid][r["集計年"].strip()] = numN(r[valkey])
    out = {}
    for code in order:
        a = attrs[code]
        items = [{"name": m, "values": [a["_items"][m][y] for y in years]} for m in a["_order"]]
        if all(v is None for it in items for v in it["values"]):
            continue  # 日帰りの宿泊施設・宿泊数など全空はスキップ
        out[code] = {"name": a["name"], "items": items}
    return years, out

def build_purchaser(fname):
    """購入者単価：大分類（費目）→ total（すべての中分類 X00）＋ sub（中分類）を年次配列に。"""
    rows = read_csv(fname)
    if not rows:
        return None, []
    valkey = list(rows[0].keys())[-1]
    years = sorted({r["集計年"].strip() for r in rows})
    cats, order = {}, []
    for r in rows:
        code = r["大分類コード"].strip()
        if code not in cats:
            cats[code] = {"code": code, "name": r["大分類名"].strip(),
                          "_total": {y: None for y in years}, "_sub": {}, "_suborder": []}
            order.append(code)
        mid_code = r["中分類コード"].strip()
        y = r["集計年"].strip()
        v = numN(r[valkey])
        if mid_code.endswith("00"):
            cats[code]["_total"][y] = v
        else:
            mid = r["中分類名"].strip()
            if mid not in cats[code]["_sub"]:
                cats[code]["_sub"][mid] = {yy: None for yy in years}
                cats[code]["_suborder"].append(mid)
            cats[code]["_sub"][mid][y] = v
    out = []
    for code in order:
        c = cats[code]
        total = [c["_total"][y] for y in years]
        sub = [{"name": m, "values": [c["_sub"][m][y] for y in years]} for m in c["_suborder"]]
        if all(v is None for v in total) and not any(v is not None for s in sub for v in s["values"]):
            continue  # 日帰りの宿泊費など全空はスキップ
        out.append({"code": code, "name": c["name"], "total": total, "sub": sub})
    return years, out

import re
def numF(s):
    """空欄は None。それ以外は float（小数保持。都道府県別の億円・万円/人・万人用）。"""
    t = str(s or "").replace(",", "").strip()
    if t == "":
        return None
    try:
        return round(float(t), 3)
    except ValueError:
        return None

def clean_cat(col):
    m = re.findall(r"（([^（）]*)）", col)
    return m[0] if m else col

def build_pref():
    """山形県（都道府県別）：訪問者数・旅行消費額・消費単価を訪問目的×費目×年で構造化。"""
    vis = read_csv("resas-yamagata-visitors.csv")
    spd = read_csv("resas-yamagata-spend.csv")
    upr = read_csv("resas-yamagata-unitprice.csv")
    if not (vis or spd):
        return None
    years = sorted({r["集計年"].strip() for r in (spd or vis)})

    def visitors_block(rows):
        purposes = {}
        for r in rows:
            p = r["訪問目的"].strip()
            purposes.setdefault(p, {y: None for y in years})[r["集計年"].strip()] = numF(list(r.values())[-1])
        return {"unit": "万人", "purposes": {k: [v[y] for y in years] for k, v in purposes.items()}}

    def cat_block(rows, unit):
        hdr = list(rows[0].keys())
        total_col, catcols = hdr[4], hdr[5:]
        cats = [clean_cat(c) for c in catcols]
        purposes = {}
        for r in rows:
            p, y = r["訪問目的"].strip(), r["集計年"].strip()
            d = purposes.setdefault(p, {"total": {yy: None for yy in years},
                                        "byCat": {c: {yy: None for yy in years} for c in catcols}})
            d["total"][y] = numF(r[total_col])
            for c in catcols:
                d["byCat"][c][y] = numF(r[c])
        out = {}
        for p, d in purposes.items():
            out[p] = {"total": [d["total"][y] for y in years],
                      "byCat": [[d["byCat"][c][y] for y in years] for c in catcols]}
        return {"unit": unit, "cats": cats, "purposes": out}

    return {"scope": "山形県", "years": years,
            "visitors": visitors_block(vis),
            "spend": cat_block(spd, "億円"),
            "unitprice": cat_block(upr, "万円／人")}

def build_inbound():
    """インバウンド消費分析：山形県（都道府県別2024/2025）＋全国籍＋22カ国比較（2025）。"""
    LASTVAL = lambda r: r[list(r.keys())[-1]]
    # ── 山形県（都道府県別） ──
    def yg_visitors(rows):
        years = sorted({r["集計年"].strip() for r in rows})
        p = {}
        for r in rows:
            p.setdefault(r["訪問目的"].strip(), {y: None for y in years})[r["集計年"].strip()] = numF(LASTVAL(r))
        return years, {"unit": "万人", "purposes": {k: [v[y] for y in years] for k, v in p.items()}}
    def yg_cat(rows, unit):
        years = sorted({r["集計年"].strip() for r in rows})
        hdr = list(rows[0].keys()); total_col, catcols = hdr[4], hdr[5:]
        cats = [clean_cat(c) for c in catcols]; purposes = {}
        for r in rows:
            p, y = r["訪問目的"].strip(), r["集計年"].strip()
            d = purposes.setdefault(p, {"total": {yy: None for yy in years},
                                        "byCat": {c: {yy: None for yy in years} for c in catcols}})
            d["total"][y] = numF(r[total_col])
            for c in catcols: d["byCat"][c][y] = numF(r[c])
        out = {p: {"total": [d["total"][y] for y in years],
                   "byCat": [[d["byCat"][c][y] for y in years] for c in catcols]} for p, d in purposes.items()}
        return {"unit": unit, "cats": cats, "purposes": out}
    vy_rows = read_csv("resas-inb-yamagata-visitors.csv")
    sp_rows = read_csv("resas-inb-yamagata-spend.csv")
    up_rows = read_csv("resas-inb-yamagata-unitprice.csv")
    yg_years, yg_vis = yg_visitors(vy_rows) if vy_rows else ([], None)
    yamagata = None
    if vy_rows:
        yamagata = {"scope": "山形県", "years": yg_years, "visitors": yg_vis,
                    "spend": yg_cat(sp_rows, "億円"), "unitprice": yg_cat(up_rows, "万円／人")}
    # ── 全国籍（00・2025） ──
    def group_by_daibunrui(rows, as_pct=False, only_total=False):
        out, order = {}, []
        for r in rows:
            g = r["大分類名"].strip()
            if g not in out: out[g] = []; order.append(g)
            mid_code = r["中分類コード"].strip(); mid = r["中分類名"].strip()
            v = (numF(LASTVAL(r)) if as_pct else numN(LASTVAL(r)))
            out[g].append({"name": mid, "code": mid_code, "value": v})
        res = {}
        for g in order:
            items = out[g]
            if only_total:
                tot = [x for x in items if x["code"].endswith("00")]
                res[g] = (tot[0]["value"] if tot else (items[0]["value"] if items else None))
            else:
                seg = [x for x in items if not x["code"].endswith("00")]
                use = seg if seg else items
                res[g] = [{"name": x["name"], "value": x["value"]} for x in use]
        return res, order
    national = None
    pur_rows = read_csv("resas-inb-national-purchaser.csv")
    con_rows = read_csv("resas-inb-national-consumption.csv")
    awa_rows = read_csv("resas-inb-national-awareness.csv")
    if pur_rows or con_rows:
        pur_map, pur_order = group_by_daibunrui(pur_rows, only_total=True) if pur_rows else ({}, [])
        con_map, con_order = group_by_daibunrui(con_rows) if con_rows else ({}, [])
        awa_map, awa_order = group_by_daibunrui(awa_rows, as_pct=True) if awa_rows else ({}, [])
        national = {"scope": "全国籍・地域", "year": "2025",
                    "purchaser": [{"name": g, "value": pur_map[g]} for g in pur_order],
                    "consumption": con_map, "consumptionOrder": con_order,
                    "awareness": awa_map, "awarenessOrder": awa_order}
    # ── 22カ国比較（フル・マトリクス／2025） ──
    # data/raw/resas/inbound-countries/ の全CSVを読み、指標→大分類→中分類→国別ランキングに整形。
    # 分類：選択率列=意識(awareness)／大分類が費目=購入者単価(purchaser)／その他=消費単価(consumption)。
    def read_csv_path(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            return [row for row in csv.DictReader(f) if any((v or "").strip() for v in row.values())]
    FEE = {"宿泊費", "飲食費", "交通費", "娯楽等サービス費", "買物代", "その他"}
    cfolder = os.path.join(RAW, "inbound-countries")
    matrix = {"purchaser": {}, "consumption": {}, "awareness": {}}
    if os.path.isdir(cfolder):
        for p in sorted(glob.glob(os.path.join(cfolder, "*.csv"))):
            for r in read_csv_path(p):
                if r.get("国コード", "").strip() in ("00", "99"):
                    continue
                lastcol = list(r.keys())[-1]
                dai, mid = r["大分類名"].strip(), r["中分類名"].strip()
                if lastcol == "選択率":
                    typ, val = "awareness", numF(r[lastcol])
                elif dai in FEE:
                    typ, val = "purchaser", numN(r[lastcol])
                else:
                    typ, val = "consumption", numN(r[lastcol])
                if val is None:
                    continue
                matrix[typ].setdefault(dai, {}).setdefault(mid, {})[r["国名"].strip()] = val
    def finalize(m):
        return {dai: {mid: sorted([{"name": k, "value": v} for k, v in cs.items()],
                                  key=lambda x: x["value"], reverse=True)
                      for mid, cs in mids.items()}
                for dai, mids in m.items()}
    countries = None
    if any(matrix[t] for t in matrix):
        countries = {t: finalize(matrix[t]) for t in matrix}
    if not (yamagata or national or countries):
        return None
    return {"label": "インバウンド消費分析", "source": "RESAS 観光マップ インバウンド消費分析",
            "year": "2025", "period": "すべての期間",
            "note": "訪日外国人消費動向調査ベース。山形県=都道府県別(2024/2025)、全国籍・国別=2025。単価は円、山形県の消費額=億円・単価=万円/人・訪問者数=万人。",
            "yamagata": yamagata, "national": national, "countries": countries}

py, price_stay = build_trip("resas-price-stay.csv")
_,  price_day = build_trip("resas-price-day.csv")
_,  pur_stay = build_purchaser("resas-purchaser-stay.csv")
_,  pur_day = build_purchaser("resas-purchaser-day.csv")
if price_stay or pur_stay:
    write("domestic.json", {
        "label": "国内観光消費分析（一人一回当たり単価）",
        "scope": "全国（参考）",
        "scopeNote": "本指標は全国値（都道府県別の内訳なし）。米沢ダッシュボードでは参考値として掲載。",
        "source": "RESAS 観光マップ 国内観光消費分析",
        "years": py or [],
        "unit": "円",
        "price": {"stay": price_stay, "day": price_day},
        "purchaser": {"stay": pur_stay, "day": pur_day},
        "yamagata": build_pref(),
    })
inb = build_inbound()
if inb:
    write("inbound.json", inb)

print("done. 実データに差し替えたら再実行してください。")
