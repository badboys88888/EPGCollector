#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
myTV SUPER EPG 抓取 -> XMLTV (双语)
修改点 (2026-11-16, 更正):
- 双语可以显示, 不是双语的问题, 别只留中文。
- 真正原因是没加 stop: 原接口只有 start_datetime,
  没有结束时间, 很多播放器没有 stop 就不显示节目。
  stop = 下一个节目的 start, 最后一个节目默认 +30 分钟。
- 删掉 clean() 里的 html.escape(): ElementTree 序列化时
  自己会转义 & < >, 先 escape 会变成双重转义。
- RETRY_COUNT 之前定义了没用, 现在 fetch_epg 真重试。
- 节目按 start 排序, 空标题跳过。
"""
import requests
import json
import gzip
import re
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

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


def clean(text):
    """只去空白, 不做 html.escape, 转义交给 ElementTree"""
    if not text:
        return ""
    return str(text).strip()


def parse_dt(dt_str):
    if not dt_str:
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"]:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def to_xml_time(dt):
    if not dt:
        return ""
    return dt.strftime("%Y%m%d%H%M%S") + " +0800"


def indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            indent(e, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def clean_name(name):
    if not name:
        return ""
    return re.sub(r'\s*\(Free\)|\s*\(免費\)|\s*Free|\s*免費', '', name, flags=re.I).strip()


def get_channels():
    params = {"platform": PLATFORM, "country_code": COUNTRY_CODE,
              "profile_class": PROFILE_CLASS}
    r = requests.get(CHANNEL_API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
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


def fetch_epg(code, s, e):
    params = {"platform": PLATFORM, "country_code": COUNTRY_CODE,
              "network_code": code, "from": s, "to": e}
    for attempt in range(RETRY_COUNT + 1):
        try:
            time.sleep(REQUEST_DELAY)
            r = requests.get(EPG_API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return code, r.json()
        except Exception as ex:
            if attempt == RETRY_COUNT:
                print(f"[WARN] {code} 失败: {ex}")
                return code, None
            time.sleep(1)


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


def extract(data):
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


def build_xml(channels, epg_data):
    tv = ET.Element("tv")
    tv.set("generator-info-name", "myTV SUPER EPG")

    # 双语台名
    for code, c in channels.items():
        ch = ET.SubElement(tv, "channel", id=code)
        ET.SubElement(ch, "display-name", lang="zh").text = clean(c["name_tc"])
        ET.SubElement(ch, "display-name", lang="en").text = clean(c["name_en"])
        if c["icon"]:
            ET.SubElement(ch, "icon", src=c["icon"])

    total = 0
    for code, data in epg_data.items():
        if not data:
            continue
        items = [p for p in extract(data) if parse_dt(p.get("start_datetime"))]
        items.sort(key=lambda p: parse_dt(p["start_datetime"]))
        for idx, p in enumerate(items):
            start_dt = parse_dt(p.get("start_datetime"))
            # stop = 下一个节目开始, 最后一个默认 +30 分钟
            if idx + 1 < len(items):
                stop_dt = parse_dt(items[idx + 1].get("start_datetime"))
            else:
                stop_dt = start_dt + timedelta(minutes=30)
            if not stop_dt or stop_dt <= start_dt:
                stop_dt = start_dt + timedelta(minutes=30)

            tc = clean(p.get("programme_title_tc", ""))
            en = clean(p.get("programme_title_en", ""))
            if not tc and not en:
                continue
            prog = ET.SubElement(tv, "programme", {
                "channel": code,
                "start": to_xml_time(start_dt),
                "stop": to_xml_time(stop_dt),
            })
            # 双语标题/简介
            if tc:
                ET.SubElement(prog, "title", lang="zh").text = tc[:200]
            if en:
                ET.SubElement(prog, "title", lang="en").text = en[:200]
            desc = clean(p.get("episode_synopsis_tc", ""))
            if desc:
                ET.SubElement(prog, "desc", lang="zh").text = desc[:300]
            desc_en = clean(p.get("episode_synopsis_en", ""))
            if desc_en:
                ET.SubElement(prog, "desc", lang="en").text = desc_en[:300]
            total += 1

    print(f"[OK] 节目数: {total}")
    indent(tv)
    return tv


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
