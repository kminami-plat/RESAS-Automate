"""HTTP取得とキャッシュ（標準ライブラリのみ）。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = (
    "RESAS-Automate/0.1 (+https://resas.go.jp/; contact: local use) "
    "python-urllib"
)
_MIN_INTERVAL = 1.0  # 政府系サイトへの連続アクセスを抑える
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def fetch(url: str, *, retries: int = 3, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            log.warning("取得失敗 (%d/%d) %s: %s", attempt, retries, url, exc)
            time.sleep(2 * attempt)
    raise RuntimeError(f"取得できませんでした: {url}") from last


def fetch_cached(url: str, cache_dir: Path, *, suffix: str = "") -> Path:
    """URLをダウンロードしてキャッシュし、ローカルパスを返す。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    name = Path(urllib.parse.urlparse(url).path).name or "download"
    path = cache_dir / f"{key}_{name}{suffix}"
    if path.exists() and path.stat().st_size > 0:
        log.info("キャッシュを利用: %s", path.name)
        return path
    log.info("ダウンロード: %s", url)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(fetch(url))
    tmp.replace(path)
    return path


def fetch_json(url: str, *, data: bytes | None = None) -> dict:
    if data is None:
        return json.loads(fetch(url))
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    _throttle()
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())
