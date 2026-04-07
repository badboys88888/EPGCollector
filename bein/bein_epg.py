#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import gzip
import os
import datetime
import time

BASE_PAGE = "https://www.bein.com/en/epg/"
AJAX_URL = "https://www.bein.com/en/epg-ajax-template/"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_PAGE,
    "Accept-Language": "en-US,en;q=0.9",
}


# =========================
# 日志
# =========================
def log(msg):
    print(f"[INFO] {msg}", flush=True)


# =========================
# 获取 postid（最终稳定版）
# =========================
def get_postid():
    log("获取 postid...")

    r = requests.get(BASE_PAGE, headers=HEADERS, timeout=20)
    r.raise_for_status()
    html = r.text

    # 1️⃣ 常规 HTML
    patterns = [
        r'postid["\']?\s*[:=]\s*["\']?(\d+)',
        r'"postid"\s*:\s*"*(\d+)"*',
        r'postId["\']?\s*[:=]\s*["\']?(\d+)',
    ]

    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            log(f"postid = {m.group(1)} (HTML)")
            return m.group(1)

    # 2️⃣ JS / script 里找
    scripts = re.findall(r'<script.*?>(.*?)</script>', html, re.S)

    for s in scripts:
        m = re.search(r'["\']postid["\']\s*:\s*["\']?(\d+)', s)
        if m:
            log(f"postid = {m.group(1)} (SCRIPT)")
            return m.group(1)

    # 3️⃣ fallback（暴力）
    m = re.search(r'postid[^0-9]{0,10}(\d{3,10})', html)
    if m:
        log(f"postid = {m.group(1)} (FALLBACK)")
        return m.group(1)

    # ❌ 失败输出调试
    log("❌ postid 获取失败，输出HTML前500字符：")
    print(html[:500])

    raise Exception("postid 未找到")


# =========================
# 获取EPG
# =========================
def fetch_epg(postid):
    log("请求EPG...")

    params = {
        "action": "epg_fetch",
        "offset": "+0",
        "category": "sports",
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": "",
        "language": "EN",
        "postid": postid,
        "loadindex": "0"
    }

    r = requests.get(AJAX_URL, params=params, headers=HEADERS, timeout=20)

    log(f"状态码: {r.status_code}")
    r.raise_for_status()

    return r.text


# =========================
# 解析EPG（尽量通用）
# =========================
def parse_epg(html):
    log("解析EPG...")

    items = re.findall(r'([0-9]{2}:[0-9]{2}).{0,100}?([^<\n]{3,})', html)

    epg = []
    for t, title in items:
        title = title.strip()
        if len(title) > 2:
            epg.append((t, title))

    log(f"解析到 {len(epg)} 条节目")
    return epg


# =========================
# XML生成（标准格式）
# =========================
def build_xml(epg):
    log("生成XML...")

    now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S +0000")

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += "<tv>\n"

    for t, title in epg:
        title = title.replace("&", "&amp;")

        xml += f'  <programme start="{now}" stop="{now}" channel="beIN">\n'
        xml += f'    <title lang="en">{title}</title>\n'
        xml += f'  </programme>\n'

    xml += "</tv>"

    return xml


# =========================
# 输出文件
# =========================
def save(xml):
    os.makedirs("bein", exist_ok=True)

    xml_file = "bein/bein.xml"
    gz_file = "bein/bein.xml.gz"

    log("写入 XML...")

    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml)

    log("压缩 GZ...")

    with open(xml_file, "rb") as f_in:
        with gzip.open(gz_file, "wb") as f_out:
            f_out.writelines(f_in)

    log("输出完成：bein.xml + bein.xml.gz")


# =========================
# 主流程
# =========================
def main():

    log("===== BEIN EPG START =====")

    try:
        postid = get_postid()
        time.sleep(1)

        html = fetch_epg(postid)

        epg = parse_epg(html)

        xml = build_xml(epg)

        save(xml)

        log("全部完成")

    except Exception as e:
        log(f"错误: {e}")


if __name__ == "__main__":
    main()
