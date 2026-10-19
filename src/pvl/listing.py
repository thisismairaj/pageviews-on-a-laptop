"""Read Wikimedia's directory listing: which hourly files exist, and how big each one is."""
import re
import urllib.request
from dataclasses import dataclass

from . import BASE_URL, USER_AGENT

_ROW = re.compile(
    r'<a href="(pageviews-(\d{8})-(\d{6})\.gz)">[^<]+</a>\s+'
    r"(\d{2}-\w{3}-\d{4} \d{2}:\d{2})\s+(\d+)"
)


@dataclass(frozen=True)
class Listed:
    name: str
    day: str  # YYYY-MM-DD
    hour: int  # 0-23
    size: int  # bytes, as published in the listing
    mtime: str  # as published, e.g. 01-Jan-2024 01:03
    url: str


def parse_listing(html: str, base_url: str) -> list[Listed]:
    out = []
    for name, ymd, hms, mtime, size in _ROW.findall(html):
        out.append(
            Listed(
                name=name,
                day=f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                hour=int(hms[:2]),
                size=int(size),
                mtime=mtime,
                url=f"{base_url}/{name}",
            )
        )
    return out


def fetch_month_listing(year: int, month: int) -> list[Listed]:
    base = f"{BASE_URL}/{year}/{year}-{month:02d}"
    req = urllib.request.Request(base + "/", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return parse_listing(html, base)
