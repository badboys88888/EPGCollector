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
# 日志系统
# ========================
def log(msg):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


# ========================
# 获取频道
# ========================
def fetch_channels():
    log("🔵 开始获取频道列表...")

    res = requests.get(BASE_URL, headers=HEADERS, timeout=20)
    log(f"频道页状态码: {res.status_code}")

    res.raise_for_status()

    matches = re.findall(r'<option value="([^"]+)">([^<]+)</option>', res.text)

    channels = []
    for value, name in matches:
        name = re.sub(r'\s*-\s*\[Channel\s*\d+\]\s*$', '', name.strip())
        channels.append({"value": value, "name": name})

    log(f"✅ 频道解析完成: {len(channels)} 个")
    return channels


# ========================
# 获取节目
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

    log(f"📡 请求频道={channel_value} 状态码={res.status_code}")

    res.raise_for_status()

    times = re.findall(r'<td class="text-center">(.*?)</td>', res.text)
    programs = re.findall(r'title="(.*?)" rel', res.text)

    log(f"📊 解析结果: times={len(times)} programs={len(programs)}")

    return times, programs


# ========================
# XML生成
# ========================
def build_xml(date_prefix, times, programs, channel_name):
    xml = ""

    count = min(len(programs), len(times)//2 - 1)

    log(f"    ✳️ 生成节目数: {count}")

    for i in range(count):
        start = date_prefix + times[i*2].replace(":", "") + "00 +0700"
        stop = date_prefix + times[(i+1)*2].replace(":", "") + "00 +0700"

        title = programs[i].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        xml += f'<programme start="{start}" stop="{stop}" channel="{channel_name}">\n'
        xml += f'<title lang="zh">{title}</title>\n'
        xml += '</programme>\n'

    return xml


# ========================
# 主程序
# ========================
def main():

    log("🚀 ===== MNCVISION EPG START =====")

    # 🔥 强制创建目录（修复你报错核心）
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
        log(f"❌ 获取频道失败: {e}")
        return

    total = len(channels)

    for idx, ch in enumerate(channels, 1):

        log(f"\n📺 [{idx}/{total}] 频道: {ch['name']}")

        try:
            t1, p1 = fetch_schedule(ch["value"], d1)
            xml += build_xml(d1_xml, t1, p1, ch["name"])

            t2, p2 = fetch_schedule(ch["value"], d2)
            xml += build_xml(d2_xml, t2, p2, ch["name"])

            time.sleep(1)

        except Exception as e:
            log(f"❌ 频道失败: {ch['name']}")
            log(str(e))
            log(traceback.format_exc())

    xml += "</tv>"

    # ========================
    # 写 XML
    # ========================
    xml_file = "mncvision_epg/mncvision.xml"
    gz_file = "mncvision_epg/mncvision.xml.gz"

    log("💾 写入 XML...")

    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml)

    log(f"✅ XML完成: {xml_file} ({len(xml)} bytes)")

    # ========================
    # gzip
    # ========================
    log("🗜️ 压缩 gzip...")

    with open(xml_file, "rb") as f_in:
        with gzip.open(gz_file, "wb") as f_out:
            f_out.writelines(f_in)

    log(f"✅ gzip完成: {gz_file}")

    log("🎉 ===== 全部完成 =====")


if __name__ == "__main__":
    main()
