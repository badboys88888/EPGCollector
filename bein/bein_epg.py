#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import gzip
import datetime
import os

BASE_PAGE = "https://www.bein.com/en/epg/"
AJAX_URL = "https://www.bein.com/en/epg-ajax-template/"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_PAGE,
}


# =========================
# 日志
# =========================
def log(msg):
    print(f"[INFO] {msg}", flush=True)


# =========================
# 获取 postid
# =========================
def get_postid():
    log("获取 postid...")

    r = requests.get(BASE_PAGE, headers=HEADERS, timeout=20)
    r.raise_for_status()

    match = re.search(r'postid["\']?\s*[:=]\s*["\']?(\d+)', r.text)

    if not match:
        raise Exception("postid 未找到")

    postid = match.group(1)
    log(f"postid = {postid}")
    return postid


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
    r.raise_for_status()

    return r.text


# =========================
# 简单解析（通用弱解析）
# =========================
def parse_epg(html):
    log("解析EPG...")

    items = re.findall(r'([0-9]{2}:[0-9]{2}).{0,50}?([^<\n]{3,})', html)

    epg = []
    for t, title in items:
        epg.append((t, title.strip()))

    log(f"解析到 {len(epg)} 条")
    return epg


# =========================
# XML生成
# =========================
def build_xml(epg):
    now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S +0000")

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n'

    for i, (t, title) in enumerate(epg):
        start = now
        stop = now

        title = title.replace("&", "&amp;")

        xml += f'<programme start="{start}" stop="{stop}" channel="beIN">\n'
        xml += f'<title>{title}</title>\n'
        xml += '</programme>\n'

    xml += '</tv>'
    return xml


# =========================
# 主程序
# =========================
def main():

    log("===== BEIN EPG START =====")

    # 📁 确保目录存在
    os.makedirs("bein", exist_ok=True)

    # 1️⃣ postid
    postid = get_postid()

    # 2️⃣ EPG
    raw = fetch_epg(postid)

    # 3️⃣ parse
    epg = parse_epg(raw)

    # 4️⃣ XML
    xml = build_xml(epg)

    # =========================
    # 输出文件（你要的）
    # =========================

    xml_file = "bein/bein.xml"
    gz_file = "bein/bein.xml.gz"

    log("写入 XML...")

    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml)

    log("生成 GZ...")

    with open(xml_file, "rb") as f_in:
        with gzip.open(gz_file, "wb") as f_out:
            f_out.writelines(f_in)

    log("完成输出：bein.xml + bein.xml.gz")


if __name__ == "__main__":
    main()
