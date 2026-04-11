#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import gzip
import re
import time
import html
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# ===================== 配置 ===================== #
CHANNEL_API_URL = "https://content-api.mytvsuper.com/v1/channel/list"
EPG_API_URL = "https://content-api.mytvsuper.com/v1/epg"

PLATFORM = "web"
COUNTRY_CODE = "ZP"
PROFILE_CLASS = "general"

DAYS_RANGE = 7
MAX_WORKERS = 8
TIMEOUT = 30
RETRY_COUNT = 2
REQUEST_DELAY = 0.3

OUTPUT_XML = "epg.xml"
OUTPUT_GZ = "epg.xml.gz"
OUTPUT_JSON = "epg.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.mytvsuper.com/"
}

# ===================== 工具 ===================== #

def clean(text):
    """防止XML炸裂（关键）"""
    if not text:
        return ""
    return html.escape(str(text).strip())


def to_xml_time(dt_str):
    """时间转换（稳定版）"""
    if not dt_str:
        return ""
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"]:
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.strftime("%Y%m%d%H%M%S") + " +0800"
        except:
            continue
    return ""


def indent(elem, level=0):
    """
    XML 美化（替代 minidom，稳定不崩）
    """
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            indent(e, level + 1)
        if not e.tail or not e.tail.strip():
            e.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


# ===================== 频道 ===================== #

def clean_name(name):
    if not name:
        return ""
    return re.sub(r'\s*\(Free\)|\s*\(免費\)|\s*Free|\s*免費', '', name, flags=re.I).strip()


def get_channels():
    params = {
        "platform": PLATFORM,
        "country_code": COUNTRY_CODE,
        "profile_class": PROFILE_CLASS
    }

    r = requests.get(CHANNEL_API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
    data = r.json()

    result = {}

    for c in data.get("channels", []):
        code = c.get("network_code")
        if not code:
            continue

        name_tc = clean_name(c.get("name_tc", ""))
        name_en = clean_name(c.get("name_en", ""))

        result[code] = {
            "name_tc": name_tc or code,
            "name_en": name_en or name_tc or code,
            "icon": c.get("landscape_poster") or ""
        }

    print(f"[OK] 频道数量: {len(result)}")
    return result


# ===================== EPG ===================== #

def fetch_epg(code, s, e):
    try:
        time.sleep(REQUEST_DELAY)

        params = {
            "platform": PLATFORM,
            "country_code": COUNTRY_CODE,
            "network_code": code,
            "from": s,
            "to": e
        }

        r = requests.get(EPG_API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        return code, r.json()

    except Exception as e:
        print(f"[WARN] {code} 失败: {e}")
        return code, None


def fetch_all(channels, s, e):
    epg = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_epg, c, s, e) for c in channels]

        for i, f in enumerate(as_completed(futures), 1):
            code, data = f.result()
            epg[code] = data

            if i % 20 == 0:
                print(f"[INFO] {i}/{len(channels)}")

    return epg


# ===================== XML ===================== #

def build_xml(channels, epg_data):
    tv = ET.Element("tv")
    tv.set("generator-info-name", "myTV SUPER EPG")

    # channels
    for code, c in channels.items():
        ch = ET.SubElement(tv, "channel", id=code)

        ET.SubElement(ch, "display-name", lang="zh").text = clean(c["name_tc"])
        ET.SubElement(ch, "display-name", lang="en").text = clean(c["name_en"])

        if c["icon"]:
            ET.SubElement(ch, "icon", src=c["icon"])

    total = 0

    # programmes
    for code, data in epg_data.items():
        if not data:
            continue

        items = extract(data)

        for p in items:
            start = to_xml_time(p.get("start_datetime"))
            if not start:
                continue

            prog = ET.SubElement(tv, "programme", {
                "channel": code,
                "start": start
            })

            # title
            tc = p.get("programme_title_tc", "")
            en = p.get("programme_title_en", "")

            if tc:
                ET.SubElement(prog, "title", lang="zh").text = clean(tc[:200])
            if en:
                ET.SubElement(prog, "title", lang="en").text = clean(en[:200])

            # desc
            desc = p.get("episode_synopsis_tc", "")
            if desc:
                ET.SubElement(prog, "desc", lang="zh").text = clean(desc[:300])

            total += 1

    print(f"[OK] 节目数: {total}")

    indent(tv)  # 👈 美化（安全版）

    return tv


# ===================== JSON提取 ===================== #

def extract(data):
    """安全提取节目"""
    if isinstance(data, list):
        out = []
        for i in data:
            out.extend(extract(i))
        return out

    if isinstance(data, dict):
        if "start_datetime" in data:
            return [data]

        for k in ["epg", "programmes", "items", "list"]:
            if k in data and isinstance(data[k], list):
                out = []
                for i in data[k]:
                    out.extend(extract(i))
                return out

        out = []
        for v in data.values():
            out.extend(extract(v))
        return out

    return []


# ===================== 保存 ===================== #

def save(xml):
    xml_bytes = ET.tostring(xml, encoding="utf-8", xml_declaration=True)

    with open(OUTPUT_XML, "wb") as f:
        f.write(xml_bytes)

    with gzip.open(OUTPUT_GZ, "wb") as f:
        f.write(xml_bytes)

    print("[OK] 文件已生成")


def save_json(data):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===================== MAIN ===================== #

def main():
    print("[INFO] 开始抓EPG...")

    s = datetime.now().strftime("%Y%m%d")
    e = (datetime.now() + timedelta(days=DAYS_RANGE)).strftime("%Y%m%d")

    channels = get_channels()
    epg = fetch_all(channels.keys(), s, e)

    save_json(epg)

    xml = build_xml(channels, epg)

    save(xml)

    print("[DONE] 完成")


if __name__ == "__main__":
    main()
