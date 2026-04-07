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
}


# =========================
# 日志
# =========================
def log(msg):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


# =========================
# 1. 获取 postid（动态）
# =========================
def get_postid():
    log("🔵 获取 postid...")

    r = requests.get(BASE_PAGE, headers=HEADERS, timeout=20)
    r.raise_for_status()

    # 多种兼容匹配
    patterns = [
        r'postid["\']?\s*[:=]\s*["\']?(\d+)',
        r'"postid":"(\d+)"',
        r'postid\s*=\s*(\d+)'
    ]

    for p in patterns:
        m = re.search(p, r.text)
        if m:
            postid = m.group(1)
            log(f"✅ postid = {postid}")
            return postid

    raise Exception("❌ postid 未找到")


# =========================
# 2. 获取 EPG
# =========================
def fetch_epg(postid):
    log("📡 请求 EPG Ajax...")

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
# 3. 解析EPG（弱结构解析）
# =========================
def parse_epg(html):
    log("🧩 解析EPG数据...")

    # 提取时间 + 标题（兼容HTML变化）
    items = re.findall(r'([0-9]{2}:[0-9]{2}).{0,80}?([^<\n]{3,})', html)

    epg = []
    for t, title in items:
        title = title.strip()
        if len(title) > 2:
            epg.append((t, title))

    log(f"✅ 解析到 {len(epg)} 条节目")
    return epg


# =========================
# 4. XML生成（标准XMLTV）
# =========================
def build_xml(epg):
    log("🏗️ 生成 XML...")

    now_date = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S +0000")

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += "<tv>\n"

    for t, title in epg:

        # ⚠️ 简化时间（EPG源不完整时的兼容写法）
        start = now_date
        stop = now_date

        title = title.replace("&", "&amp;")

        xml += f'  <programme start="{start}" stop="{stop}" channel="beIN">\n'
        xml += f'    <title lang="en">{title}</title>\n'
        xml += f'  </programme>\n'

    xml += "</tv>"

    log("✅ XML生成完成")
    return xml


# =========================
# 5. 写文件 + gzip
# =========================
def save_output(xml):
    os.makedirs("bein", exist_ok=True)

    xml_file = "bein/bein.xml"
    gz_file = "bein/bein.xml.gz"

    log("💾 写入 XML...")

    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml)

    log("🗜️ 生成 GZ...")

    with open(xml_file, "rb") as f_in:
        with gzip.open(gz_file, "wb") as f_out:
            f_out.writelines(f_in)

    log("🎉 输出完成：bein.xml + bein.xml.gz")


# =========================
# 主流程
# =========================
def main():

    log("🚀 ===== BEIN EPG START =====")

    try:
        postid = get_postid()
        time.sleep(1)

        raw = fetch_epg(postid)
        epg = parse_epg(raw)

        xml = build_xml(epg)
        save_output(xml)

        log("🏁 全部完成")

    except Exception as e:
        log(f"❌ 错误: {e}")


if __name__ == "__main__":
    main()
