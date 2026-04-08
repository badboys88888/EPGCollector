#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import gzip
import re

# ===================== 配置 ===================== #
BASE_URL = "https://www.bein.com/en/epg-ajax-template/"

POSTID = "25356"
CATEGORY = "sports"
SERVICE = "bein.net"

DAYS = 7  # 🔥 7天EPG

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bein.com/en/",
    "X-Requested-With": "XMLHttpRequest"
}

OUT_XML = "bein.xml"
OUT_GZ = "bein.xml.gz"

# ===================== 日志 ===================== #
def log(msg):
    print(f"[INFO] {msg}")

# ===================== 请求EPG ===================== #
def fetch_epg(cdate):

    url = BASE_URL + (
        f"?action=epg_fetch"
        f"&offset=0"
        f"&category={CATEGORY}"
        f"&serviceidentity={SERVICE}"
        f"&mins=00"
        f"&cdate={cdate}"
        f"&language=EN"
        f"&postid={POSTID}"
        f"&loadindex=0"
    )

    r = requests.get(url, headers=HEADERS, timeout=20)

    log(f"{cdate} HTTP {r.status_code} size={len(r.text)}")

    return r.text

# ===================== 提取频道 ===================== #
def extract_channels(html):

    # 抓 beIN SPORTS 1 / 2 / 3 ...
    channels = re.findall(r'beIN\s*SPORTS\s*\d+', html, re.I)

    # 去重保序
    return list(dict.fromkeys(channels))

# ===================== 提取节目 ===================== #
def extract_programs(html):

    items = re.findall(r'<div[^>]*>(.*?)</div>', html, re.S)

    programs = []

    for it in items:

        text = re.sub(r'<.*?>', '', it).strip()

        if not text:
            continue

        if len(text) < 6:
            continue

        if "script" in text.lower():
            continue

        if "=" in text and ";" in text:
            continue

        programs.append(text)

    return programs

# ===================== 主程序 ===================== #
def main():

    log("===== BEIN 7-DAY EPG START =====")

    root = ET.Element("tv")

    base_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    total_programs = 0

    # =========================
    # 🔥 遍历7天
    # =========================
    for d in range(DAYS):

        day = (datetime.utcnow() + timedelta(days=d)).strftime("%Y-%m-%d")

        log(f"===== 抓取日期: {day} =====")

        html = fetch_epg(day)

        channels = extract_channels(html)

        if not channels:
            log("❌ 没解析到频道")
            continue

        programs = extract_programs(html)

        log(f"频道: {len(channels)} | 节目: {len(programs)}")

        # =========================
        # 按频道生成
        # =========================
        for ci, ch in enumerate(channels):

            channel_id = ch.replace(" ", "_")

            # 只写一次 channel（避免重复）
            if d == 0:
                cnode = ET.SubElement(root, "channel", id=channel_id)
                ET.SubElement(cnode, "display-name").text = ch

            for pi, title in enumerate(programs[:30]):

                start = base_time + timedelta(days=d, minutes=pi * 30)
                stop = start + timedelta(minutes=30)

                prog = ET.SubElement(root, "programme")
                prog.set("start", start.strftime("%Y%m%d%H%M%S") + " +0000")
                prog.set("stop", stop.strftime("%Y%m%d%H%M%S") + " +0000")
                prog.set("channel", channel_id)

                ET.SubElement(prog, "title").text = title

                total_programs += 1

    # =========================
    # 输出 XML
    # =========================
    tree = ET.ElementTree(root)
    tree.write(OUT_XML, encoding="utf-8", xml_declaration=True)

    # gzip
    with open(OUT_XML, "rb") as f:
        data = f.read()

    with gzip.open(OUT_GZ, "wb") as f:
        f.write(data)

    log("===== DONE =====")
    log(f"节目总数: {total_programs}")
    log(f"输出: {OUT_XML}")
    log(f"输出: {OUT_GZ}")

# ===================== RUN ===================== #
if __name__ == "__main__":
    main()
