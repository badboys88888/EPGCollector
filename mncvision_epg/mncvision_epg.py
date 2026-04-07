#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import time
import datetime
import gzip
import os
import traceback

BASE_URL = "https://www.mncvision.id/schedule/table"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_URL,
    "Origin": "https://www.mncvision.id"
}

# ========================
# 日志
# ========================
def log(msg):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


# ========================
# 获取频道
# ========================
def fetch_channels():
    log("🔵 获取频道列表...")

    res = requests.get(BASE_URL, headers=HEADERS, timeout=20)
    log(f"状态码: {res.status_code}")
    res.raise_for_status()

    matches = re.findall(r'<option value="([^"]+)">([^<]+)</option>', res.text)

    channels = []
    for v, n in matches:
        n = re.sub(r'\s*-\s*\[Channel\s*\d+\]\s*$', '', n.strip())
        channels.append({"value": v, "name": n})

    log(f"✅ 频道数量: {len(channels)}")
    return channels


# ========================
# 节目
# ========================
def fetch_schedule(channel_value, date):
    data = {
        "search_model": "channel",
        "af0rmelement": "aformelement",
        "fdate": date,
        "fchannel": channel_value,
        "submit": "Cari"
    }

    res = requests.post(BASE_URL, headers=HEADERS, data=data, timeout=20)

    log(f"📡 {channel_value} -> {res.status_code}")
    res.raise_for_status()

    times = re.findall(r'<td class="text-center">(.*?)</td>', res.text)
    programs = re.findall(r'title="(.*?)" rel', res.text)

    return times, programs


# ========================
# XML
# ========================
def build_xml(date_prefix, times, programs, channel_name):
    xml = ""
    count = min(len(programs), len(times)//2 - 1)

    for i in range(count):
        start = date_prefix + times[i*2].replace(":", "") + "00 +0700"
        stop = date_prefix + times[(i+1)*2].replace(":", "") + "00 +0700"

        title = programs[i].replace("&", "&amp;")

        xml += f'<programme start="{start}" stop="{stop}" channel="{channel_name}">\n'
        xml += f'<title>{title}</title>\n'
        xml += '</programme>\n'

    return xml


# ========================
# 主程序
# ========================
def main():

    log("🚀 START EPG BUILDER")

    # ======= 强制目录 =======
    os.makedirs("mncvision_epg", exist_ok=True)

    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    d1 = today.strftime("%Y-%m-%d")
    d2 = tomorrow.strftime("%Y-%m-%d")

    d1_xml = today.strftime("%Y%m%d")
    d2_xml = tomorrow.strftime("%Y%m%d")

    xml = '<?xml version="1.0" encoding="UTF-8"?><tv>\n'

    try:
        channels = fetch_channels()
    except Exception as e:
        log("❌ 频道获取失败")
        log(str(e))
        return

    total = len(channels)

    for i, ch in enumerate(channels, 1):

        log(f"\n📺 [{i}/{total}] {ch['name']}")

        try:
            t1, p1 = fetch_schedule(ch["value"], d1)
            xml += build_xml(d1_xml, t1, p1, ch["name"])

            t2, p2 = fetch_schedule(ch["value"], d2)
            xml += build_xml(d2_xml, t2, p2, ch["name"])

            time.sleep(0.8)

        except Exception as e:
            log(f"❌ 失败: {ch['name']}")
            log(traceback.format_exc())

    xml += "</tv>"

    # ======= 输出路径统一 =======
    out_xml = "mncvision_epg/mncvision.xml"
    out_gz = "mncvision_epg/mncvision.xml.gz"

    log("💾 写XML...")

    with open(out_xml, "w", encoding="utf-8") as f:
        f.write(xml)

    log(f"✅ XML完成 ({len(xml)} bytes)")

    log("🗜️ gzip压缩...")

    with open(out_xml, "rb") as f_in:
        with gzip.open(out_gz, "wb") as f_out:
            f_out.writelines(f_in)

    log("🎉 完成全部输出")


if __name__ == "__main__":
    main()
