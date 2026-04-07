#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import gzip
import os
import json
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from datetime import datetime, timezone

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


# ================= 读取配置（台标/名称） ================= #
def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            log("⚠️ 未找到 config.json（仅使用ID）")
            return {}
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"❌ config.json错误: {e}")
        return {}


# ================= 时间转换 ================= #
def format_time(ts):
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S +0000")


# ================= 中文优先标题 ================= #
def get_title(p):
    return (
        p.get("nameZh")
        or p.get("titleZh")
        or p.get("localizedTitle")
        or (p.get("title", {}).get("zh") if isinstance(p.get("title"), dict) else None)
        or p.get("name")
        or p.get("title")
        or ""
    )


# ================= 请求EPG ================= #
def fetch_epg(batch, day):
    params = []
    for cid in batch:
        params.append(("channelIdList[]", cid))
    params.append(("day", str(day)))

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"
    }

    try:
        log(f"▶ DAY {day} | 请求 {len(batch)} 个频道")
        r = requests.get(EPG_URL, params=params, headers=headers, timeout=10)

        log(f"   ├─ HTTP {r.status_code}")
        r.raise_for_status()

        return r.json()

    except Exception as e:
        log(f"❌ 请求失败: {e}")
        return []


# ================= XML美化 ================= #
def prettify_xml(elem):
    rough = ET.tostring(elem, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8")


# ================= 主程序 ================= #
def main():
    log("========== NOWTV EPG START ==========")

    config = load_config()
    all_data = {}

    # ========== 抓取 ==========
    for day in range(DAYS):
        log(f"================ DAY {day} ================")

        for i in range(0, len(CHANNEL_IDS), BATCH_SIZE):
            batch = CHANNEL_IDS[i:i + BATCH_SIZE]

            data = fetch_epg(batch, day)

            if not isinstance(data, list):
                log("⚠️ 数据异常")
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

                log(f"   ✓ 频道 {cid} | +{len(programs)}")

            time.sleep(SLEEP)

    # ========== XML生成 ==========
    log("========== XML GENERATE ==========")
    log(f"📊 频道总数: {len(all_data)}")

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

            ET.SubElement(prog, "title").text = get_title(p)

    # ========== 写XML（美化） ==========
    log("🧱 XML格式化中...")

    xml_str = prettify_xml(tv)

    with open(XML_FILE, "wb") as f:
        f.write(xml_str)

    log(f"✅ XML完成: {XML_FILE}")

    # ========== 压缩 ==========
    log("🗜️ 压缩GZ...")

    with open(XML_FILE, "rb") as f_in:
        with gzip.open(GZ_FILE, "wb") as f_out:
            f_out.writelines(f_in)

    log(f"✅ GZ完成: {GZ_FILE}")

    log("========== DONE ==========")


if __name__ == "__main__":
    main()
