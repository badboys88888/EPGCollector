#!/usr/bin/env python3
"""
把直播页里的各个频道解析成 .m3u 列表 (只管直播流, 节目单见 epg_dump.py)。

模式:
  静态模式(默认): 直接请求页面, 从源码里找 m3u8
  浏览器模式(--browser): 用 playwright 打开页面, 监听网络请求里出现的 m3u8;
      --channels 按频道名依次点击, --click-sel 按 CSS 选择器依次点击

示例:
  python3 live_iptv.py URL --out ntdtv.m3u --proxy http://192.168.6.2:7890
  python live_iptv.py URL --browser --out ntdtv.m3u --proxy http://192.168.6.2:7890 --channels "美東台,美西台,加東台,加西台,亞太台,中國台"
"""
import argparse
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from fetcher import Fetcher
from stream import extract_m3u8

_T2S = str.maketrans({"東": "东", "亞": "亚", "國": "国", "臺": "台"})


def name_variants(name):
    """繁体名字同时尝试简体写法。"""
    return list(dict.fromkeys([name, name.translate(_T2S)]))


def base(u):
    return u.split("?")[0]

AD_HOSTS = re.compile(
    r"(doubleclick\.net|googlesyndication\.com|googleadservices\.com|imasdk\.googleapis\.com|"
    r"2mdn\.net|pubmatic\.com|rubiconproject\.com|openx\.net|adnxs\.com|casalemedia\.com|"
    r"media\.net|emxdgt\.com|yellowblue\.io|fundingchoicesmessages\.google\.com)")


def try_play(page, play_sel):
    """点一下播放按钮(找不到就算了), 让播放器开始加载流。"""
    if not play_sel:
        return
    try:
        page.locator(play_sel).first.click(timeout=3000)
    except Exception:
        pass


def find_tab(page, name):
    """找频道标签: 只取可见的, 并优先排除顶部导航/页头里的同名链接(如"各地分台"菜单)。"""
    fallback = None
    for v in name_variants(name):
        for el in page.get_by_text(v, exact=True).all():
            try:
                if not el.is_visible():
                    continue
                in_nav = el.evaluate("e => !!e.closest('nav, header')")
            except Exception:
                continue
            if not in_nav:
                return el
            fallback = fallback or el
    return fallback



# ---------------- 静态模式 ----------------
def guess_name(soup, link, idx):
    for tag in soup.find_all(True):
        if any(link in str(v) for v in tag.attrs.values()):
            for node in (tag, tag.parent):
                if node is None:
                    continue
                name = node.get("title") or node.get("data-title") or node.get("data-name")
                if not name:
                    name = node.get_text(" ", strip=True)
                if name and len(name) < 40:
                    return name
    return f"频道{idx}"


def resolve_src(src):
    """
    复刻站点播放脚本(bottom.js)的拼接规则:
      data-src 含 "akamaized"  -> 就是完整地址(补上协议)
      其他                      -> "//" + data-src + "/playlist.m3u8"
    """
    src = src.strip()
    if re.match(r"^https?://", src):
        link = src if (".m3u8" in src or "akamaized" in src) else src.rstrip("/") + "/playlist.m3u8"
    elif "akamaized" in src:
        link = src
    else:
        link = "//" + src.strip("/") + "/playlist.m3u8"
    if link.startswith("//"):
        link = "https:" + link
    elif not re.match(r"^https?://", link):
        link = "https://" + link
    return link


def parse_channel_items(html):
    """解析形如 <li data-channel="美東台" data-src="...m3u8" data-code="..." timezone="..."> 的频道项。"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for el in soup.select("[data-channel][data-src]"):
        src = (el.get("data-src") or "").strip()
        if src:
            items.append({
                "name": el["data-channel"].strip(),
                "src": src,
                "url": resolve_src(src),
                "code": (el.get("data-code") or "").strip(),
                "timezone": (el.get("timezone") or "").strip(),
            })
    return items


def from_html(fetcher, url):
    """返回 (channels, items)。优先读 data-channel/data-src 属性, 读不到再退回从源码里找 m3u8。"""
    html = fetcher.get_html(url)
    items = parse_channel_items(html)
    if items:
        return [(i["name"], i["url"]) for i in items], items
    links = extract_m3u8(html, url)
    soup = BeautifulSoup(html, "lxml")
    return [(guess_name(soup, l, i), l) for i, l in enumerate(links, 1)], []


# ---------------- 浏览器模式 ----------------
def from_browser(url, channels=None, click_sel=None, wait=8, proxy=None, play_sel=None, block_ads=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("需要先安装: pip install playwright && playwright install chromium")

    found, result = [], []
    with sync_playwright() as p:
        launch_args = {"proxy": {"server": proxy}} if proxy else {}
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page()
        if block_ads:
            page.route(AD_HOSTS, lambda route: route.abort())
        page.on("request", lambda r: found.append(r.url) if ".m3u8" in r.url else None)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(wait * 1000)
        try_play(page, play_sel)
        page.wait_for_timeout(wait * 1000)

        # 页面默认频道已出现的流; 直播列表会不断刷新, 点击后要排除这些旧流
        seen = {base(u) for u in found}
        default_stream = found[0] if found else None

        def click_and_collect(name, click):
            before = len(found)
            try:
                click()
            except Exception as e:
                print(f"  [!] 点击失败 {name}: {e}")
                return
            try_play(page, play_sel)
            page.wait_for_timeout(wait * 1000)
            new = [u for u in found[before:] if base(u) not in seen]
            for u in found[before:]:
                seen.add(base(u))
            if new:
                result.append((name, new[0]))
            else:
                print(f"  [!] {name}: 点击后没有出现新的 m3u8 (可能该台就是当前默认台)")

        if channels:
            for ch in channels:
                loc = find_tab(page, ch)
                if loc is None:
                    print(f"  [!] 页面上找不到可见的频道标签: {ch}")
                    continue
                click_and_collect(ch, lambda loc=loc: loc.click(timeout=5000))
            if default_stream:
                print(f"  [i] 页面默认台的流: {default_stream[:80]} (若有频道点击后无新流, 多半就是它)")
        elif click_sel:
            for i, item in enumerate(page.query_selector_all(click_sel), 1):
                name = (item.inner_text() or "").strip() or f"频道{i}"
                click_and_collect(name, lambda item=item: item.click(timeout=5000))
        else:
            for i, l in enumerate(dict.fromkeys(found), 1):
                result.append((f"频道{i}", l))
        browser.close()
    return result


def write_m3u(channels, path: Path, epg_url=None):
    # 头部带上节目单地址, 很多 IPTV 播放器会自动读取 (两种常见写法都带上)
    header = f'#EXTM3U x-tvg-url="{epg_url}" url-tvg="{epg_url}"' if epg_url else "#EXTM3U"
    lines = [header]
    for name, link in channels:
        lines.append(f'#EXTINF:-1 tvg-id="{name}" tvg-name="{name}",{name}')
        lines.append(link)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="直播页 -> m3u 列表")
    ap.add_argument("url")
    ap.add_argument("--out", default="live.m3u")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--channels", help="频道名, 逗号分隔, 如 美東台,美西台")
    ap.add_argument("--click-sel", help="频道按钮的 CSS 选择器(浏览器模式)")
    ap.add_argument("--wait", type=int, default=8, help="每次等待秒数(浏览器模式)")
    ap.add_argument("--play-sel", default=".vjs-big-play-button", help="播放按钮选择器(浏览器模式)")
    ap.add_argument("--block-ads", action="store_true", help="拦截广告域名请求(浏览器模式)")
    ap.add_argument("--epg-url", help="写进 m3u 头部的节目单(XMLTV)地址")
    ap.add_argument("--proxy")
    args = ap.parse_args()

    names = [c.strip() for c in args.channels.split(",")] if args.channels else None

    items = []
    if args.browser:
        channels = from_browser(args.url, names, args.click_sel, args.wait, args.proxy, args.play_sel, args.block_ads)
    else:
        channels, items = from_html(Fetcher(args.proxy), args.url)
        if names:
            ok = lambda n: any(v in n for c in names for v in name_variants(c))
            channels = [(n, l) for n, l in channels if ok(n)]
            items = [i for i in items if ok(i["name"])]

    if not channels:
        raise SystemExit("没找到 m3u8。试试 --browser; 若直播走的是 YouTube 等嵌入播放器, 这里抓不到。")

    write_m3u(channels, Path(args.out), args.epg_url)
    if items:
        # 频道代码/时区留着, 之后做 EPG 时可能要用
        Path(args.out).with_suffix(".json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {args.out}, 共 {len(channels)} 个频道:")
    for n, l in channels:
        print(f"  {n}  {l[:80]}")


if __name__ == "__main__":
    main()
