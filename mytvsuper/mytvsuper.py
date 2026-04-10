#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import gzip
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import os

# ===================== 配置常量 ===================== #

CHANNEL_API_URL = "https://content-api.mytvsuper.com/v1/channel/list"
EPG_API_URL = "https://content-api.mytvsuper.com/v1/epg"

# API参数
PLATFORM = "web"
COUNTRY_CODE = "ZP"
PROFILE_CLASS = "general"

# 运行参数
DAYS_RANGE = 7
MAX_WORKERS = 12
TIMEOUT = 30
REQUEST_DELAY = 0.5  # 请求间隔，避免请求过快

# 输出文件
OUTPUT_JSON = "epg.json"
OUTPUT_XML = "epg.xml"
OUTPUT_GZ = "epg.xml.gz"

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.mytvsuper.com/",
}

# ===================== 辅助函数 ===================== #

def print_step(text: str) -> None:
    """打印步骤标题"""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")

def print_info(text: str) -> None:
    """打印信息"""
    print(f"📌 {text}")

def print_success(text: str) -> None:
    """打印成功信息"""
    print(f"✅ {text}")

def print_warning(text: str) -> None:
    """打印警告信息"""
    print(f"⚠️  {text}")

def print_error(text: str) -> None:
    """打印错误信息"""
    print(f"❌ {text}")

# ===================== 日期处理函数 ===================== #

def get_date_range(days: int = DAYS_RANGE) -> tuple:
    """获取日期范围，格式：YYYYMMDD"""
    now = datetime.now()
    start_date = now.strftime("%Y%m%d")
    end_date = (now + timedelta(days=days)).strftime("%Y%m%d")
    return start_date, end_date

# ===================== 1. 获取频道详细信息 ===================== #

def get_channels() -> dict:
    """
    获取所有频道的详细信息，包括名称和图标
    
    Returns:
        字典格式：{network_code: {"name_tc": "...", "name_en": "...", "icon": "..."}, ...}
    """
    print("📡 正在获取频道详细信息...")
    
    params = {
        "platform": PLATFORM,
        "country_code": COUNTRY_CODE,
        "profile_class": PROFILE_CLASS
    }
    
    try:
        response = requests.get(
            CHANNEL_API_URL, 
            params=params, 
            headers=HEADERS, 
            timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print_error(f"获取频道列表失败: {e}")
        return {}
    
    # 提取频道列表
    channels = data.get("channels") or []
    
    channel_info = {}
    
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        
        network_code = channel.get("network_code")
        if not network_code:
            continue
        
        # 获取频道名称
        name_tc = channel.get("name_tc", "")
        name_en = channel.get("name_en", "")
        
        # 获取图标URL - 优先使用横版海报，其次竖版海报
        icon_url = ""
        if channel.get("landscape_poster"):
            icon_url = channel["landscape_poster"]
        elif channel.get("portrait_poster"):
            icon_url = channel["portrait_poster"]
        
        # 存储频道信息
        channel_info[network_code] = {
            "name_tc": name_tc,
            "name_en": name_en,
            "icon": icon_url,
            "channel_no": channel.get("channel_no", 0)
        }
    
    print_success(f"成功获取 {len(channel_info)} 个频道的详细信息")
    
    # 显示前几个频道的示例
    print_info("频道示例 (前5个):")
    for i, (code, info) in enumerate(list(channel_info.items())[:5], 1):
        has_icon = "✅" if info["icon"] else "❌"
        print(f"  {i}. {info['name_tc'][:20]:20s} (ID: {code}) 图标: {has_icon}")
    
    if len(channel_info) > 5:
        print(f"  ... 等 {len(channel_info) - 5} 个频道")
    
    return channel_info

# ===================== 2. 获取单个频道的EPG ===================== #

def fetch_epg(network_code: str, from_date: str, to_date: str) -> tuple:
    """获取指定频道的EPG数据"""
    import time
    time.sleep(REQUEST_DELAY)  # 添加请求间隔
    
    params = {
        "platform": PLATFORM,
        "country_code": COUNTRY_CODE,
        "network_code": network_code,
        "from": from_date,
        "to": to_date
    }
    
    try:
        response = requests.get(
            EPG_API_URL, 
            params=params, 
            headers=HEADERS, 
            timeout=TIMEOUT
        )
        response.raise_for_status()
        return network_code, response.json()
    except Exception as e:
        print_warning(f"频道 {network_code} 获取EPG失败: {e}")
        return network_code, None

# ===================== 3. JSON输出 ===================== #

def save_json(data: dict, filename: str = OUTPUT_JSON) -> None:
    """保存JSON数据到文件"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    file_size = os.path.getsize(filename) / 1024  # KB
    print_success(f"JSON文件已保存: {filename} ({file_size:.1f} KB)")

# ===================== 4. XML时间格式转换 ===================== #

def format_time(dt_str: str) -> str:
    """将时间字符串转换为XMLTV格式"""
    if not dt_str:
        return ""
    
    try:
        # 尝试多种常见时间格式
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"]:
            try:
                dt = datetime.strptime(dt_str, fmt)
                return dt.strftime("%Y%m%d%H%M%S") + " +0800"
            except ValueError:
                continue
        
        return ""
    except Exception:
        return ""

# ===================== 5. JSON → XMLTV转换（带频道图标） ===================== #

def build_xml(channel_info: dict, epg_data: dict) -> ET.Element:
    """
    将JSON数据转换为XMLTV格式，包含频道图标
    
    Args:
        channel_info: 频道详细信息字典
        epg_data: EPG数据字典
        
    Returns:
        XML根元素
    """
    # 创建根元素
    tv = ET.Element("tv")
    tv.set("source-info-url", "https://www.mytvsuper.com")
    tv.set("source-info-name", "myTV SUPER")
    tv.set("generator-info-name", "myTV SUPER EPG Grabber")
    tv.set("generator-info-url", "")
    
    # 1. 添加所有频道信息
    for channel_code, info in channel_info.items():
        ch = ET.SubElement(tv, "channel", id=channel_code)
        
        # 添加中文显示名称
        if info["name_tc"]:
            dn_tc = ET.SubElement(ch, "display-name")
            dn_tc.set("lang", "zh")
            dn_tc.text = info["name_tc"]
        
        # 添加英文显示名称
        if info["name_en"]:
            dn_en = ET.SubElement(ch, "display-name")
            dn_en.set("lang", "en")
            dn_en.text = info["name_en"]
        
        # 添加频道图标
        if info["icon"]:
            icon_elem = ET.SubElement(ch, "icon")
            icon_elem.set("src", info["icon"])
    
    # 2. 添加节目信息
    for channel_code, blocks in epg_data.items():
        if not blocks or channel_code not in channel_info:
            continue
        
        # 处理可能的数据结构
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                
                # 尝试提取节目列表
                items = block.get("item", [])
                for day in items:
                    if not isinstance(day, dict):
                        continue
                    
                    epg_list = day.get("epg", [])
                    for epg in epg_list:
                        if not isinstance(epg, dict):
                            continue
                        
                        _add_programme(tv, channel_code, epg)
        
        # 如果blocks是字典而不是列表
        elif isinstance(blocks, dict):
            # 尝试从字典中提取节目列表
            for key, value in blocks.items():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "epg" in item:
                            for epg in item.get("epg", []):
                                _add_programme(tv, channel_code, epg)
    
    return tv

def _add_programme(tv: ET.Element, channel_code: str, epg: dict) -> None:
    """辅助函数：添加单个节目到XML"""
    start_time = epg.get("start_datetime")
    if not start_time:
        return
    
    formatted_time = format_time(start_time)
    if not formatted_time:
        return
    
    # 创建节目元素
    prog = ET.SubElement(tv, "programme", {
        "channel": channel_code,
        "start": formatted_time
    })
    
    # 中文标题
    title_tc = epg.get("programme_title_tc", "")
    if title_tc:
        title = ET.SubElement(prog, "title", lang="zh")
        title.text = title_tc[:200]  # 限制长度
    
    # 英文标题
    title_en = epg.get("programme_title_en", "")
    if title_en:
        title_en_elem = ET.SubElement(prog, "title", lang="en")
        title_en_elem.text = title_en[:200]  # 限制长度
    
    # 节目描述
    desc = epg.get("episode_synopsis_tc", "")
    if desc:
        desc_elem = ET.SubElement(prog, "desc", lang="zh")
        desc_elem.text = desc[:300]  # 限制长度

# ===================== 6. 美化并保存XML ===================== #

def save_xml_pretty(tree: ET.Element, filename: str = OUTPUT_XML) -> None:
    """保存格式化的XML文件"""
    # 转换为字符串
    xml_str = ET.tostring(tree, encoding="utf-8")
    
    # 使用minidom美化输出
    parsed = minidom.parseString(xml_str)
    pretty_xml = parsed.toprettyxml(indent="  ", encoding="utf-8")
    
    # 写入文件
    with open(filename, "wb") as f:
        f.write(pretty_xml)
    
    file_size = len(pretty_xml) / 1024  # KB
    print_success(f"XML文件已保存: {filename} ({file_size:.1f} KB)")

# ===================== 7. 压缩为GZ ===================== #

def save_gz(xml_filename: str = OUTPUT_XML, gz_filename: str = OUTPUT_GZ) -> None:
    """将XML文件压缩为GZ格式"""
    with open(xml_filename, "rb") as f_in:
        with gzip.open(gz_filename, "wb") as f_out:
            f_out.write(f_in.read())
    
    file_size = os.path.getsize(gz_filename) / 1024  # KB
    print_success(f"GZ文件已保存: {gz_filename} ({file_size:.1f} KB)")

# ===================== 主流程 ===================== #

def main():
    """主函数"""
    print_step("myTV SUPER EPG 抓取工具 (增强版)")
    print_info(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # 获取日期范围
        from_date, to_date = get_date_range(DAYS_RANGE)
        print_info(f"📅 EPG日期范围: {from_date} 至 {to_date} ({DAYS_RANGE}天)")
        
        # 1. 获取频道详细信息（包含图标）
        channel_info = get_channels()
        if not channel_info:
            print_error("无法获取频道信息，程序退出")
            return
        
        channel_codes = list(channel_info.keys())
        
        # 2. 并行获取所有频道的EPG
        print_step("获取EPG数据")
        print_info(f"使用 {MAX_WORKERS} 个线程并行获取 {len(channel_codes)} 个频道的EPG")
        
        results = {}
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            # 提交所有任务
            futures = {ex.submit(fetch_epg, c, from_date, to_date): c for c in channel_codes}
            
            # 处理完成的任务
            completed = 0
            for future in as_completed(futures):
                code, data = future.result()
                if data:
                    results[code] = data
                
                completed += 1
                if completed % 20 == 0 or completed == len(channel_codes):
                    print(f"  [{completed:3d}/{len(channel_codes)}] 已获取 {completed} 个频道")
        
        successful = len([v for v in results.values() if v is not None])
        print_success(f"EPG获取完成: 成功 {successful}/{len(channel_codes)} 个频道")
        
        if not results:
            print_error("未获取到任何EPG数据，程序退出")
            return
        
        # 3. 保存JSON文件
        print_step("保存数据文件")
        save_json(results, OUTPUT_JSON)
        
        # 4. 转换为XML并保存（包含频道图标）
        print_info("正在转换为XMLTV格式...")
        xml_tree = build_xml(channel_info, results)
        save_xml_pretty(xml_tree, OUTPUT_XML)
        
        # 5. 压缩为GZ文件
        save_gz(OUTPUT_XML, OUTPUT_GZ)
        
        # 6. 输出统计信息
        print_step("任务完成")
        print_info(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 计算统计信息
        channels_with_icon = sum(1 for info in channel_info.values() if info.get("icon"))
        
        print_info("统计信息:")
        print(f"  • 频道总数: {len(channel_info)}")
        print(f"  • 有图标的频道: {channels_with_icon}")
        print(f"  • EPG获取成功率: {successful}/{len(channel_codes)}")
        print(f"  • 输出文件:")
        print(f"      - {OUTPUT_JSON}")
        print(f"      - {OUTPUT_XML}")
        print(f"      - {OUTPUT_GZ}")
        
    except KeyboardInterrupt:
        print("\n\n❌ 用户中断程序")
        return
    except Exception as e:
        print_error(f"程序运行异常: {e}")
        import traceback
        traceback.print_exc()
        return

# ===================== 程序入口 ===================== #

if __name__ == "__main__":
    main()
