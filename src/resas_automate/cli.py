"""コマンドラインインタフェース。"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

from . import areas, writers
from .sources import (
    estat_census_fromto,
    estat_files,
    estat_lodging,
    estat_lodging_files,
    manual_import,
    mlit_jinryu,
)

ROOT = Path(__file__).resolve().parents[2]
# CSVと sidecar の .md はここに出す。ダッシュボード側（resas-process.py）が
# 読む「生データ置き場」の相対パスに合わせてある。--out で変更可。
DEFAULT_OUT = ROOT / "data" / "raw" / "resas"
DEFAULT_CACHE = ROOT / "cache"
DEFAULT_INPUT = ROOT / "input"
DOTENV_PATH = ROOT / ".env"

log = logging.getLogger("resas_automate")


def load_dotenv(path: Path = DOTENV_PATH) -> None:
    """.env の KEY=VALUE を環境変数へ読み込む（未設定のキーのみ・依存ライブラリ不要）。

    既にシェル側で export 済みの環境変数は上書きしない。
    そのため `ESTAT_APP_ID=xxx python3 -m resas_automate ...` のような
    一時的な指定は .env より優先される。
    """
    if not path.is_file():
        return
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            log.warning("%s:%d を無視します（KEY=VALUE 形式ではありません）", path.name, lineno)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_months(spec: str | None) -> list[str] | None:
    """'2021-01:2021-12' もしくは '2021-07,2021-08' を年月リストに展開する。"""
    if not spec:
        return None
    if ":" in spec:
        start, end = (s.strip() for s in spec.split(":", 1))
        sy, sm = (int(x) for x in start.split("-"))
        ey, em = (int(x) for x in end.split("-"))
        if (ey, em) < (sy, sm):
            raise SystemExit(f"--months の範囲が逆順です: {spec}")
        out, y, m = [], sy, sm
        while (y, m) <= (ey, em):
            out.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    return [s.strip() for s in spec.split(",") if s.strip()]


def select_year_months(available: list[str], year: int) -> tuple[list[str], int]:
    """指定年のうち実際に収録されている年月を昇順で返す。

    その年のデータが1件も無ければ、収録されている最新年に自動で切り替える
    （人流オープンデータは2021年で更新が止まっているため）。
    """
    if not available:
        raise RuntimeError("収録年月が取得できませんでした")
    in_year = sorted(m for m in available if m.startswith(f"{year:04d}-"))
    if in_year:
        return in_year, year
    fallback = max(int(m[:4]) for m in available)
    log.warning(
        "%d年のデータは未収録のため、収録最新年の %d年に切り替えます", year, fallback
    )
    return sorted(m for m in available if m.startswith(f"{fallback:04d}-")), fallback


# --------------------------------------------------------------------------- fetch


def cmd_fetch(args: argparse.Namespace) -> int:
    explicit_months = parse_months(args.months)
    target = areas.resolve_areas(args.areas)
    out_dir = Path(args.out)
    cache_dir = Path(args.cache)
    kinds = args.kind or ["stay", "fromto", "lodging"]
    failures: list[str] = []

    def resolve_app_id(what: str) -> str:
        app_id = args.app_id or os.environ.get("ESTAT_APP_ID", "")
        if not app_id:
            log.error(
                "%sの取得には e-Stat のアプリケーションIDが必要です。"
                "https://www.e-stat.go.jp/api/ で無料登録し、.env に ESTAT_APP_ID を設定してください。",
                what,
            )
        return app_id

    # 人流オープンデータが要るのは滞在人口と、From-toをjinryu経路で出す場合だけ
    needs_jinryu = "stay" in kinds or (
        "fromto" in kinds and args.fromto_source == "jinryu"
    )

    monthly: dict[str, list[dict[str, str]]] | None = None
    stay_year: int | None = None
    if needs_jinryu:
        try:
            everything = mlit_jinryu.load(
                cache_dir=cache_dir, dayflag=args.dayflag, timezone=args.timezone
            )
            if explicit_months:
                missing = sorted(set(explicit_months) - everything.keys())
                if missing:
                    log.warning(
                        "人流オープンデータに未収録の年月: %s（提供期間は %s〜%s）",
                        ", ".join(missing),
                        mlit_jinryu.AVAILABLE_FROM,
                        mlit_jinryu.AVAILABLE_TO,
                    )
                months = [m for m in explicit_months if m in everything]
            else:
                months, stay_year = select_year_months(sorted(everything), args.year)
            if not months:
                raise RuntimeError("対象年月に該当するデータがありません")
            monthly = {m: everything[m] for m in months}
            log.info("滞在人口の対象期間: %s〜%s", months[0], months[-1])
        except Exception as exc:  # noqa: BLE001 - 他の種別は続行させたい
            log.error("人流オープンデータの取得に失敗: %s", exc)
            failures.append("人流オープンデータ")

    if "stay" in kinds and monthly:
        rows = mlit_jinryu.stay_rows(monthly, target)
        path = writers.write("stay", rows, out_dir)
        writers.sidecar_note(
            path,
            [
                "# resas-stay.csv の出典",
                "",
                f"- 対象地域: {areas.TARGET_AREA_LABEL}",
                f"- 対象期間: {rows[0][0]}〜{rows[-1][0]}（年月の昇順）",
                f"- 出典: 国土交通省 全国の人流オープンデータ（1kmメッシュ、市区町村単位発地別） {mlit_jinryu.SOURCE_URL}",
                f"- 集計: {areas.DAYFLAG_LABELS.get(args.dayflag, args.dayflag)} / "
                f"{areas.TIMEZONE_LABELS.get(args.timezone, args.timezone)}、発地区分（同市区町村〜それ以外）を合算",
                "- 滞在人口は1か月間における1日あたりの平均値",
                f"- 収録期間: {mlit_jinryu.AVAILABLE_FROM}〜{mlit_jinryu.AVAILABLE_TO}（配信元の提供範囲）",
            ]
            + (
                [
                    "",
                    f"※ {args.year}年は未収録のため、収録最新年 {stay_year}年を出力している。",
                ]
                if stay_year is not None and stay_year != args.year
                else []
            ),
        )

    if "fromto" in kinds and args.fromto_source == "census":
        app_id = resolve_app_id("流入元（自治体名単位）")
        if not app_id:
            failures.append("国勢調査 従業地・通学地集計")
        else:
            try:
                rows, total = estat_census_fromto.fromto_rows(
                    app_id, target, top=args.fromto_top
                )
                path = writers.write("fromto", rows, out_dir)
                log.info(
                    "流入元: %d件（%s への流入合計 %d人）", len(rows),
                    "・".join(target.values()), total,
                )
                writers.sidecar_note(
                    path,
                    [
                        "# resas-fromto.csv の出典",
                        "",
                        f"- 対象地域: {'・'.join(target.values())}（への流入）",
                        f"- 出典: {estat_census_fromto.SURVEY_LABEL}（e-Stat） "
                        f"{estat_census_fromto.SOURCE_URL}",
                        f"- 流入合計: {total:,}人（他市区町村に常住する就業者・通学者）",
                        f"- 並び: 人数の降順"
                        + (f"、上位{args.fromto_top}件＋その他" if args.fromto_top else "、全件"),
                        "",
                        "## この数値が何であるか（重要）",
                        "値は**通勤・通学者数**（2020年10月時点、国勢調査は5年に1度）であり、",
                        "観光を含む月次の「滞在人口」ではない。列名は仕様に合わせて `滞在人口` としている。",
                        "",
                        "RESAS旧サイトの「From-to分析（滞在人口）」は提供終了しており、",
                        "現行RESASにFrom-to分析機能は存在しない。",
                        "自治体名単位の流入元を公的APIから自動取得できるのは現状この国勢調査系のみ。",
                        "",
                        "## 集計方法",
                        "従業地・通学地を対象自治体に固定し、常住地を市区町村単位で展開している。",
                        "政令市の区と東京23区は親自治体（政令市本体／東京都区部）に寄せてあり、",
                        "市区町村の合算は「他市区町村に常住」の総数と一致する（二重計上なし）。",
                        "",
                        "月次の滞在人口が必要な場合は `--fromto-source jinryu`（発地4区分どまり）、",
                        "または外部で入手したCSVを `resas-automate manual --kind fromto` で取り込むこと。",
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                log.error("国勢調査 従業地・通学地集計の取得に失敗: %s", exc)
                failures.append("国勢調査 従業地・通学地集計")

    elif "fromto" in kinds and args.fromto_source == "lodging":
        try:
            _fetch_fromto_lodging(args, out_dir, cache_dir)
        except Exception as exc:  # noqa: BLE001
            log.error("宿泊旅行統計（居住地別）の取得に失敗: %s", exc)
            failures.append("宿泊旅行統計（居住地別）")

    elif "fromto" in kinds and monthly:
        rows, ym = mlit_jinryu.fromto_rows(monthly, target, month=args.fromto_month)
        path = writers.write("fromto", rows, out_dir)
        writers.sidecar_note(
            path,
            [
                "# resas-fromto.csv の出典",
                "",
                f"- 対象地域: {areas.TARGET_AREA_LABEL}",
                f"- 対象年月: {ym}（列仕様に年月が無いため、対象期間の最新月のスナップショット）",
                f"- 出典: 国土交通省 全国の人流オープンデータ {mlit_jinryu.SOURCE_URL}",
                "",
                "## 注意",
                "この経路の流入元は 同市区町村 / 同都道府県 / 同地方 / それ以外 の4区分であり、",
                "「山形市」のような自治体名単位ではない（配信データの仕様）。",
                "自治体名単位が必要な場合は `--fromto-source census`（既定）を使うこと。",
            ],
        )

    if "lodging" in kinds:
        try:
            _fetch_lodging(args, out_dir, cache_dir, explicit_months, resolve_app_id)
        except Exception as exc:  # noqa: BLE001
            log.error("宿泊旅行統計調査の取得に失敗: %s", exc)
            failures.append("宿泊旅行統計調査")

    if failures:
        log.error("失敗した取得元: %s", " / ".join(failures))
        return 1
    return 0


# ------------------------------------------------------------------- fetch: 宿泊


def _fetch_lodging(
    args: argparse.Namespace,
    out_dir: Path,
    cache_dir: Path,
    explicit_months: list[str] | None,
    resolve_app_id,
) -> None:
    """宿泊者数をAPI経路／ファイル配布経路のどちらかで取得して書き出す。

    `api` はe-Statの統計データベース（2014-01〜2016-12しか無い）、
    `files` はファイル配布のExcel（2015年〜最新月）。`auto` は
    **対象年がAPIの収録範囲に入っていればAPI、外ならファイル配布**。
    """
    source = args.lodging_source
    if source == "auto":
        in_api_range = int(estat_lodging.AVAILABLE_FROM[:4]) <= args.year <= int(
            estat_lodging.AVAILABLE_TO[:4]
        )
        source = "api" if in_api_range else "files"
        log.info("宿泊者数の取得経路: %s（--lodging-source auto の判定）", source)

    if source == "api":
        if args.lodging_area == "city":
            raise RuntimeError(
                "市区町村別はAPI経路では取得できません（--lodging-source files を使ってください）"
            )
        _fetch_lodging_api(args, out_dir, explicit_months, resolve_app_id)
        return
    _fetch_lodging_files(args, out_dir, cache_dir, explicit_months)


def _fetch_lodging_api(
    args: argparse.Namespace,
    out_dir: Path,
    explicit_months: list[str] | None,
    resolve_app_id,
) -> None:
    app_id = resolve_app_id("宿泊者数")
    if not app_id:
        raise RuntimeError("e-Stat アプリケーションIDが未設定です")
    everything = estat_lodging.fetch_lodging(app_id, pref_name=args.pref)
    by_month = {r[0]: r for r in everything}
    lodging_year: int | None = None
    if explicit_months:
        missing = sorted(set(explicit_months) - by_month.keys())
        if missing:
            log.warning(
                "宿泊旅行統計調査（API取得範囲 %s〜%s）に未収録の年月: %s",
                estat_lodging.AVAILABLE_FROM,
                estat_lodging.AVAILABLE_TO,
                ", ".join(missing),
            )
        months = [m for m in explicit_months if m in by_month]
    else:
        months, lodging_year = select_year_months(sorted(by_month), args.year)
    if not months:
        raise RuntimeError("対象年月に該当するデータがありません")
    rows = [by_month[m] for m in months]
    log.info("宿泊者数の対象期間: %s〜%s", months[0], months[-1])
    path = writers.write("lodging", rows, out_dir)
    writers.sidecar_note(
        path,
        [
            "# resas-lodging.csv の出典",
            "",
            f"- 対象: {args.pref}（延べ宿泊者数 / うち外国人）",
            f"- 対象期間: {rows[0][0]}〜{rows[-1][0]}（年月の昇順）",
            f"- 出典: 観光庁 宿泊旅行統計調査 参考第３表（e-Stat API） {estat_lodging.SOURCE_URL}",
            f"- 集計対象: 従業者数10人以上の施設（参考第３表の集計範囲）",
            f"- API取得範囲: {estat_lodging.AVAILABLE_FROM}〜{estat_lodging.AVAILABLE_TO}"
            "（このstatsDataIdはこの期間のみ「統計データベース」として登録されている）",
            "",
            "## 注意",
            "宿泊旅行統計調査の公表単位は都道府県であり、",
            f"この経路では{areas.TARGET_AREA_LABEL}単独の月次宿泊者数は取得できない。",
            "",
            "2017年以降の値はe-Stat APIから取得できない（ファイル配布のみ）。",
            "`--lodging-source files` を使うとファイル配布のExcelから自動取得できる",
            f"（市区町村別は `--lodging-area city`。配布元: {estat_lodging.FILES_URL}）。",
        ]
        + (
            [
                "",
                f"※ {args.year}年は未公表のため、公表最新年 {lodging_year}年を出力している。",
            ]
            if lodging_year is not None and lodging_year != args.year
            else []
        ),
    )


def _fetch_lodging_files(
    args: argparse.Namespace,
    out_dir: Path,
    cache_dir: Path,
    explicit_months: list[str] | None,
) -> None:
    city = areas.TARGET_CITY_NAME if args.lodging_area == "city" else None
    years = [args.year]
    if explicit_months:
        years = sorted({int(m[:4]) for m in explicit_months})

    rows: list[tuple[str, int, int]] = []
    # 複数年をまたぐ場合、出典は年ごとに別々に返るので1つにまとめる
    merged = estat_lodging_files.Provenance()
    got_any = False
    fallback_year: int | None = None
    for year in years:
        try:
            got, prov = estat_lodging_files.fetch_lodging(
                year,
                pref_name=args.pref,
                city_name=city,
                table=args.lodging_table,
                cache_dir=cache_dir,
                use_browser=args.use_browser,
            )
        except estat_lodging_files.LodgingFileError as exc:
            if len(years) > 1:
                log.warning("%d年は取得できませんでした: %s", year, exc)
                continue
            # 単年指定で空振りしたときだけ、公表済みの最新年へ切り替える
            available = estat_lodging_files.available_years(
                city=bool(city), use_browser=args.use_browser
            )
            newer = [y for y in available if y < year]
            if not newer:
                raise
            fallback_year = newer[0]
            # 「未公表」とは限らない（表の構成が違って読めない場合もある）ので理由を添える
            log.warning(
                "%d年を取得できなかったため、%d年に切り替えます（理由: %s）",
                year, fallback_year, exc,
            )
            got, prov = estat_lodging_files.fetch_lodging(
                fallback_year,
                pref_name=args.pref,
                city_name=city,
                table=args.lodging_table,
                cache_dir=cache_dir,
                use_browser=args.use_browser,
            )
        rows.extend(got)
        got_any = True
        _merge_provenance(merged, prov)

    if explicit_months:
        wanted = set(explicit_months)
        missing = sorted(wanted - {r[0] for r in rows})
        if missing:
            log.warning("ファイル配布に未収録の年月: %s", ", ".join(missing))
        rows = [r for r in rows if r[0] in wanted]
    if not rows or not got_any:
        raise RuntimeError("対象年月に該当するデータがありません")
    rows.sort()
    log.info("宿泊者数の対象期間: %s〜%s", rows[0][0], rows[-1][0])

    path = writers.write("lodging", rows, out_dir)
    writers.sidecar_note(path, _lodging_files_note(args, rows, merged, fallback_year))


def _merge_provenance(
    into: "estat_lodging_files.Provenance", other: "estat_lodging_files.Provenance"
) -> None:
    """年ごとの出典を1つに畳む。提供統計が年で変わることがあるので併記する。"""
    into.table_label = other.table_label
    into.area_label = other.area_label
    for field_name in ("dataset", "cycle"):
        current = getattr(into, field_name)
        value = getattr(other, field_name)
        if value and value not in current.split(" / "):
            setattr(into, field_name, f"{current} / {value}" if current else value)
    into.files.extend(f for f in other.files if f not in into.files)
    into.sheets.extend(s for s in other.sheets if s not in into.sheets)


def _missing_months(rows: list[tuple[str, int, int]]) -> list[str]:
    """出力の最初と最後の間で抜けている年月を返す。"""
    got = {r[0] for r in rows}
    start, end = rows[0][0], rows[-1][0]
    out: list[str] = []
    y, m = int(start[:4]), int(start[5:7])
    while f"{y:04d}-{m:02d}" <= end:
        ym = f"{y:04d}-{m:02d}"
        if ym not in got:
            out.append(ym)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _lodging_files_note(
    args: argparse.Namespace,
    rows: list[tuple[str, int, int]],
    prov: "estat_lodging_files.Provenance",
    fallback_year: int | None,
) -> list[str]:
    is_city = args.lodging_area == "city"
    gaps = _missing_months(rows)
    lines = [
        "# resas-lodging.csv の出典",
        "",
        f"- 対象: {prov.area_label}（延べ宿泊者数 / うち外国人延べ宿泊者数）",
        f"- 対象期間: {rows[0][0]}〜{rows[-1][0]}（年月の昇順）",
    ]
    if gaps:
        lines.append(f"- 欠測（この期間で値が得られなかった月）: {'・'.join(gaps)}")
    lines += [
        f"- 出典: 観光庁 宿泊旅行統計調査 {prov.dataset}（e-Stat ファイル配布）",
        f"  {estat_files.FILES_PAGE}?toukei={estat_files.TOUKEI_LODGING}",
        f"- 提供周期: {prov.cycle}",
        f"- 集計対象: {prov.table_label}",
        f"- 使用した統計表: {'・'.join(prov.sheets)}",
        f"- 変換元ファイル: {'・'.join(sorted(set(prov.files)))}",
        "",
        "## この経路について",
        "e-Stat API（getStatsData）で取れる宿泊旅行統計調査は",
        f"{estat_lodging.AVAILABLE_FROM}〜{estat_lodging.AVAILABLE_TO}分のみで、",
        "2017年以降は「統計データベース」に登録されておらずファイル配布しかない。",
        "そのため配布Excelを直接ダウンロードして変換している",
        "（`--lodging-source files`）。ブラウザ操作は不要で、",
        "e-Stat のファイル検索が内部で使っているJSON経路をそのまま叩いている。",
    ]
    if is_city:
        lines += [
            "",
            "## 市区町村別であること（重要）",
            "値は**速報値の参考表（施設所在地＝主な市区町村）**から取っている。",
            "宿泊旅行統計調査の市区町村別集計はこの参考表にしか無く、",
            "**年確定値には市区町村別の表が存在しない**。",
            "したがってこの数値が年確定値に置き換わることはない",
            "（第1次速報値→第2次速報値の改訂は入る）。",
            "",
            "掲載対象は「主な市区町村」に限られ、全市区町村は網羅されていない。",
            "掲載される市区町村は月によって変わるため、月が飛ぶことがある",
            "（上の「欠測」を参照）。都道府県計と市区町村の合計も一致しない。",
        ]
    else:
        lines += [
            "",
            "## 系列の注意",
            "宿泊旅行統計調査の公表単位は都道府県であり、この表は都道府県の値である。",
            f"{areas.TARGET_AREA_LABEL}単独の月次宿泊者数が必要な場合は `--lodging-area city`。",
            "",
            "`--lodging-table all`（既定・全施設）と `--lodging-table over10`",
            "（従業者数10人以上の施設＝API経路と同じ系列）では値が異なる。",
            "例: 2016-01 山形県は all=393,130 / over10=301,410。",
        ]
    if "速報" in prov.dataset and not is_city:
        lines += [
            "",
            "## 速報値であること",
            f"出典は{prov.dataset}であり、のちに年確定値へ置き換わる。",
            "確定後に再実行すると値が変わる。",
        ]
    if fallback_year is not None:
        lines += [
            "",
            f"※ {args.year}年は取得できなかったため、{fallback_year}年を出力している"
            "（未公表、または指定した集計対象の表がその年に無い）。",
        ]
    return lines


# ------------------------------------------------------------------ fromto: 宿泊


def _fetch_fromto_lodging(
    args: argparse.Namespace, out_dir: Path, cache_dir: Path
) -> None:
    """宿泊者の居住地別内訳を流入元として書き出す（都道府県単位・月次）。"""
    result, prov = estat_lodging_files.fetch_fromto(
        pref_name=args.pref,
        month=args.fromto_month,
        cache_dir=cache_dir,
        use_browser=args.use_browser,
    )
    shown = _top_with_other(result.rows, args.fromto_top)
    path = writers.write("fromto", shown, out_dir)
    log.info(
        "流入元: %d件（%s への延べ宿泊者数 %d人泊・%s・%s）",
        len(shown), prov.area_label, result.total, prov.year_month, result.scope,
    )
    unknown_share = result.unknown / result.total * 100 if result.total else 0.0
    writers.sidecar_note(
        path,
        [
            "# resas-fromto.csv の出典",
            "",
            f"- 対象地域: {prov.area_label}（に宿泊した人の居住地）",
            f"- 対象年月: {prov.year_month}"
            "（列仕様に年月が無いため、単月のスナップショット）",
            f"- 出典: 観光庁 宿泊旅行統計調査 {prov.dataset}（e-Stat ファイル配布）",
            f"  {estat_files.FILES_PAGE}?toukei={estat_files.TOUKEI_LODGING}",
            f"- 集計対象: {prov.table_label}",
            f"- 使用した統計表: {'・'.join(prov.sheets)}",
            f"- 変換元ファイル: {'・'.join(prov.files)}",
            f"- 合計: {result.total:,}人泊（表の「総数」と一致）",
            f"- うち居住地不詳: {result.unknown:,}人泊（{unknown_share:.1f}%）",
            "- 並び: 人泊の降順"
            + (f"、上位{args.fromto_top}件＋その他" if args.fromto_top else "、全件"),
            "",
            "## この数値が何であるか（重要）",
            "値は**延べ宿泊者数（人泊）**であり、通勤・通学者数でも滞在人口でもない。",
            "列名は仕様に合わせて `滞在人口` としている。",
            "",
            "### 4つの制約",
            "",
            f"1. **地域が{areas.TARGET_AREA_LABEL}ではなく{prov.area_label}**。",
            "   この表の施設所在地は47都道府県単位で、市区町村別の居住地内訳は存在しない。",
            "",
            f"2. **大規模施設のみ**（この月は{result.scope}）。"
            f"同月の{prov.area_label}の全施設 延べ宿泊者数に対し、",
            f"   この表の総数は {result.total:,}人泊にとどまる（実測で数%）。",
            "   大型施設に偏った標本であり、県全体の流入元構成とは一致しない。",
            "   **対象施設の範囲は月によって変わる**（2026年は2〜4月が客室数20室以上、",
            "   5〜6月が200室以上）。月をまたいだ比較はそのままではできない。",
            "",
        ]
        + (
            [
                f"3. **居住地不詳が {unknown_share:.1f}%**。表の総数と47都道府県＋国外の",
                "   内訳の合計が一致しないため、差分を「不詳」として1行に立てている。",
                "   構成比を出すときは不詳の扱いを決めること。",
            ]
            if result.unknown
            else [
                "3. **この月は居住地不詳なし**（総数と内訳の合計が一致）。",
                "   ただし月によっては総数の最大30%が都道府県に割り当てられておらず、",
                "   その場合は差分を「不詳」として1行に立てる。月次で比較するなら注意。",
            ]
        )
        + [
            "",
            "4. **速報値**。第2次速報値であり、のちに改訂されうる。",
            "   ただし年確定値には居住地47区分の表が無いため、確報での置き換えは行われない。",
            "",
            "## 他の経路",
            "自治体名単位が必要なら `--fromto-source census`（既定）。",
            "ただしそちらは2020年国勢調査の通勤・通学者数で、月次でも観光でもない。",
            "同市区町村/同都道府県/同地方/それ以外の4区分でよければ `--fromto-source jinryu`",
            "（2019-01〜2021-12）。",
        ],
    )


def _top_with_other(rows: list[tuple[str, int]], top: int) -> list[tuple[str, int]]:
    """上位 top 件＋残りを「その他」に集約する。top=0 なら全件。

    「その他」を含めた合計は元の合計と一致する（検証で効くので崩さないこと）。
    """
    if not top or len(rows) <= top:
        return list(rows)
    head = list(rows[:top])
    rest = sum(v for _, v in rows[top:])
    if rest:
        head.append(("その他", rest))
    return head


# -------------------------------------------------------------------------- manual


def cmd_manual(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    out_dir = Path(args.out)
    kinds = args.kind or ["stay", "fromto", "lodging"]
    rc = 0
    for kind in kinds:
        try:
            path = Path(args.file) if args.file else manual_import.find_input(input_dir, kind)
            rows = manual_import.IMPORTERS[kind](path)
            if not rows:
                raise RuntimeError("データ行を読み取れませんでした")
            written = writers.write(kind, rows, out_dir)
            writers.sidecar_note(
                written,
                [
                    f"# {written.name} の出典",
                    "",
                    f"- 変換元: {path.name}（RESAS等からの手動CSV出力）",
                    "- 変換: python -m resas_automate manual",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.error("%s の変換に失敗: %s", kind, exc)
            rc = 1
    return rc


# ---------------------------------------------------------------------- estat-meta


def cmd_estat_meta(args: argparse.Namespace) -> int:
    app_id = args.app_id or os.environ.get("ESTAT_APP_ID", "")
    if not app_id:
        log.error("ESTAT_APP_ID が未設定です。")
        return 1
    print(estat_lodging.dump_meta(app_id))
    return 0


# --------------------------------------------------------------------- estat-files


def cmd_estat_files(args: argparse.Namespace) -> int:
    """ファイル配布側に何年分あるかを見る。`--lodging-source files` の下調べ用。"""
    print(estat_files.describe(use_browser=args.use_browser))
    return 0


# -------------------------------------------------------------------------- sample


SAMPLE = {
    "stay": [("2025-07", "米沢市", 58500), ("2025-08", "米沢市", 61000)],
    "fromto": [("山形市", 9800), ("南陽市", 4200), ("福島市", 3100)],
    "lodging": [("2025-07", 47000, 1500), ("2025-08", 52000, 1800)],
}


def cmd_sample(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    kinds = args.kind or list(SAMPLE)
    for kind in kinds:
        rows = SAMPLE[kind]
        path = writers.write(kind, rows, out_dir)
        writers.sidecar_note(
            path,
            [
                f"# {path.name}",
                "",
                "**この内容はサンプル（ダミー）です。実データではありません。**",
                "`python -m resas_automate fetch` または `manual` で実データに差し替えてください。",
            ],
        )
    log.warning("サンプル（ダミー）値を書き出しました。実データに差し替えてください。")
    return 0


# ----------------------------------------------------------------------------- 本体


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resas_automate",
        description="米沢市／置賜の滞在人口・From-to・宿泊者数CSVを生成する",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="詳細ログ")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--out",
            default=str(DEFAULT_OUT),
            help="CSV出力先ディレクトリ（既定 data/raw/resas。同名の .md もここに出る）",
        )
        sp.add_argument(
            "--kind",
            action="append",
            choices=["stay", "fromto", "lodging"],
            help="出力する種別（複数指定可・既定は全部）",
        )

    f = sub.add_parser("fetch", help="オープンデータから自動取得する")
    common(f)
    f.add_argument("--cache", default=str(DEFAULT_CACHE), help="ダウンロードキャッシュ")
    f.add_argument(
        "--areas",
        default="yonezawa",
        choices=list(areas.AREA_PRESETS),
        help="対象地域。既定は山形県米沢市に固定（okitama は置賜8市町の参考用）",
    )
    f.add_argument(
        "--year",
        type=int,
        default=datetime.date.today().year,
        help="対象年（既定は今年。その年が未収録なら収録最新年に自動で切り替える）",
    )
    f.add_argument(
        "--months",
        help="年指定より優先。例 2021-01:2021-12 または 2021-07,2021-08",
    )
    f.add_argument(
        "--fromto-source",
        default="census",
        choices=["census", "jinryu", "lodging"],
        help="流入元の取得元。census=国勢調査（自治体名単位・2020年・既定）／"
        "jinryu=人流オープンデータ（発地4区分・2019-2021）／"
        "lodging=宿泊旅行統計の居住地別（都道府県単位・月次・最新月まで／"
        "ただし対象は山形県かつ客室数200室以上の施設のみ）",
    )
    f.add_argument(
        "--fromto-top",
        type=int,
        default=10,
        help="census時、上位何件まで出すか（残りは「その他」に集約。0で全件）",
    )
    f.add_argument(
        "--fromto-month",
        help="jinryu / lodging 時のFrom-to対象年月（例 2026-06。既定は取得できた最新月）",
    )
    f.add_argument("--dayflag", default="0", choices=["0", "1", "2"], help="0=全日 1=平日 2=休日")
    f.add_argument("--timezone", default="0", choices=["0", "1", "2"], help="0=終日 1=昼 2=夜")
    f.add_argument("--pref", default=areas.PREF_NAME, help="宿泊統計の対象都道府県")
    f.add_argument(
        "--lodging-source",
        default="auto",
        choices=["auto", "api", "files"],
        help="宿泊者数の取得経路。api=e-Stat統計データベース（2014-2016のみ・要APIキー）／"
        "files=ファイル配布のExcel（2015年〜最新月・鍵不要）／"
        "auto=対象年がAPIの収録範囲なら api、外なら files（既定）",
    )
    f.add_argument(
        "--lodging-area",
        default="pref",
        choices=["pref", "city"],
        help=f"宿泊者数の地域粒度。pref={areas.PREF_NAME}（既定）／"
        f"city={areas.TARGET_CITY_NAME}（速報値の市区町村別参考表。files経路のみ）",
    )
    f.add_argument(
        "--lodging-table",
        default="all",
        choices=list(estat_lodging_files.PREF_TABLES),
        help="files経路での集計対象。all=全施設（既定）／"
        "over10=従業者数10人以上の施設（API経路と同じ系列・2025年までの表にのみ存在）",
    )
    f.add_argument(
        "--use-browser",
        action="store_true",
        help="ファイル配布の取得をSelenium経由にする（通常不要。要 pip install -e \".[browser]\"）",
    )
    f.add_argument("--app-id", help="e-Stat アプリケーションID（既定は環境変数 ESTAT_APP_ID）")
    f.set_defaults(func=cmd_fetch)

    m = sub.add_parser("manual", help="手動DLしたCSV（input/）を所定の列仕様に変換する")
    common(m)
    m.add_argument("--input", default=str(DEFAULT_INPUT), help="入力CSVのディレクトリ")
    m.add_argument("--file", help="入力CSVを明示指定する")
    m.set_defaults(func=cmd_manual)

    e = sub.add_parser("estat-meta", help="宿泊旅行統計調査の分類コードを表示する（API経路）")
    e.add_argument("--app-id")
    e.set_defaults(func=cmd_estat_meta)

    ef = sub.add_parser(
        "estat-files", help="宿泊旅行統計調査のファイル配布の収録年月を一覧する（鍵不要）"
    )
    ef.add_argument(
        "--use-browser", action="store_true", help="Selenium経由で取得する（通常不要）"
    )
    ef.set_defaults(func=cmd_estat_files)

    s = sub.add_parser("sample", help="列仕様どおりのサンプル（ダミー）CSVを生成する")
    common(s)
    s.set_defaults(func=cmd_sample)

    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
