#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import gzip
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime

# ================= 配置 ================= #
URL = "https://nowplayer.now.com/tvguide/epglist"

CHANNELS = ["096", "099", "102", "105", "106", "108"]
DAYS = 2

OUT_XML = "nowtv.xml"
OUT_GZ = "nowtv.xml.gz"

# ================= 日志 ================= #
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================= 时间转换 ================= #
def fmt(ts):
    dt = datetime.utcfromtimestamp(ts / 1000)
    return dt.strftime("%Y%m%d%H%M%S +0000")

# ================= 请求 ================= #
def fetch(day):
    params = [("day", str(day))]
    for c in CHANNELS:
        params.append(("channelIdList[]", c))

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        "Referer": "https://nowplayer.now.com/"
    }

    for i in range(3):
        try:
            log(f"GET day={day} try={i+1}")
            r = requests.get(URL, params=params, headers=headers, timeout=10)

            log(f"STATUS {r.status_code}")

            if r.status_code == 500:
                time.sleep(2)
                continue

            r.raise_for_status()
            return r.json()

        except Exception as e:
            log(f"ERROR {e}")
            time.sleep(2)

    return []

# ================= XML构建 ================= #
def build_xml(data):
    tv = ET.Element("tv")

    count = 0

    for group in data:
        for p in group:

            count += 1
            cid = p.get("cid", "unknown")

            ch = ET.SubElement(tv, "channel", id=cid)
            ET.SubElement(ch, "display-name").text = cid

            prog = ET.SubElement(tv, "programme", {
                "channel": cid,
                "start": fmt(p["start"]),
                "stop": fmt(p["end"])
            })

            ET.SubElement(prog, "title").text = p.get("name", "")

    log(f"PROGRAMMES: {count}")
    return tv

# ================= 主程序 ================= #
def main():
    log("========== NOWTV START ==========")

    all_data = []

    for day in range(DAYS):
        log(f"DAY {day}")

        data = fetch(day)

        if not data:
            log("EMPTY DATA")
            continue

        all_data.extend(data)

    log("BUILD XML")

    xml_root = build_xml(all_data)

    xml_str = ET.tostring(xml_root, encoding="utf-8")

    with open(OUT_XML, "wb") as f:
        f.write(xml_str)

    log(f"XML SAVED: {OUT_XML}")

    log("COMPRESS GZ")

    with open(OUT_XML, "rb") as f_in:
        with gzip.open(OUT_GZ, "wb") as f_out:
            f_out.writelines(f_in)

    log(f"GZ SAVED: {OUT_GZ}")

    log("========== DONE ==========")


if __name__ == "__main__":
    main()
