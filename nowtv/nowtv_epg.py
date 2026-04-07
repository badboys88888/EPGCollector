#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import gzip
import os
import json
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from datetime import datetime, timezone

# ================= 配置 ================= #
EPG_URL = "https://nowplayer.now.com/tvguide/epglist"

CHANNEL_IDS = [str(i) for i in range(1, 300)]
DAYS = 2
BATCH_SIZE = 10
SLEEP = 0.3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DEBUG_FILE = os.path.join(BASE_DIR, "debug_epg.json")


# ================= 日志 ================= #
def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")


# ================= 读取配置（台标/名称） ================= #
def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            log("⚠️ 未找到 config.json（仅使用ID）")
            return {}
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"❌ config.json错误: {e}")
        return {}


# ================= 时间转换 ================= #
def format_time(ts):
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S +0000")


# ================= 中文优先标题 ================= #
def get_title(p):
    """尝试多种可能的标题字段，优先使用中文"""
    
    # 尝试所有可能的字段
    title_fields = [
        "nameZh", "titleZh", "localizedTitle", "name", "title", 
        "programmeName", "programmeTitle", "displayName", "displayTitle"
    ]
    
    # 先尝试获取中文标题
    for field in title_fields:
        if field in p and p[field]:
            # 如果字段值是字典，尝试获取中文
            if isinstance(p[field], dict):
                if "zh" in p[field]:
                    return p[field]["zh"]
                elif "zh-HK" in p[field]:
                    return p[field]["zh-HK"]
                elif "zh_CN" in p[field]:
                    return p[field]["zh_CN"]
                elif "zh_HK" in p[field]:
                    return p[field]["zh_HK"]
                elif "cn" in p[field]:
                    return p[field]["cn"]
            # 如果直接是字符串
            elif isinstance(p[field], str) and p[field].strip():
                return p[field].strip()
    
    # 如果以上都没有，尝试从title对象中获取
    if "title" in p and isinstance(p["title"], dict):
        for lang in ["zh", "zh-HK", "zh_CN", "zh_HK", "cn"]:
            if lang in p["title"] and p["title"][lang]:
                return p["title"][lang]
    
    # 如果还是没有，返回第一个非空字符串字段
    for key, value in p.items():
        if isinstance(value, str) and value.strip() and key not in ["start", "end", "channelId"]:
            return value.strip()
    
    return ""


# ================= 请求EPG ================= #
def fetch_epg(batch, day):
    params = []
    for cid in batch:
        params.append(("channelIdList[]", cid))
    params.append(("day", str(day)))
    
    # 添加语言参数
    params.append(("locale", "zh_HK"))
    params.append(("lang", "zh"))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Origin": "https://nowplayer.now.com",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }

    try:
        log(f"▶ DAY {day} | 请求 {len(batch)} 个频道")
        r = requests.get(EPG_URL, params=params, headers=headers, timeout=10)

        log(f"   ├─ HTTP {r.status_code}")
        
        # 如果返回非200状态码，记录响应内容
        if r.status_code != 200:
            log(f"   ├─ 响应: {r.text[:200]}")
        
        r.raise_for_status()

        return r.json()

    except Exception as e:
        log(f"❌ 请求失败: {e}")
        return []


# ================= 调试函数 - 检查数据结构 ================= #
def debug_data_structure(data, day, batch):
    """调试：检查返回的数据结构"""
    if not data or not isinstance(data, list):
        log(f"   ⚠️ DAY {day} 返回数据不是列表")
        return
    
    # 保存调试数据
    debug_data = {
        "day": day,
        "batch": batch,
        "data_length": len(data),
        "data_type": str(type(data)),
        "sample_program": None
    }
    
    # 查找一个节目作为样本
    for i, programs in enumerate(data):
        if isinstance(programs, list) and programs:
            debug_data["sample_program"] = programs[0]
            break
    
    # 保存到文件
    debug_file = f"debug_day{day}_batch{'_'.join(batch[:3])}.json"
    with open(os.path.join(BASE_DIR, debug_file), "w", encoding="utf-8") as f:
        json.dump(debug_data, f, ensure_ascii=False, indent=2)
    
    log(f"   ├─ 数据结构已保存到: {debug_file}")
    
    # 打印样本节目信息
    if debug_data["sample_program"]:
        program = debug_data["sample_program"]
        log(f"   ├─ 样本节目键: {list(program.keys())}")
        
        # 检查标题相关字段
        title_fields = [k for k in program.keys() if 'name' in k.lower() or 'title' in k.lower()]
        if title_fields:
            log(f"   ├─ 标题相关字段: {title_fields}")
            for field in title_fields:
                if field in program:
                    log(f"   ├─   {field}: {program[field]}")


# ================= 尝试不同的API接口 ================= #
def try_different_api(batch, day):
    """尝试不同的API接口获取中文EPG"""
    
    # 尝试不同的接口和参数
    api_tests = [
        {
            "url": "https://nowplayer.now.com/tvguide/epglist",
            "params": [("channelIdList[]", cid) for cid in batch] + [("day", str(day)), ("locale", "zh_HK")]
        },
        {
            "url": "https://nowplayer.now.com/tvguide/epglist",
            "params": [("channelIdList[]", cid) for cid in batch] + [("day", str(day)), ("lang", "zh-HK")]
        },
        {
            "url": "https://nowplayer.now.com/tvguide/epg",
            "params": [("channelIds", ",".join(batch)), ("day", str(day)), ("lang", "zh_HK")]
        },
        {
            "url": "https://nowplayer.now.com/api/epg/list",
            "params": {"channelIds": ",".join(batch), "dateOffset": day, "language": "zh"}
        }
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }
    
    for i, test in enumerate(api_tests):
        try:
            log(f"   ├─ 尝试接口 {i+1}: {test['url']}")
            
            if "params" in test and isinstance(test["params"], list):
                response = requests.get(test["url"], params=test["params"], headers=headers, timeout=10)
            else:
                response = requests.get(test["url"], params=test.get("params", {}), headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    log(f"   ├─ 接口 {i+1} 成功")
                    return data
        except Exception as e:
            log(f"   ├─ 接口 {i+1} 失败: {e}")
            continue
    
    return []


# ================= XML美化 ================= #
def prettify_xml(elem):
    rough = ET.tostring(elem, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8")


# ================= 主程序 ================= #
def main():
    log("========== NOWTV EPG START ==========")

    config = load_config()
    all_data = {}

    # ========== 抓取 ==========
    for day in range(DAYS):
        log(f"================ DAY {day} ================")

        for i in range(0, len(CHANNEL_IDS), BATCH_SIZE):
            batch = CHANNEL_IDS[i:i + BATCH_SIZE]

            # 先尝试原始接口
            data = fetch_epg(batch, day)
            
            # 如果数据为空，尝试其他接口
            if not data or (isinstance(data, list) and len(data) == 0):
                log(f"   ⚠️ 原始接口无数据，尝试其他接口...")
                data = try_different_api(batch, day)
            
            if not isinstance(data, list):
                log("⚠️ 数据异常")
                continue
            
            # 调试：检查数据结构
            debug_data_structure(data, day, batch)
            
            # 保存原始数据用于分析
            if day == 0 and i == 0:  # 只保存第一天的第一批数据用于分析
                with open(DEBUG_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                log(f"   ├─ 原始数据已保存到: {DEBUG_FILE}")

            for idx, programs in enumerate(data):
                if idx >= len(batch):
                    continue

                cid = batch[idx]

                if not programs:
                    continue

                if cid not in all_data:
                    all_data[cid] = []

                all_data[cid].extend(programs)

                # 显示第一个节目的标题
                if programs and idx == 0:  # 只显示第一个频道的第一个节目
                    title = get_title(programs[0])
                    log(f"   ✓ 频道 {cid} | 标题: {title[:30]}... | +{len(programs)}")
                else:
                    log(f"   ✓ 频道 {cid} | +{len(programs)}")

            time.sleep(SLEEP)

    # ========== XML生成 ==========
    log("========== XML GENERATE ==========")
    log(f"📊 频道总数: {len(all_data)}")
    
    # 统计节目语言
    chinese_count = 0
    english_count = 0
    for cid, programs in all_data.items():
        for p in programs:
            title = get_title(p)
            # 简单判断是否为中文（包含中文字符）
            if any('\u4e00' <= char <= '\u9fff' for char in title):
                chinese_count += 1
            elif title.strip():  # 非空英文标题
                english_count += 1
    
    log(f"📺 中文节目: {chinese_count}, 英文节目: {english_count}")

    tv = ET.Element("tv")

    for cid, programs in all_data.items():

        meta = config.get(cid, {})
        name = meta.get("name", cid)
        logo = meta.get("logo", "")

        # channel
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = name

        if logo:
            ET.SubElement(ch, "icon", {"src": logo})

        # programme
        for p in programs:
            prog = ET.SubElement(tv, "programme", {
                "channel": cid,
                "start": format_time(p["start"]),
                "stop": format_time(p["end"])
            })

            title = get_title(p)
            ET.SubElement(prog, "title").text = title
            
            # 添加语言属性
            if any('\u4e00' <= char <= '\u9fff' for char in title):
                prog.set("lang", "zh")
            elif title.strip():
                prog.set("lang", "en")

    # ========== 写XML（美化） ==========
    log("🧱 XML格式化中...")

    xml_str = prettify_xml(tv)

    with open(XML_FILE, "wb") as f:
        f.write(xml_str)

    log(f"✅ XML完成: {XML_FILE}")

    # ========== 压缩 ==========
    log("🗜️ 压缩GZ...")

    with open(XML_FILE, "rb") as f_in:
        with gzip.open(GZ_FILE, "wb") as f_out:
            f_out.writelines(f_in)

    log(f"✅ GZ完成: {GZ_FILE}")

    log("========== DONE ==========")


if __name__ == "__main__":
    main()
