#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# ===================== 配置 ===================== #
BASE_URL = "https://www.bein.com/en/epg-ajax-template/"

OUTPUT_XML = "bein.xml"
OUTPUT_GZ = "bein.xml.gz"

CATEGORY = "sports"
SERVICE = "bein.net"
POSTID = "25356"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bein.com/en/",
    "X-Requested-With": "XMLHttpRequest"
}

# ===================== 日志 ===================== #
def log(msg):
    print(f"[INFO] {msg}")

# ===================== 请求 ===================== #
def fetch_epg():
    params = {
        "action": "epg_fetch",
        "offset": "+0",
        "category": CATEGORY,
        "serviceidentity": SERVICE,
        "mins": "00",
        "cdate": "",
        "language": "EN",
        "postid": POSTID,
        "loadindex": "0",
        "ajax": "true"
    }

    log("请求 beIN EPG API...")

    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)

    log(f"HTTP状态码: {r.status_code}")

    if r.status_code != 200:
        raise Exception("请求失败")

    return r.text

# ===================== 解析HTML（关键） ===================== #
def parse_epg(html):
    log("开始解析HTML节目数据...")

    programs = []

    # ⚠️ beIN页面结构通常包含 event / time / title
    blocks = re.findall(r'<div[^>]*class="[^"]*event[^"]*"[^>]*>(.*?)</div>', html, re.S)

    if not blocks:
        log("⚠️ 未找到 event block，尝试宽松解析...")

        blocks = re.findall(r'<div.*?>(.*?)</div>', html, re.S)

    for b in blocks:
        # 时间
        time_match = re.search(r'(\d{1,2}:\d{2})', b)
        title_match = re.search(r'>([^<>]{3,100})<', b)

        time_text = time_match.group(1) if time_match else "00:00"
        title_text = title_match.group(1).strip() if title_match else None

        if title_text:
            programs.append({
                "time": time_text,
                "title": title_text
            })

    log(f"解析到节目数量: {len(programs)}")

    return programs

# ===================== XML构建 ===================== #
def build_xml(programs):
    log("生成 XMLTV...")

    root = ET.Element("tv")

    channel = ET.SubElement(root, "channel", id="beIN_SPORTS")
    ET.SubElement(channel, "display-name").text = "beIN SPORTS"

    now = datetime.now().strftime("%Y%m%d")

    for p in programs:
        prog = ET.SubElement(root, "programme")
        prog.set("start", now + p["time"].replace(":", "") + "00 +0000")
        prog.set("stop", now + p["time"].replace(":", "") + "59 +0000")
        prog.set("channel", "beIN_SPORTS")

        ET.SubElement(prog, "title").text = p["title"]

    return root

# ===================== 保存 ===================== #
def save_xml(root):
    log("写入 XML 文件...")

    tree = ET.ElementTree(root)
    tree.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)

    with open(OUTPUT_XML, "rb") as f:
        data = f.read()

    with gzip.open(OUTPUT_GZ, "wb") as f:
        f.write(data)

    log(f"输出完成: {OUTPUT_XML} + {OUTPUT_GZ}")

# ===================== 主程序 ===================== #
def main():
    log("===== BEIN EPG START =====")

    try:
        html = fetch_epg()

        log(f"返回长度: {len(html)}")

        programs = parse_epg(html)

        if not programs:
            log("❌ 没解析到节目（说明接口仍是模板）")
            log("前200字符预览：")
            print(html[:200])
            return

        xml = build_xml(programs)

        save_xml(xml)

    except Exception as e:
        log(f"错误: {e}")
        raise

# ===================== RUN ===================== #
if __name__ == "__main__":
    main()
