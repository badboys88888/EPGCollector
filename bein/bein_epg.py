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

# ===================== 输出 ===================== #
OUT_XML = "bein.xml"
OUT_GZ = "bein.xml.gz"

# ===================== 日志 ===================== #
def log(msg):
    print(f"[INFO] {msg}")

# ===================== 请求 ===================== #
def fetch_epg():
    log("请求 beIN EPG...")

    url = BASE_URL + "?action=epg_fetch&offset=+0&category=sports&serviceidentity=bein.net&mins=00&cdate=&language=EN&postid=25356&loadindex=0"

    r = requests.get(url, headers=HEADERS, timeout=20)

    log(f"HTTP: {r.status_code}, size={len(r.text)}")

    return r.text

# ===================== 清洗 ===================== #
def clean_text(html):

    items = re.findall(r'<div[^>]*>(.*?)</div>', html, re.S)

    programs = []

    blacklist = ["var", "script", "container", "lastday", "function"]

    for it in items:

        text = re.sub(r'<.*?>', '', it).strip()

        if not text:
            continue

        if any(b in text.lower() for b in blacklist):
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

    base_html = fetch_epg()

    # =========================
    # 🔥 1. 提取所有 postid
    # =========================
    postids = list(set(re.findall(r'postid=(\d+)', base_html)))

    if not postids:
        log("❌ 没找到任何 postid")
        print(base_html[:300])
        return

    log(f"找到 postid: {postids}")

    root = ET.Element("tv")

    start_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    # =========================
    # 🔥 2. 循环抓所有频道
    # =========================
    for pid in postids:

        log(f"抓取频道 postid={pid}")

        params = {
            "action": "epg_fetch",
            "offset": "+0",
            "category": CATEGORY,
            "serviceidentity": SERVICE,
            "mins": "00",
            "cdate": "",
            "language": "EN",
            "postid": pid,
            "loadindex": "0",
            "ajax": "true"
        }

        r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)

        programs = clean_text(r.text)

        if not programs:
            continue

        channel_id = f"beIN_{pid}"

        # =========================
        # channel（只写一次！非常关键）
        # =========================
        channel = ET.SubElement(root, "channel", id=channel_id)
        ET.SubElement(channel, "display-name").text = f"beIN {pid}"

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

    # =========================
    # 🔥 3. 输出 XML
    # =========================
    tree = ET.ElementTree(root)
    tree.write(OUT_XML, encoding="utf-8", xml_declaration=True)

    # gzip
    with open(OUT_XML, "rb") as f:
        data = f.read()

    with gzip.open(OUT_GZ, "wb") as f:
        f.write(data)

    log("完成输出:")
    log(f"- {OUT_XML}")
    log(f"- {OUT_GZ}")

# ===================== RUN ===================== #
if __name__ == "__main__":
    main()
