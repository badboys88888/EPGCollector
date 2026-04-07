#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ===================== 配置 ===================== #
BASE_URL = "https://www.bein.com/en/epg-ajax-template/"

OUTPUT_XML = "bein.xml"
OUTPUT_GZ = "bein.xml.gz"

POSTID = "25356"
CATEGORY = "sports"
SERVICE = "bein.net"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bein.com/en/",
    "X-Requested-With": "XMLHttpRequest"
}

# ===================== 日志 ===================== #
def log(msg):
    print(f"[INFO] {msg}")

# ===================== 获取数据 ===================== #
def fetch_epg():
    log("请求 beIN EPG...")

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

    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)

    log(f"HTTP状态: {r.status_code}")
    log(f"内容长度: {len(r.text)}")

    return r.text

# ===================== 清洗HTML ===================== #
def clean_text(html):
    log("开始清洗HTML...")

    # 提取所有div内容
    items = re.findall(r'<div[^>]*>(.*?)</div>', html, re.S)

    programs = []

    for it in items:

        text = re.sub(r'<.*?>', '', it).strip()

        # ======= 过滤垃圾 ======= #
        if not text:
            continue

        if any(x in text.lower() for x in [
            "lastday",
            "var ",
            "container",
            "script",
            "function"
        ]):
            continue

        if len(text) < 5:
            continue

        # 过滤纯时间条
        if re.match(r'^\d{1,2}:\d{2}', text):
            continue

        # 过滤JS碎片
        if "=" in text and ";" in text:
            continue

        programs.append(text)

    log(f"清洗后节目数: {len(programs)}")

    return programs

# ===================== 构建XML ===================== #
def build_xml(programs):
    log("生成 XMLTV...")

    root = ET.Element("tv")

    channel = ET.SubElement(root, "channel", id="beIN_SPORTS")
    ET.SubElement(channel, "display-name").text = "beIN SPORTS"

    # 起始时间（从现在开始）
    start_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    for i, title in enumerate(programs):

        start = start_time + timedelta(minutes=i * 30)
        stop = start_time + timedelta(minutes=(i + 1) * 30)

        prog = ET.SubElement(root, "programme")
        prog.set("start", start.strftime("%Y%m%d%H%M%S") + " +0000")
        prog.set("stop", stop.strftime("%Y%m%d%H%M%S") + " +0000")
        prog.set("channel", "beIN_SPORTS")

        ET.SubElement(prog, "title").text = title

    return root

# ===================== 保存 ===================== #
def save(root):

    log("写入XML...")

    tree = ET.ElementTree(root)
    tree.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)

    with open(OUTPUT_XML, "rb") as f:
        data = f.read()

    with gzip.open(OUTPUT_GZ, "wb") as f:
        f.write(data)

    log("输出完成:")
    log(f"- {OUTPUT_XML}")
    log(f"- {OUTPUT_GZ}")

# ===================== 主函数 ===================== #
def main():

    log("===== BEIN EPG START =====")

    html = fetch_epg()

    # 如果返回HTML太像页面，提示
    if "<html" in html.lower():
        log("⚠️ 警告：可能仍是网页模板，但继续尝试解析")

    programs = clean_text(html)

    if not programs:
        log("❌ 没有解析到节目")
        print(html[:300])
        return

    xml = build_xml(programs)

    save(xml)

# ===================== RUN ===================== #
if __name__ == "__main__":
    main()
