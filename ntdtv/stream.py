"""负责从播放页源码里找 m3u8 地址, 并解析 auth_key 里的时间信息。

注意: auth_key 格式常见为 timestamp-rand-uid-md5。
这个 timestamp 在不同 CDN 里含义不同: 有的是"签发时间"(过期 = 签发 + 站点设定的有效期),
有的直接是"过期时间"。单看链接无法区分, 所以这里只报告时间, 过期判断需要你传入 ttl。
"""
import re
import time
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, urlparse

_M3U8_RE = re.compile(r'https?://[^\s"\'<>\\]+?\.m3u8[^\s"\'<>\\]*', re.I)


def extract_m3u8(html: str, base_url: str = "") -> list:
    """返回页面源码中出现的所有 m3u8 地址(去重保序)。"""
    text = unescape(html).replace("\\/", "/").replace("\\u0026", "&")
    return list(dict.fromkeys(_M3U8_RE.findall(text)))


def get_auth_timestamp(url: str):
    """读取 auth_key 开头的 10 位时间戳, 返回 datetime 或 None。"""
    qs = parse_qs(urlparse(url).query)
    key = (qs.get("auth_key") or [""])[0]
    first = key.split("-")[0]
    if first.isdigit() and len(first) == 10:
        return datetime.fromtimestamp(int(first))
    return None


def age_seconds(url: str):
    ts = get_auth_timestamp(url)
    return int(time.time() - ts.timestamp()) if ts else None


def is_expired(url: str, ttl: int = None) -> bool:
    """ttl: 签发后的有效秒数(需实测, 见 probe.py)。未提供 ttl 时无法判断, 返回 False。"""
    age = age_seconds(url)
    return bool(ttl and age is not None and age > ttl)


def describe_expiry(url: str, ttl: int = None) -> str:
    ts = get_auth_timestamp(url)
    if not ts:
        return "未检测到 auth_key 时间戳"
    age = age_seconds(url)
    s = f"auth_key 时间 {ts:%Y-%m-%d %H:%M:%S}, 已过 {age // 60} 分钟"
    if ttl:
        s += " (按 ttl 判断: " + ("已过期" if is_expired(url, ttl) else "仍有效") + ")"
    return s
