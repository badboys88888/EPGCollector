#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import time
import datetime
import gzip

BASE_URL = "https://www.mncvision.id/schedule/table"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_URL,
    "Origin": "https://www.mncvision.id"
}

# =========================
# 获取频道列表
# =========================
def fetch_channels():
    print("获取频道列表...")
    res = requests.get(BASE_URL, headers=HEADERS, timeout=15)
    res.raise_for_status()

    matches = re.findall(r'<option value="([^"]+)">([^<]+)</option>', res.text)

    channels = []
    for value, name in matches:
        name = re.sub(r'\s*-\s*\[Channel\s*\d+\]\s*$', '', name.strip())
        channels.append({
            "value": value,
            "name": name
        })

    print(f"频道数: {len(channels)}")
    return channels


# =========================
# 获取节目表
# =========================
def fetch_schedule(channel_value, date):
    data = {
        "search_model": "channel",
        "af0rmelement": "aformelement",
        "fdate": date,
        "fchannel": channel_value,
        "submit": "Cari"
    }

    res = requests.post(BASE_URL, headers=HEADERS, data=data, timeout=15)
    res.raise_for_status()

    times = re.findall(r'<td class="text-center">(.*?)</td>', res.text)
    programs = re.findall(r'title="(.*?)" rel', res.text)

    return times, programs


# =========================
# 生成节目XML
# =========================
def generate_program_xml(date_prefix, times, programs, channel_name):
    xml = ""
    count = min(len(programs), len(times)//2 - 1)

    for i in range(count):
        start = date_prefix + times[i*2].replace(":", "") + "00 +0700"
        stop = date_prefix + times[(i+1)*2].replace(":", "") + "00 +0700"

        title = programs[i].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        xml += f'<programme start="{start}" stop="{stop}" channel="{channel_name}">\n'
        xml += f'<title lang="zh">{title}</title>\n'
        xml += f'<desc></desc>\n'
        xml += '</programme>\n'

    return xml


# =========================
# 主程序
# =========================
def main():

    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    d1 = today.strftime("%Y-%m-%d")
    d2 = tomorrow.strftime("%Y-%m-%d")

    d1_xml = today.strftime("%Y%m%d")
    d2_xml = tomorrow.strftime("%Y%m%d")

    xml = '<?xml version="1.0" encoding="UTF-8"?><tv>\n'

    channels = fetch_channels()

    # 写 channel 标签
    for ch in channels:
        xml += f'<channel id="{ch["name"]}">\n'
        xml += f'<display-name>{ch["name"]}</display-name>\n'
        xml += '</channel>\n'

    # 抓节目
    for ch in channels:
        print(f"处理频道: {ch['name']}")

        try:
            t1, p1 = fetch_schedule(ch["value"], d1)
            xml += generate_program_xml(d1_xml, t1, p1, ch["name"])

            t2, p2 = fetch_schedule(ch["value"], d2)
            xml += generate_program_xml(d2_xml, t2, p2, ch["name"])

            time.sleep(1)

        except Exception as e:
            print("错误:", e)

    xml += "</tv>"

    # 保存
    with open("mncvision.xml", "w", encoding="utf-8") as f:
        f.write(xml)

    # gzip
    with open("mncvision_epg/mncvision.xml", "rb") as f_in:
        with gzip.open("mncvision.xml.gz", "wb") as f_out:
            f_out.writelines(f_in)

    print("完成：mncvision.xml + mncvision.xml.gz")


if __name__ == "__main__":
    main()
