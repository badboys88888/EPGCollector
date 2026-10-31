#!/usr/bin/env python3
"""
节目单 -> XMLTV (可导入 Emby 的 Live TV 节目指南)。

数据来自直播页内嵌的 data-schedules(JSON), 结构见 inspect_schedules.py 的分析:
  schedules[频道code]["date-<时间戳>"] = [ {time_start(UTC 秒), title, ...}, ... ]
页面没有结束时间: 这里用"下一个节目的开始时间"当结束时间, 最后一个节目按默认时长补;
单个节目最长不超过 --max-hours (防止节目表有缺口时拉出一个超长节目)。
XMLTV 里的频道 id = 频道名(和 ntdtv.m3u 里的 tvg-id 一致)。

用法:
  python epg.py "https://www.ntdtv.com/b5/live-tv" --proxy http://192.168.6.2:7890 --out output/ntdtv.xml
  python epg.py output/probe/page.html --check        # 只检查, 不写文件
  python epg.py output/schedules.json --check         # 也可以直接读 inspect_schedules.py 存的 JSON
"""
import argparse
import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from live_iptv import parse_channel_items
from xmltv import write_xmltv


def load(source, proxy):
    """返回 (schedules dict, 页面 html 或 None)。"""
    if not source.startswith("http") and source.lower().endswith(".json"):
        return json.loads(Path(source).read_text(encoding="utf-8")), None
    if source.startswith("http"):
        from fetcher import Fetcher
        html = Fetcher(proxy).get_html(source)
    else:
        html = Path(source).read_text(encoding="utf-8")
    el = BeautifulSoup(html, "lxml").select_one("[data-schedules]")
    if el is None:
        raise SystemExit("页面里没找到 data-schedules, 节目单可能换了位置。")
    return json.loads(el["data-schedules"]), html


def channel_map(html, json_path):
    """code -> {name, timezone}。优先用 live_iptv.py 生成的 ntdtv.json, 没有就从页面里解析。"""
    items = []
    if json_path and Path(json_path).exists():
        items = json.loads(Path(json_path).read_text(encoding="utf-8"))
    elif html:
        items = parse_channel_items(html)
    return {i["code"]: {"name": i["name"], "timezone": i.get("timezone", "")} for i in items if i.get("code")}


def flatten(channel_data):
    """把一个频道所有日期的节目拍平, 按开始时间排序并去重(同一时间只留第一条)。"""
    lists = channel_data if isinstance(channel_data, list) else list(channel_data.values())
    seen = {}
    for lst in lists:
        for it in lst:
            try:
                ts = int(float(it["time_start"]))
            except (KeyError, TypeError, ValueError):
                continue
            title = (it.get("title") or "").strip()
            if title:
                seen.setdefault(ts, title)
    return sorted(seen.items())


def to_programmes(cid, items, default_minutes, max_hours):
    out = []
    for i, (ts, title) in enumerate(items):
        end_ts = items[i + 1][0] if i + 1 < len(items) else ts + default_minutes * 60
        end_ts = min(end_ts, ts + int(max_hours * 3600))
        if end_ts <= ts:
            continue
        out.append({
            "channel": cid,
            "start": datetime.fromtimestamp(ts, timezone.utc),
            "stop": datetime.fromtimestamp(end_ts, timezone.utc),
            "title": title,
        })
    return out


def show_upcoming(name, tzname, items, n=8, now=None):
    """按该频道自己的时区打印"当前+接下来"的节目, 用来和网页上显示的对照。"""
    try:
        from zoneinfo import ZoneInfo
        tz, label = ZoneInfo(tzname), tzname
    except Exception:
        tz, label = timezone.utc, "UTC(镜像里没有时区数据, 需要重新构建镜像)"
    now = time.time() if now is None else now
    cur = sum(1 for ts, _ in items if ts <= now)
    start = max(cur - 1, 0)
    print(f"[{name}] 显示时区 {label}, 共 {len(items)} 条")
    for ts, title in items[start:start + n]:
        print(f"    {datetime.fromtimestamp(ts, tz):%m-%d %H:%M}  {title}")


def main():
    ap = argparse.ArgumentParser(description="直播页节目单 -> XMLTV")
    ap.add_argument("source", help="页面 URL / 已保存的 HTML / schedules.json")
    ap.add_argument("--proxy")
    ap.add_argument("--out", default="output/ntdtv.xml")
    ap.add_argument("--channels-json", default="output/ntdtv.json")
    ap.add_argument("--default-minutes", type=int, default=30, help="最后一个节目的时长")
    ap.add_argument("--max-hours", type=float, default=6, help="单个节目最长时长")
    ap.add_argument("--check", action="store_true", help="只检查并打印, 不写文件")
    ap.add_argument("--gzip", action="store_true", help="同时写出 .xml.gz 压缩版")
    ap.add_argument("--at", help="检查时模拟的时间点(UTC), 如 2026-10-30T20:08:00Z, 用来对照已保存页面当时的显示")
    ap.add_argument("--page-list", action="store_true", help="打印页面里 .time-list 实际显示的节目(对照用)")
    args = ap.parse_args()
    now = datetime.fromisoformat(args.at.replace("Z", "+00:00")).timestamp() if args.at else None

    schedules, html = load(args.source, args.proxy)
    cmap = channel_map(html, args.channels_json)

    if args.page_list:
        rows = BeautifulSoup(html or "", "lxml").select(".time-list .program")
        print(f"== 页面里 .time-list 实际显示的节目 ({len(rows)} 条, 前 10 条) ==")
        for r in rows[:10]:
            print("   ", r.get_text(" ", strip=True))
        print()

    channels, programmes = {}, []
    for code, cdata in schedules.items():
        info = cmap.get(code, {})
        name = info.get("name", code)
        items = flatten(cdata)
        if not items:
            print(f"[!] {name}: 没有节目数据")
            continue
        channels[name] = name
        programmes += to_programmes(name, items, args.default_minutes, args.max_hours)
        if args.check:
            show_upcoming(name, info.get("timezone", ""), items, now=now)

    if args.check:
        print(f"\n共 {len(channels)} 个频道, {len(programmes)} 条节目 (未写文件)")
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_xmltv(out, channels, programmes)
    print(f"已写入 {out}: {len(channels)} 个频道, {len(programmes)} 条节目")
    if args.gzip:
        gz_path = Path(str(out) + ".gz")
        with open(out, "rb") as f_in, open(gz_path, "wb") as raw:
            # filename="" 且 mtime=0: 压缩结果只取决于内容, 内容没变时字节完全一致
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
                gz.write(f_in.read())
        print(f"已写入 {gz_path} ({gz_path.stat().st_size} 字节)")


if __name__ == "__main__":
    main()
