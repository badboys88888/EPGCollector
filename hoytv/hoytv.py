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
# 清洗文本
# =========================
def clean(x):
    return x.strip() if x and x.strip() else None


# =========================
# ⭐ 获取节目标题（终极版）
# =========================
def get_title(item):
    # 1️⃣ EpisodeShortDescription（主用）
    epi = item.find("EpisodeInfo")
    if epi is not None:
        title = clean(epi.findtext("EpisodeShortDescription"))
        if title:
            ep_index = epi.findtext("EpisodeIndex")
            if ep_index and ep_index != "0":
                title = f"{title} 第{ep_index}集"
            return title

    # 2️⃣ ComScore（新闻类）
    cs = item.find("ComScore")
    if cs is not None:
        title = clean(cs.findtext("ns_st_pr"))
        if title:
            return title

    # 3️⃣ ProgramTitle（备用）
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
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ")


# =========================
# 主逻辑
# =========================
def build_epg():
    channels = get_channels()
    tv = ET.Element("tv")

    total_programs = 0

    now = datetime.now()

    # 保留：昨天 00:00
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # =========================
    # ✅ 先写 channel（关键）
    # =========================
    for ch in channels:
        ch_id = str(ch["videos"]["id"])
        ch_name = ch["name"]["zh_hk"]

        ch_node = ET.SubElement(tv, "channel")
        ch_node.set("id", ch_id)

        name = ET.SubElement(ch_node, "display-name")
        name.text = ch_name

        # logo
        if ch.get("image"):
            icon = ET.SubElement(ch_node, "icon")
            icon.set("src", ch["image"])

    # =========================
    # 写 programme
    # =========================
    for ch in channels:
        ch_id = str(ch["videos"]["id"])
        ch_name = ch["name"]["zh_hk"]
        epg_url = ch.get("epg")

        if not epg_url:
            continue

        print(f"[处理] {ch_name}")

        try:
            root = fetch_epg(epg_url)
        except Exception as e:
            print("  ❌ EPG下载失败:", e)
            continue

        items = root.findall(".//EpgItem")

        written = 0

        for item in items:
            start = item.findtext("EpgStartDateTime")
            end = item.findtext("EpgEndDateTime")

            try:
                start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
                end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
            except:
                continue

            # ❗过滤：昨天之前删除
            if end_dt < yesterday:
                continue

            # 获取标题
            title = get_title(item)

            if not title:
                continue

            prog = ET.SubElement(tv, "programme")
            prog.set("channel", ch_id)
            prog.set("start", to_xmltv_time(start))
            prog.set("stop", to_xmltv_time(end))

            t = ET.SubElement(prog, "title")
            t.text = title

            total_programs += 1
            written += 1

        print(f"  ✔ 写入 {written} 条节目")

    # =========================
    # 输出
    # =========================
    xml_str = pretty_xml(tv)

    with open("epg.xml", "w", encoding="utf-8") as f:
        f.write(xml_str)

    with gzip.open("epg.xml.gz", "wt", encoding="utf-8") as f:
        f.write(xml_str)

    print("\n===== 完成 =====")
    print(f"节目总数: {total_programs}")
    print("输出: epg.xml / epg.xml.gz")


# =========================
# 运行
# =========================
if __name__ == "__main__":
    build_epg()
