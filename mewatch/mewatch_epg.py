#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meWatch EPG 抓取工具 - GitHub Actions 兼容版
功能：获取新加坡meWatch平台全部频道的7天EPG数据
输出：保存到 mewatch/ 目录
"""

import requests
import json
import gzip
import time
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any
import sys

# =========================
# 配置参数
# =========================
BASE_URL = "https://cdn.mewatch.sg"
DAYS_TO_FETCH = 7
PAGE_SIZE = 24
CHANNEL_LIST_ID = "239614"
TOTAL_CHANNELS = 121  # 根据API返回，总共有121个频道

# 输出目录和文件
OUTPUT_DIR = "mewatch"  # GitHub Actions 期望的目录
os.makedirs(OUTPUT_DIR, exist_ok=True)  # 确保目录存在

OUTPUT_JSON = os.path.join(OUTPUT_DIR, "channels.json")
OUTPUT_XML = os.path.join(OUTPUT_DIR, "mewatch.xml")
OUTPUT_GZ = os.path.join(OUTPUT_DIR, "mewatch.xml.gz")
OUTPUT_EPG_JSON = os.path.join(OUTPUT_DIR, "mewatch_epg.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

# =========================
# 工具函数
# =========================
def to_xmltv_time(utc_str: str) -> str:
    """将UTC时间转换为XMLTV格式（新加坡时区 UTC+8）"""
    try:
        if utc_str.endswith('Z'):
            dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        
        dt = dt + timedelta(hours=8)
        return dt.strftime("%Y%m%d%H%M%S +0800")
    except Exception as e:
        return "20000101000000 +0800"

def save_json(data: Dict, filename: str) -> None:
    """保存JSON文件"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ JSON文件已保存: {filename}")

def save_xml(xml_content: str, xml_file: str, gz_file: str) -> None:
    """保存XML和GZ文件"""
    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml_content)
    print(f"✓ XML文件已保存: {xml_file}")
    
    with open(xml_file, "rb") as f_in:
        with gzip.open(gz_file, "wb") as f_out:
            f_out.write(f_in.read())
    print(f"✓ 压缩文件已保存: {gz_file}")

# =========================
# API 调用函数
# =========================
def fetch_all_channels() -> Dict[str, Dict]:
    """获取所有频道信息"""
    print("=" * 60)
    print("步骤1: 获取频道列表 (共121个频道)")
    print("=" * 60)
    
    all_channels = {}
    page = 1
    
    while True:
        print(f"  正在获取第 {page} 页...")
        url = f"{BASE_URL}/api/lists/{CHANNEL_LIST_ID}"
        params = {
            "ff": "idp,ldp,rpt,cd",
            "lang": "en",
            "page": page,
            "page_size": PAGE_SIZE,
            "segments": "all"
        }
        
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=30)
            data = response.json()
            
            items = data.get("items", [])
            if not items:
                break
            
            for item in items:
                channel_id = str(item.get("id", ""))
                if not channel_id:
                    continue
                
                # 频道信息
                channel_info = {
                    "id": channel_id,
                    "name": item.get("title", f"Channel_{channel_id}"),
                    "icon": "",
                    "description": item.get("description", ""),
                    "number": item.get("logicalChannelNumber"),
                    "genres": item.get("genres", []),
                    "isPlayable": item.get("isPlayable", False),
                    "videoFormat": item.get("videoFormat", "")
                }
                
                # 获取图标
                images = item.get("images", {})
                if isinstance(images, dict):
                    for img_key in ["tile", "default", "wallpaper", "poster"]:
                        if img_key in images and images[img_key]:
                            channel_info["icon"] = images[img_key]
                            break
                
                all_channels[channel_id] = channel_info
            
            print(f"    第 {page} 页: 获取到 {len(items)} 个频道，累计 {len(all_channels)} 个")
            
            if len(all_channels) >= TOTAL_CHANNELS:
                break
                
            page += 1
            time.sleep(0.3)
            
        except Exception as e:
            print(f"  获取失败: {e}")
            break
    
    print(f"\n✓ 成功获取 {len(all_channels)} 个频道")
    return all_channels

def fetch_single_channel_epg(channel_id: str, channel_name: str, days: int) -> List[Dict]:
    """获取单个频道多天的EPG数据"""
    programmes = []
    start_date = datetime.utcnow()
    
    for day in range(days):
        current_date = (start_date + timedelta(days=day)).strftime("%Y-%m-%d")
        
        url = f"{BASE_URL}/api/schedules"
        params = {
            "channels": channel_id,
            "date": current_date,
            "duration": 24,
            "hour": 0,
            "intersect": "true",
            "lang": "en",
            "segments": "all",
            "ff": "idp,ldp,rpt,cd"
        }
        
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=30)
            data = response.json()
            
            schedules = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "schedules" in item:
                        schedules.extend(item.get("schedules", []))
            elif isinstance(data, dict) and "schedules" in data:
                schedules = data.get("schedules", [])
            
            for schedule in schedules:
                if not isinstance(schedule, dict) or schedule.get("isGap"):
                    continue
                
                item_data = schedule.get("item", {})
                programme = {
                    "channel": channel_id,
                    "start": schedule.get("startDate"),
                    "stop": schedule.get("endDate"),
                    "title": item_data.get("title", "No Title"),
                    "description": item_data.get("description", ""),
                    "episode_title": item_data.get("episodeTitle", ""),
                    "season": item_data.get("seasonNumber"),
                    "episode": item_data.get("episodeNumber"),
                    "classification": item_data.get("classification", {}),
                    "images": item_data.get("images", {}),
                    "duration": schedule.get("duration")
                }
                programmes.append(programme)
            
            time.sleep(0.2)
            
        except Exception as e:
            print(f"    获取 {current_date} 失败: {e}")
            continue
    
    return programmes

def fetch_all_epg_data(channels: Dict[str, Dict]) -> List[Dict]:
    """获取所有频道的EPG数据"""
    print("\n" + "=" * 60)
    print(f"步骤2: 获取全部 {len(channels)} 个频道 {DAYS_TO_FETCH} 天的EPG数据")
    print("=" * 60)
    print(f"预计请求数: {len(channels)} 频道 × {DAYS_TO_FETCH} 天 = {len(channels) * DAYS_TO_FETCH} 次API调用")
    print(f"预计时间: 约 {len(channels) * DAYS_TO_FETCH * 0.3 / 60:.1f} 分钟")
    print("开始处理...\n")
    
    all_programmes = []
    channel_ids = list(channels.keys())
    
    for i, channel_id in enumerate(channel_ids, 1):
        channel_name = channels[channel_id]["name"]
        print(f"[{i}/{len(channel_ids)}] 处理频道: {channel_name} ({channel_id})")
        
        start_time = time.time()
        channel_programs = fetch_single_channel_epg(channel_id, channel_name, DAYS_TO_FETCH)
        elapsed = time.time() - start_time
        
        all_programmes.extend(channel_programs)
        print(f"    完成: {len(channel_programs)} 个节目 (耗时: {elapsed:.1f}秒)")
    
    print(f"\n✓ EPG数据获取完成")
    print(f"总节目数: {len(all_programmes)}")
    return all_programmes

# =========================
# 生成输出文件
# =========================
def generate_channels_json(channels: Dict[str, Dict]) -> Dict:
    """生成频道JSON文件"""
    print("\n" + "=" * 60)
    print("步骤3: 生成频道JSON文件")
    print("=" * 60)
    
    channels_list = []
    for channel_id, info in channels.items():
        channel_data = {
            "id": channel_id,
            "name": info["name"],
            "icon": info["icon"],
            "description": info["description"],
            "number": info["number"],
            "genres": info["genres"],
            "isPlayable": info["isPlayable"],
            "videoFormat": info["videoFormat"]
        }
        channels_list.append(channel_data)
    
    channels_list.sort(key=lambda x: (x["number"] is None, x["number"]))
    
    json_data = {
        "metadata": {
            "source": "meWatch Singapore",
            "generated_at": datetime.now().isoformat(),
            "total_channels": len(channels_list)
        },
        "channels": channels_list
    }
    
    return json_data

def generate_xmltv(channels: Dict[str, Dict], programmes: List[Dict]) -> str:
    """生成XMLTV格式文件"""
    print("\n" + "=" * 60)
    print("步骤4: 生成XMLTV文件")
    print("=" * 60)
    
    xml_lines = []
    xml_lines.append('<?xml version="1.0" encoding="UTF-8" ?>')
    xml_lines.append('<tv generator-info-name="meWatch EPG" source-info-url="https://www.mewatch.sg">')
    
    # 添加频道
    print("添加频道信息...")
    for channel_id, channel_info in channels.items():
        xml_lines.append(f'  <channel id="{channel_id}">')
        xml_lines.append(f'    <display-name>{channel_info["name"]}</display-name>')
        
        if channel_info["description"]:
            desc = channel_info["description"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_lines.append(f'    <desc>{desc}</desc>')
        
        if channel_info["icon"]:
            xml_lines.append(f'    <icon src="{channel_info["icon"]}" />')
        
        xml_lines.append('  </channel>')
    
    # 添加节目
    print("添加节目信息...")
    programme_count = 0
    for prog in programmes:
        start_time = prog.get("start")
        stop_time = prog.get("stop")
        
        if not start_time or not stop_time:
            continue
        
        start_xml = to_xmltv_time(start_time)
        stop_xml = to_xmltv_time(stop_time)
        
        xml_lines.append(f'  <programme start="{start_xml}" stop="{stop_xml}" channel="{prog["channel"]}">')
        
        # 标题
        title = prog.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        xml_lines.append(f'    <title lang="en">{title}</title>')
        
        # 描述
        description = prog.get("description", "")
        if description:
            description = description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_lines.append(f'    <desc lang="en">{description}</desc>')
        
        # 分类
        classification = prog.get("classification", {})
        if isinstance(classification, dict) and classification.get("name"):
            category = classification["name"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_lines.append(f'    <category lang="en">{category}</category>')
        
        xml_lines.append('  </programme>')
        programme_count += 1
    
    xml_lines.append('</tv>')
    
    print(f"添加了 {programme_count} 个节目")
    return "\n".join(xml_lines)

# =========================
# 主程序
# =========================
def main():
    print("=" * 60)
    print("meWatch EPG 完整数据抓取工具 (GitHub Actions 兼容版)")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出目录: {OUTPUT_DIR}/")
    print("=" * 60)
    
    try:
        # 1. 获取所有频道
        channels = fetch_all_channels()
        if not channels:
            print("错误: 未能获取到任何频道")
            return
        
        # 2. 生成频道JSON文件
        channels_json = generate_channels_json(channels)
        save_json(channels_json, OUTPUT_JSON)
        
        # 3. 获取所有频道的EPG数据
        programmes = fetch_all_epg_data(channels)
        
        if not programmes:
            print("警告: 未能获取到任何节目数据")
        
        # 4. 生成XMLTV文件
        xml_content = generate_xmltv(channels, programmes)
        save_xml(xml_content, OUTPUT_XML, OUTPUT_GZ)
        
        # 5. 保存完整EPG数据
        if programmes:
            epg_data = {
                "metadata": {
                    "fetched_at": datetime.now().isoformat(),
                    "channels_count": len(channels),
                    "programmes_count": len(programmes),
                    "days": DAYS_TO_FETCH
                },
                "programmes": programmes
            }
            save_json(epg_data, OUTPUT_EPG_JSON)
        
        # 6. 显示结果
        print("\n" + "=" * 60)
        print("任务完成!")
        print(f"频道数: {len(channels)}")
        print(f"节目数: {len(programmes)}")
        print(f"数据天数: {DAYS_TO_FETCH}")
        print("\n生成的文件 (在目录 mewatch/ 中):")
        print(f"  1. channels.json - 频道信息 (id, name, icon等)")
        print(f"  2. mewatch.xml - XMLTV格式EPG")
        print(f"  3. mewatch.xml.gz - 压缩的XMLTV")
        if programmes:
            print(f"  4. mewatch_epg.json - 完整EPG数据")
        print("=" * 60)
        
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
    except Exception as e:
        print(f"\n程序运行出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
