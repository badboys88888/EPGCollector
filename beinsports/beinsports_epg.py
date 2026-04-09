#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beIN SPORTS 专业EPG生成器 (修复版)
使用接口原始时区，不进行时区转换
"""

import requests
import json
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from datetime import datetime, timedelta, timezone
import time
import re
import os
import sys
import traceback
from typing import Dict, List, Optional, Set
from collections import defaultdict

# ================= 配置区域 ================= #
CONFIG_FILE = "config.json"            # 频道配置文件
OUTPUT_XML = "beinsports_epg.xml"      # 输出XML文件
OUTPUT_GZ = "beinsports_epg.xml.gz"    # 压缩版本
ERROR_LOG = "epg_errors.log"           # 错误日志

# EPG时间范围（天）
DAYS_BACK = 1                          # 获取过去1天的节目
DAYS_FORWARD = 7                       # 获取未来7天的节目

# API配置
BASE_API = "https://www.beinsports.com/api/opta"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
REQUEST_DELAY = 0.5                    # 请求延迟（秒）

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,es;q=0.7,ar;q=0.6",
    "Referer": "https://www.beinsports.com/",
    "Origin": "https://www.beinsports.com",
}

# ================= 日志系统 ================= #
def log(msg, error=False):
    """统一的日志输出函数"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    print(log_msg)
    
    if error:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")
            traceback.print_exc(file=f)

# ================= 工具函数 ================= #
def iso_format(dt: datetime) -> str:
    """转换为ISO 8601格式（API要求）"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def xmltv_time(dt: datetime) -> str:
    """转换为XMLTV时间格式（使用原始时区，不进行转换）"""
    # 如果datetime有时区信息，保留它
    if dt.tzinfo is not None:
        # 提取时区偏移
        offset = dt.utcoffset()
        if offset is not None:
            offset_seconds = offset.total_seconds()
            offset_hours = int(offset_seconds // 3600)
            offset_minutes = int((offset_seconds % 3600) // 60)
            offset_str = f"{offset_hours:+03d}{offset_minutes:02d}"
        else:
            offset_str = "+0000"
    else:
        # 如果没有时区信息，假设是UTC
        offset_str = "+0000"
    
    # 格式化为XMLTV时间格式
    time_str = dt.strftime("%Y%m%d%H%M%S")
    return f"{time_str} {offset_str}"

def parse_iso_time(iso_str: str) -> Optional[datetime]:
    """解析ISO时间字符串，保留时区信息"""
    try:
        # 处理ISO格式时间
        if iso_str.endswith('Z'):
            # UTC时间
            dt = datetime.fromisoformat(iso_str[:-1] + '+00:00')
        elif 'T' in iso_str and '+' in iso_str:
            # 包含时区偏移的时间
            dt = datetime.fromisoformat(iso_str)
        elif 'T' in iso_str and '-' in iso_str[-6:]:
            # 包含时区偏移的时间
            dt = datetime.fromisoformat(iso_str)
        else:
            # 没有明确时区，假设是UTC
            dt = datetime.fromisoformat(iso_str)
            dt = dt.replace(tzinfo=timezone.utc)
        
        return dt
    except Exception as e:
        log(f"时间解析失败: {iso_str} - {e}")
        return None

def normalize_string(text: str) -> str:
    """规范化字符串，移除控制字符"""
    if not text:
        return ""
    return ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')

# ================= 核心功能：频道ID生成 ================= #
def generate_stable_xml_id(channel_data: Dict) -> str:
    """
    生成稳定、唯一的XML频道ID
    使用名称缩写 + 原始ID前缀，确保唯一性
    示例：beinsports1_6626, bein4k_67DD
    """
    channel_id = channel_data.get("id", "")
    name = channel_data.get("name", "").lower()
    
    if not channel_id or not name:
        return "unknown"
    
    # 提取原始ID的前4-6个字符（确保唯一性）
    id_prefix = channel_id[:6] if len(channel_id) >= 6 else channel_id
    id_prefix = id_prefix.upper()
    
    # 基于频道名称生成基础标识
    if "4k" in name:
        base_id = "bein4k"
    elif "español" in name or "esp" in name:
        base_id = "beinespanol"
    elif "français" in name or "fr " in name:
        base_id = "beinfr"
    elif "english" in name or "en " in name:
        base_id = "beinen"
    elif "xtra" in name and "ñ" in name:
        base_id = "beinextra_es"
    elif "xtra" in name:
        base_id = "beinextra"
    elif "max" in name:
        # 提取MAX后面的数字
        max_match = re.search(r'max\s*(\d+)', name)
        if max_match:
            base_id = f"beinmax{max_match.group(1)}"
        else:
            base_id = "beinmax"
    elif "bein sports" in name:
        # 提取主频道数字
        num_match = re.search(r'bein sports\s*(\d+)', name)
        if num_match:
            base_id = f"beinsports{num_match.group(1)}"
        else:
            base_id = "beinsports"
    else:
        # 清理名称生成基础ID
        clean_name = re.sub(r'[^a-z0-9]', '_', name)
        # 取前15个字符
        base_id = clean_name[:15].strip('_')
        if not base_id:
            base_id = "bein"
    
    # 返回组合ID
    return f"{base_id}_{id_prefix}"

# ================= 配置文件处理 ================= #
def load_config(config_file: str) -> Optional[Dict]:
    """加载配置文件，支持多种格式"""
    log(f"加载配置文件: {config_file}")
    
    if not os.path.exists(config_file):
        log(f"❌ 配置文件不存在: {config_file}", True)
        return None
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # 处理不同的配置文件格式
        if isinstance(config, list):
            log(f"✅ 配置文件加载成功（列表格式，{len(config)} 个频道）")
            # 转换为标准字典格式
            config = {
                "generated_at": datetime.now().isoformat(),
                "total_channels": len(config),
                "channels": config
            }
        elif isinstance(config, dict) and "channels" in config:
            log(f"✅ 配置文件加载成功（字典格式，{len(config['channels'])} 个频道）")
        elif isinstance(config, dict):
            log(f"✅ 配置文件加载成功（字典格式）")
            # 检查是否直接是频道列表
            if any(key in config for key in ["id", "name", "icon"]):
                # 这看起来是一个频道对象，不是列表
                config = {
                    "generated_at": datetime.now().isoformat(),
                    "total_channels": 1,
                    "channels": [config]
                }
            elif "channels" not in config:
                config["channels"] = []
                config["total_channels"] = 0
        else:
            log(f"❌ 未知的配置文件格式: {type(config)}", True)
            return None
        
        # 确保channels是列表
        if "channels" not in config:
            config["channels"] = []
        
        # 计算实际频道数量
        actual_channels = len(config.get("channels", []))
        config["total_channels"] = actual_channels
        
        log(f"   生成时间: {config.get('generated_at', '未知')}")
        log(f"   频道总数: {actual_channels}")
        
        return config
        
    except Exception as e:
        log(f"❌ 读取配置文件失败: {e}", True)
        return None

def extract_channel_info(channel_data: Dict) -> Optional[Dict]:
    """从原始频道数据中提取规范化信息"""
    # 基本信息
    channel_id = channel_data.get("id", "")
    name = channel_data.get("name", "")
    
    if not channel_id or not name:
        return None
    
    # 图标提取（优先使用icon，其次logo）
    icon = channel_data.get("icon") or channel_data.get("logo")
    
    # 显示名称
    display_name = channel_data.get("display_name") or name
    
    # 检测语言
    name_lower = name.lower()
    if "español" in name_lower or "ñ" in name_lower:
        language = "es"
    elif "français" in name_lower or "france" in str(channel_data.get("country", "")).lower():
        language = "fr"
    elif "english" in name_lower or "en" in str(channel_data.get("region", "")).lower():
        language = "en"
    elif "arabic" in name_lower or "ar" in str(channel_data.get("region", "")).lower():
        language = "ar"
    else:
        language = "en"  # 默认英语
    
    # 生成XML ID
    xml_id = generate_stable_xml_id(channel_data)
    
    # 构建频道信息
    return {
        "id": channel_id,
        "xml_id": xml_id,
        "name": name,
        "display_name": display_name,
        "icon": icon,
        "language": language,
        "country": channel_data.get("country"),
        "region": channel_data.get("region", "unknown"),
        "provider": channel_data.get("provider", "unknown"),
        "external_id": channel_data.get("external_id"),
    }

def process_channels(config: Dict) -> tuple:
    """处理所有频道数据，返回频道列表和映射表"""
    if not config or "channels" not in config:
        log("❌ 配置文件中没有频道数据", True)
        return [], {}, {}
    
    raw_channels = config["channels"]
    log(f"处理 {len(raw_channels)} 个频道")
    
    processed_channels = []
    id_to_channel = {}
    xml_id_to_channel = {}
    
    for idx, raw_channel in enumerate(raw_channels):
        if not isinstance(raw_channel, dict):
            continue
        
        channel_info = extract_channel_info(raw_channel)
        if not channel_info:
            continue
        
        processed_channels.append(channel_info)
        id_to_channel[channel_info["id"]] = channel_info
        xml_id_to_channel[channel_info["xml_id"]] = channel_info
    
    log(f"✅ 处理完成: {len(processed_channels)} 个频道")
    
    # 按显示名称排序
    processed_channels.sort(key=lambda x: x["display_name"].lower())
    
    return processed_channels, id_to_channel, xml_id_to_channel

# ================= API请求处理 ================= #
def make_api_request(url: str, params: List[tuple]) -> Optional[Dict]:
    """发送API请求，带重试机制"""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                log(f"HTTP {response.status_code}: {url}")
                
        except requests.exceptions.Timeout:
            log(f"请求超时 (尝试 {attempt + 1}/{MAX_RETRIES}): {url}")
        except Exception as e:
            log(f"请求异常: {e}")
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(REQUEST_DELAY * (attempt + 1))
    
    return None

def fetch_epg_data(channel_ids: List[str], start_time: datetime, end_time: datetime) -> List[Dict]:
    """获取EPG数据"""
    if not channel_ids:
        return []
    
    url = f"{BASE_API}/tv-event"
    
    params = [
        ("searchKey", ""),
        ("startBefore", iso_format(end_time)),
        ("endAfter", iso_format(start_time)),
        ("limit", "5000"),
    ]
    
    # 添加频道ID
    for channel_id in channel_ids:
        params.append(("channelIds", channel_id))
    
    log(f"获取EPG数据: {len(channel_ids)} 个频道")
    log(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M')} 到 {end_time.strftime('%Y-%m-%d %H:%M')}")
    
    data = make_api_request(url, params)
    if not data:
        log("获取EPG数据失败")
        return []
    
    # 提取节目数据
    programs = []
    if isinstance(data, list):
        programs = data
    elif isinstance(data, dict):
        for key in ["rows", "data", "items", "events"]:
            if key in data and isinstance(data[key], list):
                programs = data[key]
                break
    
    log(f"✅ 获取到 {len(programs)} 个节目")
    return programs

# ================= XML生成 ================= #
def create_xml_root() -> ET.Element:
    """创建XML根元素"""
    tv = ET.Element("tv")
    
    # 添加源信息
    tv.set("source-info-name", "beIN SPORTS")
    tv.set("source-info-url", "https://www.beinsports.com")
    tv.set("generator-info-name", "beIN-SPORTS-EPG-Generator-Pro")
    tv.set("generator-info-url", "")
    # 使用UTC时间
    tv.set("date", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000"))
    
    return tv

def add_channel_definitions(tv_element: ET.Element, channels: List[Dict]) -> None:
    """添加频道定义到XML"""
    log("添加频道定义到XML...")
    
    for channel in channels:
        # 创建channel元素
        channel_elem = ET.SubElement(tv_element, "channel", id=channel["xml_id"])
        
        # 显示名称（带语言标签）
        display_name = ET.SubElement(channel_elem, "display-name", lang=channel["language"])
        display_name.text = channel["display_name"]
        
        # 原始名称（备用）
        if channel["name"] != channel["display_name"]:
            alt_name = ET.SubElement(channel_elem, "display-name", lang=channel["language"])
            alt_name.text = channel["name"]
        
        # 图标（如果有）
        if channel.get("icon"):
            icon_elem = ET.SubElement(channel_elem, "icon", src=channel["icon"])
        
        # 来源URL
        url_elem = ET.SubElement(channel_elem, "url")
        url_elem.text = f"beinsports:{channel['id']}"
        
        # 地区信息
        if channel.get("region"):
            ET.SubElement(channel_elem, "region").text = channel["region"]
        
        # 提供商信息
        if channel.get("provider"):
            ET.SubElement(channel_elem, "provider").text = channel["provider"]
        
        # 国家信息
        if channel.get("country"):
            ET.SubElement(channel_elem, "country").text = channel["country"]
        
        # 外部ID
        if channel.get("external_id"):
            ET.SubElement(channel_elem, "external-id").text = str(channel["external_id"])
    
    log(f"✅ 添加了 {len(channels)} 个频道定义")

def parse_program_data(program: Dict, id_to_channel: Dict) -> Optional[Dict]:
    """解析单个节目数据"""
    try:
        channel_id = program.get("channelId")
        if not channel_id or channel_id not in id_to_channel:
            return None
        
        channel_info = id_to_channel[channel_id]
        
        # 提取时间
        start_time = program.get("startDate")
        end_time = program.get("endDate")
        if not start_time or not end_time:
            return None
        
        # 解析时间，保留原始时区
        start_dt = parse_iso_time(start_time)
        end_dt = parse_iso_time(end_time)
        if not start_dt or not end_dt:
            return None
        
        # 提取标题
        title = (
            program.get("title") or
            program.get("competitionName") or
            program.get("sport") or
            program.get("name") or
            "Sports Event"
        )
        
        # 提取描述
        description = program.get("description") or ""
        
        # 提取队伍信息
        home_team = program.get("homeTeamName") or program.get("homeTeam")
        away_team = program.get("awayTeamName") or program.get("awayTeam")
        
        # 提取运动类型
        sport = program.get("sport") or ""
        
        # 构建节目信息
        program_info = {
            "xml_channel_id": channel_info["xml_id"],
            "channel_language": channel_info["language"],
            "channel_name": channel_info["display_name"],
            "title": normalize_string(title),
            "description": normalize_string(description),
            "start_time": start_dt,
            "end_time": end_dt,
            "home_team": normalize_string(home_team) if home_team else None,
            "away_team": normalize_string(away_team) if away_team else None,
            "sport": normalize_string(sport),
            "competition": normalize_string(program.get("competitionName") or ""),
            "season": program.get("season"),
            "round": program.get("round"),
            "episode_num": program.get("episodeNumber"),
            "source_id": channel_id,
            "program_id": program.get("id"),
        }
        
        return program_info
        
    except Exception as e:
        log(f"解析节目数据失败: {e}")
        return None

def add_programmes(tv_element: ET.Element, programs: List[Dict], id_to_channel: Dict) -> int:
    """添加节目数据到XML"""
    log("添加节目数据到XML...")
    
    added_count = 0
    seen_programs = set()  # 用于去重
    
    for program in programs:
        program_info = parse_program_data(program, id_to_channel)
        if not program_info:
            continue
        
        # 生成唯一标识符（用于去重）
        program_key = (
            program_info["xml_channel_id"],
            program_info["start_time"].isoformat(),
            program_info["end_time"].isoformat(),
            program_info["title"]
        )
        
        if program_key in seen_programs:
            continue
        seen_programs.add(program_key)
        
        # 创建programme元素
        prog_elem = ET.SubElement(tv_element, "programme", {
            "channel": program_info["xml_channel_id"],
            "start": xmltv_time(program_info["start_time"]),
            "stop": xmltv_time(program_info["end_time"])
        })
        
        # 来源信息
        prog_elem.set("source-id", program_info["source_id"])
        if program_info.get("program_id"):
            prog_elem.set("program-id", program_info["program_id"])
        
        # 标题（带语言标签）
        title_elem = ET.SubElement(prog_elem, "title", lang=program_info["channel_language"])
        title_elem.text = program_info["title"]
        
        # 副标题（如果有队伍信息）
        if program_info["home_team"] and program_info["away_team"]:
            sub_title = f"{program_info['home_team']} vs {program_info['away_team']}"
            sub_elem = ET.SubElement(prog_elem, "sub-title", lang=program_info["channel_language"])
            sub_elem.text = sub_title
        
        # 描述
        if program_info["description"]:
            desc_elem = ET.SubElement(prog_elem, "desc", lang=program_info["channel_language"])
            desc_elem.text = program_info["description"]
        
        # 分类（总是体育）
        category_elem = ET.SubElement(prog_elem, "category", lang=program_info["channel_language"])
        category_elem.text = "Sports"
        
        # 具体运动类型
        if program_info["sport"]:
            sport_elem = ET.SubElement(prog_elem, "category", lang=program_info["channel_language"])
            sport_elem.text = program_info["sport"]
        
        # 比赛信息
        if program_info["competition"]:
            competition_elem = ET.SubElement(prog_elem, "category", lang=program_info["channel_language"])
            competition_elem.text = program_info["competition"]
        
        # 剧集号（如果有）
        if program_info.get("episode_num"):
            episode_elem = ET.SubElement(prog_elem, "episode-num", system="onscreen")
            episode_elem.text = str(program_info["episode_num"])
        
        added_count += 1
    
    log(f"✅ 添加了 {added_count} 个节目（去重后）")
    return added_count

def save_xml_file(tv_element: ET.Element, output_file: str) -> bool:
    """保存XML文件"""
    try:
        # 生成XML字符串
        xml_string = ET.tostring(tv_element, encoding="utf-8", xml_declaration=True)
        
        # 解析并美化
        dom = minidom.parseString(xml_string)
        dom_pretty = dom.toprettyxml(indent="  ", encoding="utf-8")
        
        # 保存文件
        with open(output_file, "wb") as f:
            f.write(dom_pretty)
        
        file_size = os.path.getsize(output_file)
        log(f"✅ XML文件保存成功: {output_file}")
        log(f"   文件大小: {file_size:,} 字节")
        
        return True
        
    except Exception as e:
        log(f"❌ 保存XML文件失败: {e}", True)
        return False

def create_gzip_version(xml_file: str, gz_file: str) -> bool:
    """创建GZIP压缩版本"""
    try:
        import gzip
        
        with open(xml_file, "rb") as f_in:
            with gzip.open(gz_file, "wb") as f_out:
                f_out.writelines(f_in)
        
        gz_size = os.path.getsize(gz_file)
        log(f"✅ GZIP压缩文件保存成功: {gz_file}")
        log(f"   文件大小: {gz_size:,} 字节")
        
        return True
        
    except ImportError:
        log("⚠️ gzip模块不可用，跳过压缩文件生成")
        return False
    except Exception as e:
        log(f"❌ 创建GZIP文件失败: {e}")
        return False

# ================= 主程序 ================= #
def main():
    """主程序"""
    print("=" * 60)
    print("beIN SPORTS 专业EPG生成器 (修复版)")
    print("使用接口原始时区，不进行时区转换")
    print("=" * 60)
    
    # 清理错误日志
    if os.path.exists(ERROR_LOG):
        os.remove(ERROR_LOG)
    
    # 1. 加载配置文件
    config = load_config(CONFIG_FILE)
    if not config:
        log("❌ 加载配置文件失败，程序退出", True)
        return False
    
    # 2. 处理频道数据
    channels, id_to_channel, xml_id_to_channel = process_channels(config)
    if not channels:
        log("❌ 没有可用的频道数据，程序退出", True)
        return False
    
    # 3. 设置时间范围
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=DAYS_BACK)
    end_time = now + timedelta(days=DAYS_FORWARD)
    
    print(f"\n📅 EPG时间范围:")
    print(f"   当前时间: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   覆盖时长: {DAYS_BACK + DAYS_FORWARD} 天")
    print(f"   ⚠️ 注意: 使用接口原始时区，不进行时区转换")
    
    # 4. 获取EPG数据
    all_programs = []
    channel_ids = [ch["id"] for ch in channels]
    
    # 分批获取，避免URL过长
    batch_size = 8
    for i in range(0, len(channel_ids), batch_size):
        batch = channel_ids[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(channel_ids) + batch_size - 1) // batch_size
        
        print(f"\n获取批次 {batch_num}/{total_batches}: {len(batch)} 个频道")
        
        programs = fetch_epg_data(batch, start_time, end_time)
        if programs:
            all_programs.extend(programs)
            print(f"  收到 {len(programs)} 个节目")
        else:
            print(f"  无节目数据")
        
        # 延迟以避免请求过快
        if i + batch_size < len(channel_ids):
            time.sleep(REQUEST_DELAY)
    
    if not all_programs:
        print("⚠️ 未获取到任何节目数据，但仍会生成频道定义")
    
    # 5. 生成XML
    print("\n生成XML结构...")
    
    # 创建根元素
    tv_root = create_xml_root()
    
    # 添加频道定义
    add_channel_definitions(tv_root, channels)
    
    # 添加节目数据
    program_count = add_programmes(tv_root, all_programs, id_to_channel)
    
    # 6. 保存文件
    print("\n保存输出文件...")
    
    if not save_xml_file(tv_root, OUTPUT_XML):
        log("生成XML文件失败", True)
        return False
    
    # 创建压缩版本
    try:
        create_gzip_version(OUTPUT_XML, OUTPUT_GZ)
    except ImportError:
        print("⚠️ gzip模块不可用，跳过压缩文件生成")
    
    # 7. 显示统计信息
    print("\n" + "=" * 60)
    print("✅ EPG生成完成！")
    print("=" * 60)
    
    print(f"\n📊 统计信息:")
    print(f"   配置文件: {CONFIG_FILE}")
    print(f"   总频道数: {len(channels)}")
    print(f"   获取节目: {len(all_programs)} 条")
    print(f"   有效节目: {program_count} 条（去重后）")
    print(f"   时间范围: {start_time.strftime('%Y-%m-%d')} 到 {end_time.strftime('%Y-%m-%d')}")
    print(f"   输出文件: {OUTPUT_XML}")
    print(f"   压缩文件: {OUTPUT_GZ}")
    
    # 显示文件大小
    if os.path.exists(OUTPUT_XML):
        size = os.path.getsize(OUTPUT_XML)
        print(f"   XML大小: {size:,} 字节 ({size/1024/1024:.2f} MB)")
    
    if os.path.exists(OUTPUT_GZ):
        size = os.path.getsize(OUTPUT_GZ)
        print(f"   GZ大小: {size:,} 字节 ({size/1024/1024:.2f} MB)")
    
    # 显示频道摘要
    print(f"\n📺 频道摘要（前10个）:")
    for i, channel in enumerate(channels[:10], 1):
        icon_status = "✅" if channel.get("icon") else "❌"
        print(f"  {i:2d}. {channel['display_name']:25s} [{channel['xml_id']}] 图标: {icon_status}")
    
    if len(channels) > 10:
        print(f"  ... 还有 {len(channels)-10} 个频道")
    
    # 显示XML片段预览
    try:
        with open(OUTPUT_XML, "r", encoding="utf-8") as f:
            content = f.read(2000)  # 读取前2000字符
            lines = content.split('\n')[:15]
            print(f"\n📄 XML文件前15行预览:")
            for i, line in enumerate(lines, 1):
                print(f"  {i:2d}: {line.rstrip()}")
    except:
        pass
    
    # 8. 检查错误日志
    if os.path.exists(ERROR_LOG) and os.path.getsize(ERROR_LOG) > 0:
        print(f"\n⚠️ 发现错误，请查看: {ERROR_LOG}")
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
        sys.exit(130)
        
    except Exception as e:
        log(f"程序异常: {e}", True)
        sys.exit(1)