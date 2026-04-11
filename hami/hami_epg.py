#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import requests
import gzip
import os
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom

# =========================
# 配置
# =========================
EPG_URL = "https://hamivideo.hinet.net/hamivideo/channel/epg.do"
CONFIG_FILE = "config.json"
DAYS = 3  # 建议3天更稳定

OUTPUT_XML = "hami_epg.xml"


# =========================
# 读取 config
# =========================
def load_channels():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("channels", [])


# =========================
# 请求EPG
# =========================
def fetch_epg(contentPk, date):

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://hamivideo.hinet.net/hamivideo/channel/{contentPk}.do"
    }

    data = {
        "contentPk": contentPk,
        "date": date
    }

    r = requests.post(EPG_URL, data=data, headers=headers, timeout=20)

    print("\n==============================")
    print(f"📺 {contentPk}")
    print(f"📅 {date}")
    print(f"STATUS: {r.status_code}")
    print(f"TYPE: {r.headers.get('content-type')}")
    print(f"BODY: {r.text[:120]}")

    try:
        return r.json()
    except:
        print("❌ 非JSON（可能被拦截或无数据）")
        return []


# =========================
# 解析EPG
# =========================
def parse_epg(data, channel_name):

    if not isinstance(data, list):
        return []

    result = []

    for p in data:
        if not isinstance(p, dict):
            continue

        result.append({
            "channel": channel_name,
            "title": p.get("programName", ""),
            "start": int(p.get("startTime", 0)),
            "end": int(p.get("endTime", 0))
        })

    return result


# =========================
# 时间格式
# =========================
def fmt(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S +0800")
    except:
        return datetime.now().strftime("%Y%m%d%H%M%S +0800")


# =========================
# 写XML（安全版）
# =========================
def write_xml(channels, programs):

    tv = ET.Element("tv")

    # ===== channels =====
    for ch in channels:
        cid = ch["id"]
        node = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(node, "display-name").text = ch.get("name", cid)

    # ===== programs =====
    for p in programs:

        pr = ET.SubElement(tv, "programme", {
            "channel": p["channel"],
            "start": fmt(p["start"]),
            "stop": fmt(p["end"])
        })

        ET.SubElement(pr, "title", lang="zh").text = p["title"]

    # ===== pretty XML =====
    raw = ET.tostring(tv, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")

    with open(OUTPUT_XML, "wb") as f:
        f.write(pretty)

    print(f"\n✅ XML生成完成: {OUTPUT_XML}")


# =========================
# 主流程
# =========================
def build_epg():

    print("\n🚀 Hami EPG START")

    channels = load_channels()

    print(f"📡 频道数量: {len(channels)}")

    if not channels:
        print("❌ config.json 为空")
        return

    today = datetime.now()

    all_programs = []

    for ch in channels:

        cid = ch.get("id")
        name = ch.get("name")

        if not cid:
            continue

        print("\n--------------------------------")
        print(f"📺 {name} ({cid})")

        for d in range(DAYS):

            date_str = (today + timedelta(days=d)).strftime("%Y-%m-%d")

            data = fetch_epg(cid, date_str)

            parsed = parse_epg(data, name)

            print(f"✔ 节目数: {len(parsed)}")

            all_programs.extend(parsed)

    # ===== 防空 =====
    if not all_programs:
        print("\n❌ EPG为空（API限制 / 参数错误 / IP被挡）")
        return

    write_xml(channels, all_programs)

    # ===== gzip =====
    with open(OUTPUT_XML, "rb") as f_in:
        with gzip.open(OUTPUT_XML + ".gz", "wb") as f_out:
            f_out.write(f_in.read())

    print(f"✅ GZ生成完成: {OUTPUT_XML}.gz")
    print("\n🎉 DONE")


if __name__ == "__main__":
    build_epg()
