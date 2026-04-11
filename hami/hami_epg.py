#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import requests
import gzip
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom

# =========================
# 配置
# =========================
EPG_URL = "https://hamivideo.hinet.net/hamivideo/channel/epg.do"

CONFIG_FILE = "config.json"
DAYS = 2

OUTPUT_XML = "hami_epg.xml"


# =========================
# 读取 config.json
# =========================
def load_channels():

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("channels", [])


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

    r = requests.post(EPG_URL, data=data, headers=headers, timeout=15)

    print("\n==============================")
    print(f"📡 请求频道: {contentPk}")
    print(f"📅 日期: {date}")
    print(f"STATUS: {r.status_code}")
    print(f"TYPE: {r.headers.get('content-type')}")
    print(f"HEAD: {r.text[:150]}")

    try:
        return r.json()
    except:
        print("❌ 非JSON（可能被拦截/无数据）")
        return []


# =========================
# 时间转换
# =========================
def fmt(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y%m%d%H%M%S +0800")
    except:
        return datetime.now().strftime("%Y%m%d%H%M%S +0800")


# =========================
# 解析EPG
# =========================
def parse_epg(data, channel_name):

    programs = []

    if not isinstance(data, list):
        return programs

    for p in data:

        if not isinstance(p, dict):
            continue

        programs.append({
            "channel": channel_name,
            "title": p.get("programName", ""),
            "start": p.get("startTime", 0),
            "end": p.get("endTime", 0)
        })

    return programs


# =========================
# XML美化写入
# =========================
def write_xml(tv):

    raw = ET.tostring(tv, encoding="utf-8")

    pretty = minidom.parseString(raw).toprettyxml(
        indent="  ",
        encoding="utf-8"
    )

    with open(OUTPUT_XML, "wb") as f:
        f.write(pretty)

    print("\n✅ XML 已生成:", OUTPUT_XML)


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

    tv = ET.Element("tv")

    all_programs = []

    today = datetime.now()

    # =========================
    # 遍历频道
    # =========================
    for ch in channels:

        contentPk = ch.get("id")
        name = ch.get("name")

        if not contentPk:
            continue

        print("\n--------------------------------")
        print(f"📺 频道: {name} ({contentPk})")

        for d in range(DAYS):

            date_str = (today + timedelta(days=d)).strftime("%Y-%m-%d")

            data = fetch_epg(contentPk, date_str)

            parsed = parse_epg(data, name)

            print(f"✔ 节目数: {len(parsed)}")

            all_programs.extend(parsed)

    # =========================
    # 防空检查
    # =========================
    if not all_programs:
        print("\n❌ EPG为空（接口限制/参数错误）")
        return

    # =========================
    # 写频道
    # =========================
    for ch in channels:
        ET.SubElement(tv, "channel", id=ch["name"])
        ET.SubElement(tv.find("channel[@id='%s']" % ch["name"]), "display-name").text = ch["name"]

    # =========================
    # 写节目
    # =========================
    for p in all_programs:

        pr = ET.SubElement(tv, "programme", {
            "channel": p["channel"],
            "start": fmt(p["start"]),
            "stop": fmt(p["end"])
        })

        ET.SubElement(pr, "title", lang="zh").text = p["title"]

    # =========================
    # 输出XML
    # =========================
    write_xml(tv)

    # =========================
    # gzip压缩
    # =========================
    with open(OUTPUT_XML, "rb") as f_in:
        with gzip.open(OUTPUT_XML + ".gz", "wb") as f_out:
            f_out.write(f_in.read())

    print("✅ GZ 已生成:", OUTPUT_XML + ".gz")

    print("\n🎉 完成")


# =========================
# RUN
# =========================
if __name__ == "__main__":
    build_epg()
