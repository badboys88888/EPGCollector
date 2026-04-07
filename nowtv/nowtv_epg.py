#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import gzip
import os
import json
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

# ================= 配置 ================= #
EPG_URL = "https://nowplayer.now.com/tvguide/epglist"

CHANNEL_IDS = [str(i) for i in range(1, 300)]
DAYS = 2
BATCH_SIZE = 10
SLEEP = 0.3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


# ================= 日志 ================= #
def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")


# ================= 读取映射 ================= #
def load_config():
    if not os.path.exists(CONFIG_FILE):
        log("未找到 config.json，使用默认ID模式")
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ================= 时间转换 ================= #
def format_time(ts):
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S +0000")


# ================= 请求 ================= #
def fetch_epg(batch, day):
    params = []
    for cid in batch:
        params.append(("channelIdList[]", cid))
    params.append(("day", str(day)))

    try:
        r = requests.get(EPG_URL, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"请求失败: {e}")
        return []


# ================= 主程序 ================= #
def main():
    log("========== NOWTV EPG START ==========")

    config = load_config()
    all_data = {}

    for day in range(DAYS):
        log(f"DAY {day}")

        for i in range(0, len(CHANNEL_IDS), BATCH_SIZE):
            batch = CHANNEL_IDS[i:i + BATCH_SIZE]

            data = fetch_epg(batch, day)

            if not isinstance(data, list):
                log("数据异常，跳过")
                continue

            for idx, programs in enumerate(data):
                if idx >= len(batch):
                    continue

                cid = batch[idx]

                if not programs:
                    continue

                if cid not in all_data:
                    all_data[cid] = []

                all_data[cid].extend(programs)

                log(f"频道 {cid} +{len(programs)}")

            time.sleep(SLEEP)

    # ================= XML ================= #
    log("生成 XML...")

    tv = ET.Element("tv")

    for cid, programs in all_data.items():

        meta = config.get(cid, {})
        name = meta.get("name", cid)
        logo = meta.get("logo", "")

        # channel
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = name

        if logo:
            ET.SubElement(ch, "icon", {"src": logo})

        # programme
        for p in programs:
            prog = ET.SubElement(tv, "programme", {
                "channel": cid,
                "start": format_time(p["start"]),
                "stop": format_time(p["end"])
            })

            ET.SubElement(prog, "title").text = p.get("name", "")

    tree = ET.ElementTree(tv)
    tree.write(XML_FILE, encoding="utf-8", xml_declaration=True)

    log(f"XML生成完成: {XML_FILE}")

    # ================= GZ ================= #
    log("压缩 GZ...")

    with open(XML_FILE, "rb") as f_in:
        with gzip.open(GZ_FILE, "wb") as f_out:
            f_out.writelines(f_in)

    log(f"GZ生成完成: {GZ_FILE}")


if __name__ == "__main__":
    main()
