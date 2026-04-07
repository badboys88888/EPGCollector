#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import time
import datetime

# =========================
# 配置
# =========================
BASE_PAGE = "https://www.bein.com/en/epg/"
AJAX_URL = "https://www.bein.com/en/epg-ajax-template/"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_PAGE,
    "Accept-Language": "en-US,en;q=0.9"
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

    # 多种匹配方式（防页面变化）
    patterns = [
        r'postid["\']?\s*:\s*["\']?(\d+)',
        r'postid\s*=\s*(\d+)',
        r'"postid":"(\d+)"'
    ]

    for p in patterns:
        match = re.search(p, r.text)
        if match:
            postid = match.group(1)
            log(f"✅ postid = {postid}")
            return postid

    raise Exception("❌ postid 未找到（页面结构可能变化）")


# =========================
# 2. 请求 EPG
# =========================
def fetch_epg(postid, category="sports"):
    log("📡 请求 EPG Ajax...")

    params = {
        "action": "epg_fetch",
        "offset": "+0",
        "category": category,
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
# 3. 简单解析（可扩展）
# =========================
def parse_epg(html):
    log("🧩 解析EPG...")

    # ⚠️ beIN返回结构不固定，这里做“弱解析”
    items = re.findall(r'([0-9]{2}:[0-9]{2}).*?([^<]+)', html)

    results = []

    for t in items:
        if len(t) >= 2:
            results.append({
                "time": t[0],
                "title": t[1].strip()
            })

    log(f"✅ 解析到 {len(results)} 条节目")
    return results


# =========================
# 4. 主函数
# =========================
def main():

    log("🚀 ===== BEIN EPG START =====")

    try:
        # 1️⃣ 动态获取 postid
        postid = get_postid()

        time.sleep(1)

        # 2️⃣ 获取EPG
        raw = fetch_epg(postid)

        # 3️⃣ 解析
        epg = parse_epg(raw)

        # 4️⃣ 输出测试
        log("📺 示例输出：")

        for i, item in enumerate(epg[:10]):
            print(f"{item['time']} - {item['title']}")

        log("🎉 完成")

    except Exception as e:
        log(f"❌ 错误: {e}")


if __name__ == "__main__":
    main()
