#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import requests
import xml.etree.ElementTree as ET
import gzip
from datetime import datetime

# ================= 读取配置 ================= #
def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

# ================= 日志 ================= #
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================= 时间 ================= #
def fmt(ts):
    return datetime.utcfromtimestamp(ts / 1000).strftime("%Y%m%d%H%M%S +0000")

# ================= 抓EPG（直接用你接口） ================= #
def fetch_epg():
    url = "https://nowplayer.now.com/tvguide/epglist?day=1"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        "Referer": "https://nowplayer.now.com/"
    }

    log("GET NOWTV EPG")

    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()

    return r.json()

# ================= 解析EPG ================= #
def parse(data):
    epg = {}

    for group in data:
        for p in group:

            cid = str(p.get("channelId", ""))
            if not cid:
                continue

            if cid not in epg:
                epg[cid] = []

            epg[cid].append(p)

    return epg

# ================= XML生成 ================= #
def build_xml(config, epg):
    tv = ET.Element("tv")

    for cid, info in config.items():

        # channel
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = info["name"]

        if info.get("logo"):
            ET.SubElement(ch, "icon", {"src": info["logo"]})

        # programme
        for p in epg.get(cid, []):

            prog = ET.SubElement(tv, "programme", {
                "channel": cid,
                "start": fmt(p["start"]),
                "stop": fmt(p["end"])
            })

            ET.SubElement(prog, "title").text = p.get("name", "")

    return tv

# ================= 主流程 ================= #
def main():
    log("========== NOWTV RUN START ==========")

    config = load_config()
    log(f"CHANNELS: {len(config)}")

    data = fetch_epg()
    epg = parse(data)

    log(f"EPG CHANNELS: {len(epg)}")

    xml = build_xml(config, epg)

    xml_bytes = ET.tostring(xml, encoding="utf-8")

    with open("nowtv.xml", "wb") as f:
        f.write(xml_bytes)

    log("XML OK")

    with open("nowtv.xml", "rb") as f:
        with gzip.open("nowtv.xml.gz", "wb") as f2:
            f2.writelines(f)

    log("GZ OK")

    log("========== DONE ==========")


if __name__ == "__main__":
    main()
