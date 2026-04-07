# -*- coding: utf-8 -*-
import os
import json
import time
import gzip
import logging
import requests
from xml.etree.ElementTree import Element, SubElement, tostring

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
CONFIG = os.path.join(BASE, "config.json")

os.makedirs(OUT, exist_ok=True)

# ===================== LOG ===================== #
log_file = os.path.join(OUT, "nowtv.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("NOWTV")


# ===================== HTTP（核心防炸） ===================== #
def fetch(url, retry=3):
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        "Referer": "https://nowplayer.now.com/tvguide/",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest"
    }

    for i in range(retry):
        try:
            r = requests.get(url, headers=headers, timeout=20)

            log.info(f"GET {url}")
            log.info(f"STATUS {r.status_code}")

            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    log.error("❌ 非JSON返回（前300字符）")
                    log.error(r.text[:300])
                    return None

            if r.status_code == 500:
                log.warning(f"500错误，重试 {i+1}/{retry}")
                time.sleep(2 * (i + 1))
                continue

            log.error(f"HTTP ERROR {r.status_code}")
            return None

        except Exception as e:
            log.error(f"REQUEST ERROR: {e}")
            time.sleep(2)

    return None


# ===================== CONFIG ===================== #
def load_config():
    if not os.path.exists(CONFIG):
        return {}

    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"CONFIG ERROR: {e}")
        return {}


# ===================== 1. 获取频道 ===================== #
def get_channels():
    url = "https://nowplayer.now.com/tvguide/epglist?day=0"

    data = fetch(url)
    if not data:
        return []

    chs = data.get("channels", [])
    log.info(f"频道数量: {len(chs)}")

    result = []
    for c in chs:
        result.append({
            "id": c.get("channelnum"),
            "name": c.get("title"),
            "logo": c.get("icon", "")
        })

    return result


# ===================== 分批工具（解决500关键） ===================== #
def chunk_list(lst, size=8):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]


# ===================== 2. 分批抓EPG ===================== #
def get_epg(channel_ids):
    all_data = {}

    for batch in chunk_list(channel_ids, 8):
        base = "https://nowplayer.now.com/tvguide/epglist?day=0"

        for cid in batch:
            base += f"&channelIdList[]={cid}"

        log.info(f"批次请求: {batch}")

        data = fetch(base)

        if not data:
            log.warning("本批次失败，跳过")
            continue

        for ch in data.get("channels", []):
            cid = ch.get("channelnum")
            all_data[cid] = ch.get("programs", [])

        time.sleep(1)

    log.info(f"EPG完成频道数: {len(all_data)}")
    return all_data


# ===================== 3. XML ===================== #
def build_xml(channels, epg):
    tv = Element("tv")

    for c in channels:
        cid = c["id"]

        node = SubElement(tv, "channel", id=str(cid))
        SubElement(node, "display-name").text = c["name"]

        if c["logo"]:
            SubElement(node, "icon", src=c["logo"])

        for p in epg.get(cid, []):
            prog = SubElement(tv, "programme", {
                "start": p.get("startTime", ""),
                "stop": p.get("endTime", ""),
                "channel": str(cid)
            })

            SubElement(prog, "title").text = p.get("title", "")

    xml_bytes = tostring(tv, encoding="utf-8")

    xml_path = os.path.join(OUT, "nowtv.xml")

    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    log.info(f"XML生成: {xml_path}")
    return xml_path


# ===================== 4. gzip ===================== #
def gzip_file(path):
    gz = path + ".gz"

    with open(path, "rb") as f_in:
        with gzip.open(gz, "wb") as f_out:
            f_out.write(f_in.read())

    log.info(f"GZ生成: {gz}")


# ===================== MAIN ===================== #
def main():
    log.info("========== NOWTV EPG START ==========")

    cfg = load_config()

    channels = get_channels()
    if not channels:
        log.error("无频道数据")
        return

    ids = [c["id"] for c in channels]

    epg = get_epg(ids)

    xml = build_xml(channels, epg)
    gzip_file(xml)

    log.info("========== DONE ==========")


if __name__ == "__main__":
    main()
