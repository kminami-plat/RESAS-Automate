"""Selenium 経由の取得（**任意依存**・通常は使わない）。

## 位置づけ

このリポジトリの方針は「外部依存ゼロ」で、実際 e-Stat のファイル配布は
`estat_files.py` のプレーンHTTPだけで辿れる（2026-09 実測）。
したがって**既定ではこのモジュールは読み込まれない**。

用意してあるのは、配信元がJSレンダリング専用に変わって
`/retrieve/api_file` が塞がれた場合の逃げ道としてである。
`--use-browser` を付けたときだけ `estat_files` から呼ばれる。

動作確認済み（2026-09、Selenium 4.49 / Chrome ヘッドレス）:
`--use-browser` でも提供統計4件・収録期間ともHTTP経路と同じ結果になる。
ただし `page_source` は属性がエスケープ済みなので、
`estat_files._items()` 側で `html.unescape()` してから正規表現にかけている
（これを忘れると `&amp;month=` になって月コードが取れない）。

## 入れ方

    .venv/bin/python -m pip install -e ".[browser]"

Selenium 4.6 以降は Selenium Manager がドライバを自動調達するので、
chromedriver を手で置く必要はない（Google Chrome 本体は必要）。

## 注意

ヘッドレスChromeのダウンロードは既定で無効になっていることがあるため、
`prefs` と CDP の `Page.setDownloadBehavior` の両方で許可している。
政府系サイトへの負荷を避けるため、`http.py` と同じく待ちを入れる。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

PAGE_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300

_INSTALL_HINT = (
    "Selenium が入っていません。ブラウザ経由の取得を使う場合は "
    '`.venv/bin/python -m pip install -e ".[browser]"` を実行してください。'
    "（通常は不要です。e-Statのファイル配布は --use-browser なしで取得できます）"
)


class BrowserError(RuntimeError):
    pass


def available() -> bool:
    """Selenium が import できるか。"""
    try:
        import selenium  # noqa: F401
    except ImportError:
        return False
    return True


def _driver(download_dir: Path | None = None):
    try:
        from selenium import webdriver
    except ImportError as exc:  # pragma: no cover - 依存が無い環境向け
        raise BrowserError(_INSTALL_HINT) from exc

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280,2000")
    if download_dir is not None:
        download_dir.mkdir(parents=True, exist_ok=True)
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as exc:  # noqa: BLE001 - ドライバ調達の失敗は理由が多様
        raise BrowserError(
            f"ヘッドレスChromeを起動できませんでした: {exc}\n"
            "Google Chrome がインストールされているか確認してください。"
        ) from exc
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    if download_dir is not None:
        # headless では prefs だけだとダウンロードが握り潰されることがある
        try:
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir)},
            )
        except Exception:  # noqa: BLE001 - CDPが無くても prefs で動く場合がある
            log.debug("Page.setDownloadBehavior を設定できませんでした", exc_info=True)
    return driver


def render(url: str, *, wait_selector: str | None = None, timeout: int = 30) -> str:
    """URLをブラウザで開き、JS実行後のHTMLを返す。"""
    driver = _driver()
    try:
        log.info("ブラウザで取得: %s", url)
        driver.get(url)
        if wait_selector:
            _wait_for(driver, wait_selector, timeout)
        else:
            time.sleep(2)
        return driver.page_source
    finally:
        driver.quit()


def _wait_for(driver, selector: str, timeout: int) -> None:
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as ec
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, timeout).until(
            ec.presence_of_element_located((By.CSS_SELECTOR, selector))
        )
    except TimeoutException as exc:
        raise BrowserError(
            f"{timeout}秒待っても要素 '{selector}' が現れませんでした: {driver.current_url}"
        ) from exc


def download(url: str, dest_dir: Path, *, timeout: int = DOWNLOAD_TIMEOUT) -> Path:
    """URLをブラウザで開いてファイルを保存し、保存先のパスを返す。

    ダウンロードは非同期なので、``.crdownload`` が消えて実体が現れるまで待つ。
    """
    dest_dir = Path(dest_dir)
    before = {p.name for p in dest_dir.glob("*")} if dest_dir.is_dir() else set()
    driver = _driver(download_dir=dest_dir)
    try:
        log.info("ブラウザでダウンロード: %s", url)
        try:
            driver.get(url)
        except Exception:  # noqa: BLE001 - 添付ダウンロードは遷移が完了しない
            log.debug("ページ遷移は完了しませんでした（添付ダウンロードでは正常）")
        return _wait_for_download(dest_dir, before, timeout)
    finally:
        driver.quit()


def _wait_for_download(dest_dir: Path, before: set[str], timeout: int) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = [
            p
            for p in dest_dir.glob("*")
            if p.name not in before and not p.name.endswith((".crdownload", ".tmp"))
        ]
        if new and all(p.stat().st_size > 0 for p in new):
            # 書き込み途中の可能性があるので、サイズが安定するまで一拍おく
            newest = max(new, key=lambda p: p.stat().st_mtime)
            size = newest.stat().st_size
            time.sleep(1.0)
            if newest.stat().st_size == size:
                log.info("ダウンロード完了: %s", newest.name)
                return newest
        time.sleep(1.0)
    raise BrowserError(f"{timeout}秒以内にダウンロードが完了しませんでした: {dest_dir}")
