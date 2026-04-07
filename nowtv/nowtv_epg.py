#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import requests
import xml.etree.ElementTree as ET
import gzip
import shutil
import logging

# ===================== 配置 ===================== #

BASE_EPG_URL = "https://nowplayer.now.com/tvguide/epglist"
CHANNEL_URL = "https://nowplayer.now.com/tvguide/channelList"

BASE_DIR = "nowtv"
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = XML_FILE + ".gz"

DAYS = 2

# ===================== 日志 ===================== #

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)

# ===================== 工具 ===================== #

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"channels": {}}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ===================== 获取全部频道 ===================== #

def get_all_channels():
    logging.info("获取全频道列表...")

    r = requests.get(CHANNEL_URL, timeout=20)
    data = r.json()

    ids = []
    names = {}

    for c in data.get("channels", []):
        cid = str(c.get("channelId") or c.get("channelnum"))
        name = c.get("title") or cid

        if cid:
            ids.append(cid)
            names[cid] = name

    logging.info(f"获取频道数: {len(ids)}")
    return ids, names

# ===================== 获取EPG ===================== #

def fetch_epg(channel_ids, day):
    params = [("day", day)]
    for cid in channel_ids:
        params.append(("channelIdList[]", cid))

    r = requests.get(BASE_EPG_URL, params=params, timeout=30)
    return r.json()

# ===================== XML美化 ===================== #

def indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            indent(e, level + 1)
        if not e.tail or not e.tail.strip():
            e.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

# ===================== 主流程 ===================== #

def main():

    os.makedirs(BASE_DIR, exist_ok=True)

    cfg = load_config()
    config_channels = cfg.get("channels", {})

    api_ids, api_names = get_all_channels()

    tv = ET.Element("tv")

    total = 0

    # ===================== EPG ===================== #
    for day in range(1, DAYS + 1):

        logging.info(f"DAY {day}")

        data = fetch_epg(api_ids, day)
        channels = data.get("channels", [])

        for ch in channels:

            cid = str(ch.get("channelnum"))
            programs = ch.get("programs", [])

            logging.info(f"频道 {cid} +{len(programs)}")

            # ===== config优先，其次API ===== #
            cfg_item = config_channels.get(cid, {})

            name = cfg_item.get("name") or api_names.get(cid) or cid
            logo = cfg_item.get("logo")

            # ===== channel节点 ===== #
            if not any(x.attrib.get("id") == cid for x in tv.findall("channel")):
                ch_node = ET.SubElement(tv, "channel", id=cid)

                ET.SubElement(ch_node, "display-name").text = name

                if logo:
                    ET.SubElement(ch_node, "icon", src=logo)

            # ===== programme ===== #
            for p in programs:
                prog = ET.SubElement(tv, "programme", {
                    "start": p.get("startTime", ""),
                    "stop": p.get("endTime", ""),
                    "channel": cid
                })

                ET.SubElement(prog, "title").text = p.get("title", "")

                total += 1

    logging.info("生成XML...")

    indent(tv)

    tree = ET.ElementTree(tv)
    tree.write(XML_FILE, encoding="utf-8", xml_declaration=True)

    logging.info(f"XML完成: {XML_FILE}")

    # ===================== GZ ===================== #
    logging.info("压缩GZ...")

    with open(XML_FILE, "rb") as f_in:
        with gzip.open(GZ_FILE, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    logging.info(f"GZ完成: {GZ_FILE}")
    logging.info(f"总节目数: {total}")

    # ===================== 自动补config ===================== #
    changed = False

    for cid in api_ids:
        if cid not in config_channels:
            config_channels[cid] = {
                "name": api_names.get(cid, cid),
                "logo": ""
            }
            changed = True

    if changed:
        save_config(cfg)
        logging.info("config.json 已自动更新")

# ===================== 启动 ===================== #

if __name__ == "__main__":
    main()
