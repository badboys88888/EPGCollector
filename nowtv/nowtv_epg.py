# -*- coding: utf-8 -*-
import os
import json
import gzip
import logging
import requests
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

os.makedirs(OUT_DIR, exist_ok=True)

# ===================== LOG ===================== #
log_file = os.path.join(OUT_DIR, "nowtv.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("NOWTV")

# ===================== HTTP ===================== #
def get_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://nowplayer.now.com/",
        "Accept": "application/json"
    }

    try:
        r = requests.get(url, headers=headers, timeout=20)
        log.info(f"GET {url}")
        log.info(f"STATUS {r.status_code}")

        if r.status_code != 200:
            log.error(f"HTTP ERROR {r.status_code}")
            return None

        try:
            return r.json()
        except Exception:
            log.error("❌ 非JSON返回（前300字）：")
            log.error(r.text[:300])
            return None

    except Exception as e:
        log.error(f"REQUEST ERROR: {e}")
        return None


# ===================== CONFIG ===================== #
def load_config():
    if not os.path.exists(CONFIG_FILE):
        log.warning("无config.json")
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"config错误: {e}")
        return {}


# ===================== 1. 获取频道（动态） ===================== #
def fetch_channels():
    url = "https://nowplayer.now.com/tvguide/epglist?day=0"

    data = get_json(url)
    if not data:
        return []

    channels = data.get("channels", [])
    log.info(f"频道总数: {len(channels)}")

    result = []

    for c in channels:
        cid = c.get("channelnum")
        result.append({
            "id": cid,
            "name": c.get("title"),
            "logo": c.get("icon", "")
        })

    return result


# ===================== 2. 获取EPG ===================== #
def fetch_epg(channel_ids):
    if not channel_ids:
        return {}

    base = "https://nowplayer.now.com/tvguide/epglist?day=0"

    for cid in channel_ids:
        base += f"&channelIdList[]={cid}"

    data = get_json(base)
    if not data:
        return {}

    epg_map = {}

    for ch in data.get("channels", []):
        cid = ch.get("channelnum")
        epg_map[cid] = ch.get("programs", [])

    log.info(f"EPG频道数: {len(epg_map)}")
    return epg_map


# ===================== 3. XML输出 ===================== #
def build_xml(channels, epg_map):
    tv = Element("tv")

    for ch in channels:
        cid = ch["id"]

        ch_node = SubElement(tv, "channel", id=str(cid))
        SubElement(ch_node, "display-name").text = ch["name"]

        if ch["logo"]:
            SubElement(ch_node, "icon", src=ch["logo"])

        for p in epg_map.get(cid, []):
            prog = SubElement(tv, "programme", {
                "start": p.get("startTime", ""),
                "stop": p.get("endTime", ""),
                "channel": str(cid)
            })

            SubElement(prog, "title").text = p.get("title", "")

    xml_bytes = tostring(tv, encoding="utf-8")

    xml_path = os.path.join(OUT_DIR, "nowtv.xml")
    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    log.info(f"XML生成: {xml_path}")
    return xml_path


# ===================== 4. gzip ===================== #
def gzip_file(path):
    gz_path = path + ".gz"

    with open(path, "rb") as f_in:
        with gzip.open(gz_path, "wb") as f_out:
            f_out.write(f_in.read())

    log.info(f"GZ生成: {gz_path}")
    return gz_path


# ===================== MAIN ===================== #
def main():
    log.info("========== NOWTV EPG START ==========")

    cfg = load_config()

    channels = fetch_channels()
    if not channels:
        log.error("无频道数据")
        return

    ids = [c["id"] for c in channels]

    epg = fetch_epg(ids)

    xml = build_xml(channels, epg)
    gzip_file(xml)

    log.info("========== DONE ==========")


if __name__ == "__main__":
    main()
