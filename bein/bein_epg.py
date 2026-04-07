#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime
import time

# ===================== 配置 ===================== #
BASE_URL = "https://www.bein.com/en/epg-ajax-template/"

OUTPUT_XML = "bein.xml"
OUTPUT_GZ = "bein.xml.gz"

CATEGORY = "sports"
SERVICE = "bein.net"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bein.com/en/"
}

# ===================== 日志 ===================== #
def log(msg):
    print(f"[INFO] {msg}")

# ===================== 请求EPG ===================== #
def fetch_epg(offset="+0"):
    params = {
        "action": "epg_fetch",
        "offset": offset,
        "category": CATEGORY,
        "serviceidentity": SERVICE,
        "mins": "00",
        "cdate": "",
        "language": "EN",
        "postid": "25356",   # ⚠️ 先固定（稳定跑）
        "loadindex": "0"
    }

    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text

# ===================== 转 XML（简化版） ===================== #
def build_xml(raw):
    root = ET.Element("tv")

    channel = ET.SubElement(root, "channel", id="beIN_SPORTS")
    ET.SubElement(channel, "display-name").text = "beIN SPORTS"

    prog = ET.SubElement(root, "programme")
    prog.set("start", datetime.now().strftime("%Y%m%d%H%M%S"))
    prog.set("stop", datetime.now().strftime("%Y%m%d%H%M%S"))
    prog.set("channel", "beIN_SPORTS")

    ET.SubElement(prog, "title").text = "beIN LIVE DATA"
    ET.SubElement(prog, "desc").text = raw[:200]

    return root

# ===================== 保存 ===================== #
def save_xml(root):
    tree = ET.ElementTree(root)
    tree.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)

    with open(OUTPUT_XML, "rb") as f_in:
        with gzip.open(OUTPUT_GZ, "wb") as f_out:
            f_out.write(f_in.read())

# ===================== 主程序 ===================== #
def main():
    log("===== BEIN EPG START =====")

    try:
        raw = fetch_epg()
        log("EPG 获取成功")

        xml_root = build_xml(raw)

        save_xml(xml_root)
        log("输出完成: bein.xml + bein.xml.gz")

    except Exception as e:
        log(f"错误: {e}")
        raise

if __name__ == "__main__":
    main()
