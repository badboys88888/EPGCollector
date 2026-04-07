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

# 不再使用固定的CHANNEL_IDS，将从config.json中读取
CHANNEL_IDS = []  # 初始为空，从config.json中获取
DAYS = 2
BATCH_SIZE = 10
SLEEP = 0.3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
VALID_CHANNELS_FILE = os.path.join(BASE_DIR, "valid_channels.json")


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
            config = json.load(f)
        
        # 返回完整的config，包括频道ID和配置信息
        return config
    except Exception as e:
        log(f"❌ config.json错误: {e}")
        return {}


# ================= 从config.json获取频道ID ================= #
def get_channel_ids_from_config():
    """从config.json中提取所有频道ID"""
    config = load_config()
    
    if not config:
        log("⚠️ config.json为空，将使用channel_ids.json")
        return []
    
    # 提取所有频道ID
    channel_ids = list(config.keys())
    log(f"📁 从config.json中读取到 {len(channel_ids)} 个频道")
    
    # 显示前10个频道
    for i, cid in enumerate(channel_ids[:10]):
        channel_name = config[cid].get("name", "未知")
        log(f"   {i+1:2d}. {cid}: {channel_name}")
    
    if len(channel_ids) > 10:
        log(f"  ... 还有 {len(channel_ids)-10} 个频道")
    
    return channel_ids


# ================= 验证频道ID是否有效 ================= #
def validate_channel_ids(channel_ids):
    """验证频道ID是否有效（有EPG数据）"""
    if not channel_ids:
        return []
    
    log("🔍 开始验证频道有效性...")
    
    valid_channels = []
    test_day = 0  # 只测试今天
    
    # 批量验证
    for i in range(0, len(channel_ids), BATCH_SIZE):
        batch = channel_ids[i:i + BATCH_SIZE]
        
        try:
            params = []
            for cid in batch:
                params.append(("channelIdList[]", cid))
            params.append(("day", str(test_day)))
            params.append(("locale", "zh_HK"))
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
            }
            
            response = requests.get(EPG_URL, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list):
                    for idx, programs in enumerate(data):
                        if idx < len(batch) and programs:
                            cid = batch[idx]
                            valid_channels.append(cid)
                            log(f"   ✓ 频道 {cid} 有效")
            
        except Exception as e:
            log(f"❌ 验证失败: {e}")
        
        time.sleep(SLEEP)
    
    return valid_channels


# ================= 从channel_ids.json获取备用频道ID ================= #
def get_channel_ids_from_json():
    """从channel_ids.json获取备用频道ID"""
    channel_ids_file = os.path.join(BASE_DIR, "channel_ids.json")
    
    if os.path.exists(channel_ids_file):
        try:
            with open(channel_ids_file, "r", encoding="utf-8") as f:
                ids = json.load(f)
            
            # 确保ID是字符串格式
            ids = [str(cid) for cid in ids]
            
            log(f"📁 从channel_ids.json中读取到 {len(ids)} 个频道")
            return ids
        except Exception as e:
            log(f"❌ 读取channel_ids.json失败: {e}")
    
    return []


# ================= 确定最终的频道ID列表 ================= #
def determine_channel_ids():
    """确定最终的频道ID列表"""
    
    # 1. 优先从config.json获取
    channel_ids = get_channel_ids_from_config()
    
    if not channel_ids:
        # 2. 如果config.json为空，从channel_ids.json获取
        channel_ids = get_channel_ids_from_json()
    
    if not channel_ids:
        # 3. 如果都没有，使用默认范围
        log("⚠️ 使用默认频道ID范围: 001-300")
        channel_ids = [f"{i:03d}" for i in range(1, 301)]
    
    # 4. 验证并返回有效的频道ID
    valid_ids = validate_channel_ids(channel_ids)
    
    if valid_ids:
        log(f"✅ 验证完成: {len(valid_ids)} 个有效频道")
    else:
        log("⚠️ 验证失败，将使用所有配置的频道ID")
        valid_ids = channel_ids
    
    return valid_ids


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

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }

    try:
        log(f"▶ DAY {day} | 请求 {len(batch)} 个频道: {batch}")
        r = requests.get(EPG_URL, params=params, headers=headers, timeout=10)

        log(f"   ├─ HTTP {r.status_code}")
        r.raise_for_status()

        return r.json()

    except Exception as e:
        log(f"❌ 请求失败: {e}")
        return []


# ================= XML美化 ================= #
def prettify_xml(elem):
    rough = ET.tostring(elem, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8")


# ================= 主程序 ================= #
def main():
    log("========== NOWTV EPG START ==========")

    # 1. 确定频道ID列表
    CHANNEL_IDS = determine_channel_ids()
    
    if not CHANNEL_IDS:
        log("❌ 没有可用的频道ID，程序退出")
        return
    
    # 2. 加载配置
    config = load_config()
    
    # 3. 抓取EPG数据
    all_data = {}
    
    for day in range(DAYS):
        log(f"================ DAY {day} ================")

        for i in range(0, len(CHANNEL_IDS), BATCH_SIZE):
            batch = CHANNEL_IDS[i:i + BATCH_SIZE]

            data = fetch_epg(batch, day)

            if not isinstance(data, list):
                log("⚠️ 数据异常")
                continue

            for idx, programs in enumerate(data):
                if idx >= len(batch):
                    continue

                cid = batch[idx]

                if not programs:
                    # 记录没有节目的频道
                    log(f"   ⚠️ 频道 {cid} 无节目")
                    continue

                if cid not in all_data:
                    all_data[cid] = []

                all_data[cid].extend(programs)

                # 显示频道信息
                title = get_title(programs[0]) if programs else "无标题"
                log(f"   ✓ 频道 {cid} | 标题: {title[:30]}... | +{len(programs)}")

            time.sleep(SLEEP)
            
            # 显示当前进度
            progress = min(i + BATCH_SIZE, len(CHANNEL_IDS))
            log(f"   📈 进度: {progress}/{len(CHANNEL_IDS)} 个频道")

    # 4. 统计信息
    log("========== 统计信息 ==========")
    log(f"📊 配置频道数: {len(CHANNEL_IDS)}")
    log(f"📺 获取到数据的频道数: {len(all_data)}")
    
    if len(all_data) == 0:
        log("❌ 没有获取到任何EPG数据，请检查网络或API")
        return

    # 5. 生成XML
    log("========== XML GENERATE ==========")
    
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
        # 从config获取频道信息
        channel_info = config.get(cid, {})
        name = channel_info.get("name", cid)
        logo = channel_info.get("logo", "")

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

    # 6. 写XML
    log("🧱 XML格式化中...")
    xml_str = prettify_xml(tv)

    with open(XML_FILE, "wb") as f:
        f.write(xml_str)

    log(f"✅ XML完成: {XML_FILE} (大小: {len(xml_str)} 字节)")

    # 7. 压缩
    log("🗜️ 压缩GZ...")
    with open(XML_FILE, "rb") as f_in:
        with gzip.open(GZ_FILE, "wb") as f_out:
            f_out.writelines(f_in)

    log(f"✅ GZ完成: {GZ_FILE}")

    log("========== DONE ==========")


if __name__ == "__main__":
    main()
