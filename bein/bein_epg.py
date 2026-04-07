#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import requests
from bs4 import BeautifulSoup
from collections import OrderedDict

BASE_URL = "https://www.bein.com/en/tv-guide/?c=us&"
AJAX_URL = "https://www.bein.com/en/epg-ajax-template/"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest"
}


# ================================
# ① 抓取 TV GUIDE HTML
# ================================
def fetch_html():
    print("[INFO] Fetch TV Guide HTML...")
    r = requests.get(BASE_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


# ================================
# ② 提取 JS 里的频道 / postid
# ================================
def extract_channel_bootstrap(html):
    print("[INFO] Extracting JS bootstrap data...")

    # 常见：postid
    postids = re.findall(r'postid[\"\\']?\s*[:=]\s*[\"\\']?(\d+)', html)

    # 去重保序
    postids = list(OrderedDict.fromkeys(postids))

    print(f"[INFO] Found postids: {len(postids)}")

    # 尝试抓 JSON channels（如果存在）
    json_channels = None
    m = re.search(r'channels\s*:\s*(\[[\s\S]*?\])', html)
    if m:
        try:
            json_channels = json.loads(m.group(1))
            print(f"[INFO] Found JS channels: {len(json_channels)}")
        except:
            json_channels = None

    return postids, json_channels


# ================================
# ③ 请求 epg_fetch
# ================================
def fetch_epg(postid):
    params = {
        "action": "epg_fetch",
        "offset": "0",
        "category": "sports",
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": "",
        "language": "EN",
        "postid": postid,
        "loadindex": "0"
    }

    try:
        r = requests.get(AJAX_URL, params=params, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print("[ERR]", e)

    return ""


# ================================
# ④ 从 epg_html 提取频道名
# ================================
def extract_channel_name(epg_html):
    soup = BeautifulSoup(epg_html, "html.parser")

    # 常见标题结构
    title = soup.find("div", class_=re.compile("channel|logo|name", re.I))
    if title:
        return title.get_text(strip=True)

    # fallback
    text = soup.get_text(" ", strip=True)

    m = re.search(r'beIN\s*SPORTS\s*\d+', text, re.I)
    if m:
        return m.group(0).upper()

    return None


# ================================
# ⑤ 提取节目
# ================================
def extract_programs(epg_html):
    soup = BeautifulSoup(epg_html, "html.parser")
    programs = []

    items = soup.find_all("div")
    for i in items:
        txt = i.get_text(" ", strip=True)
        if len(txt) > 10 and ":" in txt:
            programs.append(txt)

    return programs[:50]


# ================================
# ⑥ 主流程
# ================================
def main():
    html = fetch_html()

    postids, js_channels = extract_channel_bootstrap(html)

    results = OrderedDict()

    print("[INFO] Fetching EPG per postid...\n")

    for idx, pid in enumerate(postids):
        print(f"[INFO] ({idx+1}/{len(postids)}) postid={pid}")

        epg_html = fetch_epg(pid)
        if not epg_html:
            continue

        channel_name = extract_channel_name(epg_html)
        programs = extract_programs(epg_html)

        if not channel_name:
            channel_name = f"UNKNOWN_{pid}"

        # 防止重复
        if channel_name in results:
            continue

        results[channel_name] = {
            "postid": pid,
            "programs": programs
        }

    # ================================
    # 输出结果
    # ================================
    print("\n========== FINAL CHANNEL LIST ==========")
    print(f"Total Channels: {len(results)}\n")

    for name, data in results.items():
        print(f"[{name}] (postid={data['postid']})")
        for p in data["programs"][:5]:
            print("   -", p)
        print()


if __name__ == "__main__":
    main()
