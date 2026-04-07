import requests
import gzip
import xml.etree.ElementTree as ET
import json
from collections import defaultdict

# ===================== 配置 ===================== #
SOURCES = [
    {
        "url": "https://github.com/jeffrybp/epgtv/raw/refs/heads/main/public/mytvsupercom.xml.gz",
        "name": "mytvsupercom",
        "is_gz": True
    },
    {
        "url": "https://github.com/jeffrybp/epgtv/raw/refs/heads/main/public/nowcomhk.xml.gz",
        "name": "nowcomhk",
        "is_gz": True
    },
    {
        "url": "https://github.com/jeffrybp/epgtv/raw/refs/heads/main/public/kbscokr.xml.gz",
        "name": "kbscokr",
        "is_gz": True
    },
        {
        "url": "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
        "name": "www.sky.com",
        "is_gz": True
    },
    {
        "url": "https://github.com/jeffrybp/epgtv/raw/refs/heads/main/public/beinsportsconnecthk.xml.gz",
        "name": "beinsportsconnecthk",
        "is_gz": True
    }
]

OUTPUT_FILE = "channels.json"

# ===================== 读取XML ===================== #
def load_xml(url, is_gz):
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    if is_gz:
        data = gzip.decompress(r.content)
        return ET.fromstring(data)

    return ET.fromstring(r.content)

# ===================== 主逻辑 ===================== #
channels = {}

for src in SOURCES:
    print(f"Loading: {src['name']}")

    try:
        root = load_xml(src["url"], src["is_gz"])
    except Exception as e:
        print(f"Failed {src['name']}: {e}")
        continue

    for ch in root.findall("channel"):
        cid = ch.attrib.get("id")
        if not cid:
            continue

        name = ch.findtext("display-name")
        icon_node = ch.find("icon")
        icon = icon_node.attrib.get("src") if icon_node is not None else None

        # 如果同一个 channel 已存在，就不覆盖（保留第一个来源）
        if cid not in channels:
            channels[cid] = {
                "name": name,
                "icon": icon,
                "source": src["name"]
            }

# ===================== 输出 JSON ===================== #
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(channels, f, ensure_ascii=False, indent=2)

print(f"\nDone! Saved to {OUTPUT_FILE}")
