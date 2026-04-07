#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ===================== 配置 ===================== #
BASE_URL = "https://www.bein.com/en/epg-ajax-template/"

CATEGORY = "sports"
SERVICE = "bein.net"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bein.com/en/",
    "X-Requested-With": "XMLHttpRequest"
}

# ⚠️ 固定 postid（关键：不要再从HTML抓）
POSTIDS = [
    "25356",
    "25357",
    "25358",
    "25359"
]

OUT_XML = "bein.xml"
OUT_GZ = "bein.xml.gz"

# ===================== 日志 ===================== #
def log(msg):
    print(f"[INFO] {msg}")

# ===================== 清洗 ===================== #
def clean_text(html):

    items = re.findall(r'<div[^>]*>(.*?)</div>', html, re.S)

    programs = []

    blacklist = [
        "script",
        "var ",
        "function",
        "container",
        "category",
        "ajax",
        "lastday"
    ]

    for it in items:

        text = re.sub(r'<.*?>', '', it).strip()

        if not text:
            continue

        low = text.lower()

        if any(b in low for b in blacklist):
            continue

        if len(text) < 6:
            continue

        if "=" in text and ";" in text:
            continue

        programs.append(text)

    return programs

# ===================== 主函数 ===================== #
def main():

    log("===== BEIN EPG START =====")

    root = ET.Element("tv")

    start_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    total = 0

    # =========================
    # 遍历所有频道
    # =========================
    for pid in POSTIDS:

        log(f"请求 postid={pid}")

        url = BASE_URL + (
            f"?action=epg_fetch"
            f"&offset=+0"
            f"&category={CATEGORY}"
            f"&serviceidentity={SERVICE}"
            f"&mins=00"
            f"&cdate="
            f"&language=EN"
            f"&postid={pid}"
            f"&loadindex=0"
        )

        r = requests.get(url, headers=HEADERS, timeout=20)

        log(f"HTTP {r.status_code} size={len(r.text)}")

        programs = clean_text(r.text)

        log(f"解析节目数: {len(programs)}")

        if not programs:
            continue

        channel_id = f"beIN_{pid}"

        # =========================
        # channel（每个频道只写一次）
        # =========================
        channel = ET.SubElement(root, "channel", id=channel_id)
        ET.SubElement(channel, "display-name").text = f"beIN SPORTS {pid}"

        # =========================
        # programme
        # =========================
        for i, title in enumerate(programs):

            start = start_time + timedelta(minutes=i * 30)
            stop = start + timedelta(minutes=30)

            prog = ET.SubElement(root, "programme")
            prog.set("start", start.strftime("%Y%m%d%H%M%S") + " +0000")
            prog.set("stop", stop.strftime("%Y%m%d%H%M%S") + " +0000")
            prog.set("channel", channel_id)

            ET.SubElement(prog, "title").text = title

            total += 1

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

    log("===== 完成 =====")
    log(f"节目总数: {total}")
    log(f"输出: {OUT_XML}")
    log(f"输出: {OUT_GZ}")

# ===================== RUN ===================== #
if __name__ == "__main__":
    main()
