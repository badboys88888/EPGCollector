#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import gzip
import re
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

# ===================== 配置 ===================== #
CHANNEL_API_URL = "https://content-api.mytvsuper.com/v1/channel/list"
EPG_API_URL = "https://content-api.mytvsuper.com/v1/epg"

PLATFORM = "web"
COUNTRY_CODE = "ZP"
PROFILE_CLASS = "general"

DAYS_RANGE = 7
MAX_WORKERS = 8
TIMEOUT = 30
RETRY_COUNT = 2
REQUEST_DELAY = 0.3

OUTPUT_JSON = "epg.json"
OUTPUT_XML = "epg.xml"
OUTPUT_GZ = "epg.xml.gz"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.mytvsuper.com/"
}

# ===================== 工具函数 ===================== #
def clean_name(name):
    """清理频道名称"""
    if not name:
        return ""
    patterns = [
        r'\s*\(免費\)', r'\s*\(Free\)',
        r'\s*免費', r'\s*Free',
    ]
    cleaned = name
    for p in patterns:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    return cleaned

def to_xml_time(dt_str):
    """时间格式转换"""
    if not dt_str:
        return ""
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"]:
            try:
                dt = datetime.strptime(dt_str, fmt)
                return dt.strftime("%Y%m%d%H%M%S") + " +0800"
            except:
                continue
        return ""
    except:
        return ""

# ===================== 频道获取 ===================== #
def get_channels():
    """获取频道信息，包含名称清理"""
    params = {
        "platform": PLATFORM,
        "country_code": COUNTRY_CODE,
        "profile_class": PROFILE_CLASS
    }
    
    r = requests.get(CHANNEL_API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
    data = r.json()
    channels = data.get("channels", [])
    
    result = {}
    for c in channels:
        code = c.get("network_code")
        if not code:
            continue
        
        # 清理名称
        name_tc = clean_name(c.get("name_tc", ""))
        name_en = clean_name(c.get("name_en", ""))
        
        result[code] = {
            "name_tc": name_tc or code,
            "name_en": name_en or name_tc or code,
            "icon": c.get("landscape_poster") or c.get("portrait_poster") or ""
        }
    
    print(f"[INFO] 获取到 {len(result)} 个频道")
    return result

# ===================== 智能节目探测器 ===================== #
def extract_programmes(data, max_depth=5):
    """
    从任意结构中提取节目
    不依赖固定字段路径
    """
    if not data:
        return []
    
    # 如果本身就是列表，递归处理每个元素
    if isinstance(data, list):
        programmes = []
        for item in data:
            programmes.extend(extract_programmes(item, max_depth-1))
        return programmes
    
    # 如果是字典
    if isinstance(data, dict):
        # 检查是否已经是节目（包含start_datetime）
        if "start_datetime" in data and data["start_datetime"]:
            return [data]
        
        # 检查常见节目列表字段
        for key in ["epg", "programmes", "items", "list"]:
            if key in data and isinstance(data[key], list):
                programmes = []
                for item in data[key]:
                    programmes.extend(extract_programmes(item, max_depth-1))
                return programmes
        
        # 递归查找
        if max_depth > 0:
            programmes = []
            for value in data.values():
                programmes.extend(extract_programmes(value, max_depth-1))
            return programmes
    
    return []

# ===================== EPG获取 ===================== #
def fetch_epg_with_retry(code, start_date, end_date):
    """带重试的EPG获取"""
    for attempt in range(RETRY_COUNT + 1):
        try:
            time.sleep(REQUEST_DELAY)
            params = {
                "platform": PLATFORM,
                "country_code": COUNTRY_CODE,
                "network_code": code,
                "from": start_date,
                "to": end_date
            }
            r = requests.get(EPG_API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return code, r.json()
        except Exception as e:
            if attempt < RETRY_COUNT:
                time.sleep(1)  # 等待1秒后重试
            else:
                print(f"[WARN] 频道 {code} 获取失败: {e}")
                return code, None
    return code, None

def fetch_all_epg(channels, start_date, end_date):
    """并发获取所有频道的EPG"""
    epg_data = {}
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_epg_with_retry, code, start_date, end_date): code 
                  for code in channels}
        
        for i, future in enumerate(as_completed(futures), 1):
            code, data = future.result()
            epg_data[code] = data
            
            if i % 20 == 0 or i == len(channels):
                print(f"[INFO] 进度: {i}/{len(channels)}")
    
    success = sum(1 for d in epg_data.values() if d is not None)
    print(f"[OK] EPG获取完成: {success}/{len(channels)} 成功")
    return epg_data

# ===================== XML构建 ===================== #
def build_xml(channels, epg_data):
    """构建完整的XMLTV"""
    tv = ET.Element("tv")
    tv.set("generator-info-name", "myTV SUPER EPG")
    
    # 1. 添加频道
    for code, info in channels.items():
        ch = ET.SubElement(tv, "channel", id=code)
        
        # 中文名称
        if info["name_tc"]:
            dn_zh = ET.SubElement(ch, "display-name")
            dn_zh.set("lang", "zh")
            dn_zh.text = info["name_tc"]
        
        # 英文名称
        if info["name_en"]:
            dn_en = ET.SubElement(ch, "display-name")
            dn_en.set("lang", "en")
            dn_en.text = info["name_en"]
        
        # 图标
        if info["icon"]:
            ET.SubElement(ch, "icon", src=info["icon"])
    
    # 2. 添加节目
    total_programmes = 0
    
    for code, data in epg_data.items():
        if not data or code not in channels:
            continue
        
        # 使用智能探测器提取节目
        programmes = extract_programmes(data)
        
        if not programmes:
            continue
        
        # 按时间排序
        programmes.sort(key=lambda x: x.get("start_datetime", ""))
        
        for prog in programmes:
            start_time = prog.get("start_datetime")
            if not start_time:
                continue
            
            xml_time = to_xml_time(start_time)
            if not xml_time:
                continue
            
            # 创建节目元素
            prog_elem = ET.SubElement(tv, "programme", {
                "channel": code,
                "start": xml_time
            })
            
            # 中文标题
            title_tc = prog.get("programme_title_tc", "")
            if title_tc:
                title_zh = ET.SubElement(prog_elem, "title")
                title_zh.set("lang", "zh")
                title_zh.text = title_tc[:200]
            
            # 英文标题
            title_en = prog.get("programme_title_en", "")
            if title_en and title_en != title_tc:
                title_en_elem = ET.SubElement(prog_elem, "title")
                title_en_elem.set("lang", "en")
                title_en_elem.text = title_en[:200]
            
            # 描述
            desc = prog.get("episode_synopsis_tc", "")
            if desc:
                desc_elem = ET.SubElement(prog_elem, "desc")
                desc_elem.set("lang", "zh")
                desc_elem.text = desc[:300]
            
            total_programmes += 1
    
    print(f"[OK] 总节目数: {total_programmes}")
    return tv

# ===================== 输出 ===================== #
def save_files(xml_tree):
    """保存XML和GZ文件"""
    # 美化XML
    xml_str = ET.tostring(xml_tree, encoding="utf-8")
    pretty_xml = minidom.parseString(xml_str).toprettyxml(encoding="utf-8")
    
    # 保存XML
    with open(OUTPUT_XML, "wb") as f:
        f.write(pretty_xml)
    print(f"[OK] XML保存: {OUTPUT_XML}")
    
    # 保存GZ
    with gzip.open(OUTPUT_GZ, "wb") as f:
        f.write(pretty_xml)
    print(f"[OK] GZ保存: {OUTPUT_GZ}")

def save_json(data):
    """保存JSON文件"""
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON保存: {OUTPUT_JSON}")

# ===================== 主程序 ===================== #
def main():
    print("[INFO] 开始获取 myTV SUPER EPG...")
    start_time = datetime.now()
    
    # 获取日期范围
    s = datetime.now().strftime("%Y%m%d")
    e = (datetime.now() + timedelta(days=DAYS_RANGE)).strftime("%Y%m%d")
    print(f"[INFO] 日期: {s} 到 {e}")
    
    # 1. 获取频道
    channels = get_channels()
    if not channels:
        print("[ERROR] 无频道信息")
        return
    
    # 2. 获取EPG
    epg_data = fetch_all_epg(channels.keys(), s, e)
    
    # 3. 保存JSON
    save_json(epg_data)
    
    # 4. 构建XML
    xml_tree = build_xml(channels, epg_data)
    
    # 5. 保存文件
    save_files(xml_tree)
    
    # 6. 统计
    duration = (datetime.now() - start_time).total_seconds()
    print(f"[OK] 完成! 耗时: {duration:.1f}秒")

if __name__ == "__main__":
    main()