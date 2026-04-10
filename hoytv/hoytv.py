import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from xml.dom import minidom
import gzip

CHANNEL_API = "https://api2.hoy.tv/api/v3/a/channel?orientation=landscape"
HEADERS = {"User-Agent": "Mozilla/5.0"}


# =========================
# 时间转换
# =========================
def to_xmltv_time(t):
    dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y%m%d%H%M%S") + " +0800"


# =========================
# 获取频道
# =========================
def get_channels():
    r = requests.get(CHANNEL_API, headers=HEADERS, timeout=10)
    return r.json()["data"]


# =========================
# 获取EPG
# =========================
def fetch_epg(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.encoding = "utf-8"
    return ET.fromstring(r.text)


# =========================
# 清洗
# =========================
def clean(x):
    return x.strip() if x and x.strip() else None


# =========================
# 获取标题（终极版）
# =========================
def get_title(item):
    epi = item.find("EpisodeInfo")
    if epi is not None:
        title = clean(epi.findtext("EpisodeShortDescription"))
        if title:
            idx = epi.findtext("EpisodeIndex")
            if idx and idx != "0":
                title = f"{title} 第{idx}集"
            return title

    cs = item.find("ComScore")
    if cs is not None:
        title = clean(cs.findtext("ns_st_pr"))
        if title:
            return title

    info = item.find("ProgramInfo")
    if info is not None:
        title = clean(info.findtext("ProgramTitle"))
        if title:
            return title

    return None


# =========================
# XML美化
# =========================
def pretty_xml(elem):
    rough = ET.tostring(elem, "utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ")


# =========================
# 主逻辑
# =========================
def build_epg():
    channels = get_channels()
    tv = ET.Element("tv")

    now = datetime.now()
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    total = 0

    # =========================
    # channel
    # =========================
    for ch in channels:
        ch_id = str(ch["videos"]["id"])
        ch_name = ch["name"]["zh_hk"]

        c = ET.SubElement(tv, "channel")
        c.set("id", ch_id)

        d = ET.SubElement(c, "display-name")
        d.text = ch_name

        if ch.get("image"):
            icon = ET.SubElement(c, "icon")
            icon.set("src", ch["image"])

    # =========================
    # programme
    # =========================
    for ch in channels:
        ch_id = str(ch["videos"]["id"])
        epg_url = ch.get("epg")

        if not epg_url:
            continue

        print(f"[处理] {ch['name']['zh_hk']}")

        try:
            root = fetch_epg(epg_url)
        except:
            continue

        for item in root.findall(".//EpgItem"):
            start = item.findtext("EpgStartDateTime")
            end = item.findtext("EpgEndDateTime")

            try:
                end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
            except:
                continue

            # 删除昨天以前
            if end_dt < yesterday:
                continue

            title = get_title(item)
            if not title:
                continue

            p = ET.SubElement(tv, "programme")
            p.set("channel", ch_id)
            p.set("start", to_xmltv_time(start))
            p.set("stop", to_xmltv_time(end))

            t = ET.SubElement(p, "title")
            t.text = title

            total += 1

    # =========================
    # 输出（最终文件名）
    # =========================
    xml_str = pretty_xml(tv)

    with open("hoytv/hoytv.xml", "w", encoding="utf-8") as f:
        f.write(xml_str)

    with gzip.open("hoytv/hoytv.xml.gz", "wt", encoding="utf-8") as f:
        f.write(xml_str)

    print("\n===== 完成 =====")
    print(f"节目总数: {total}")
    print("输出: hoytv/hoytv.xml / hoytv/hoytv.xml.gz")


if __name__ == "__main__":
    build_epg()
