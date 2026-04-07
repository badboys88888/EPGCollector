#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import requests
import xml.etree.ElementTree as ET
import gzip
from datetime import datetime

# ================= 路径修复（关键）================= #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")

# ================= 日志 ================= #
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================= 读取配置 ================= #
def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"config.json not found: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# ================= 时间格式 ================= #
def fmt(ts):
    # NOWTV 是毫秒时间戳
    return datetime.utcfromtimestamp(ts / 1000).strftime("%Y%m%d%H%M%S +0000")

# ================= 抓EPG ================= #
def fetch_epg():
    url = "https://nowplayer.now.com/tvguide/epglist?day=1"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        "Referer": "https://nowplayer.now.com/"
    }

    log("Fetching EPG...")

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    return r.json()

# ================= 解析EPG ================= #
def parse_epg(data):
    epg = {}

    # 结构通常是 list[list[]]
    for group in data:
        if not isinstance(group, list):
            continue

        for p in group:
            try:
                cid = str(p.get("channelId", ""))
                if not cid:
                    continue

                epg.setdefault(cid, []).append(p)

            except Exception:
                continue

    return epg

# ================= XML构建 ================= #
def build_xml(config, epg):
    tv = ET.Element("tv")

    for cid, info in config.items():

        # ---------- channel ----------
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = info.get("name", cid)

        if info.get("logo"):
            ET.SubElement(ch, "icon", {"src": info["logo"]})

        # ---------- programme ----------
        for p in epg.get(cid, []):

            try:
                start = fmt(p["start"])
                stop = fmt(p["end"])
                title = p.get("name", "").strip()

                prog = ET.SubElement(tv, "programme", {
                    "channel": cid,
                    "start": start,
                    "stop": stop
                })

                ET.SubElement(prog, "title").text = title

            except Exception:
                continue

    return tv

# ================= 写文件 ================= #
def save_files(xml_root):
    xml_bytes = ET.tostring(xml_root, encoding="utf-8")

    # XML
    with open(XML_FILE, "wb") as f:
        f.write(xml_bytes)

    # GZ
    with open(XML_FILE, "rb") as f:
        with gzip.open(GZ_FILE, "wb") as gz:
            gz.writelines(f)

# ================= 主函数 ================= #
def main():
    log("========== NOWTV START ==========")

    # 1. config
    config = load_config()
    log(f"Channels in config: {len(config)}")

    # 2. epg
    data = fetch_epg()
    epg = parse_epg(data)
    log(f"Channels in epg: {len(epg)}")

    # 3. build xml
    xml_root = build_xml(config, epg)

    # 4. save
    save_files(xml_root)

    log("XML + GZ DONE")
    log("========== FINISH ==========")


if __name__ == "__main__":
    main()
