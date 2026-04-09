#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beIN EPG 终极版
结合原始可用脚本的解析逻辑 + mapping.json映射 + 改进功能
"""

import requests
import json
import datetime
import sys
import re
import gzip
import os
import time
from datetime import timedelta
from html import unescape

BASE_URL = "https://www.bein.com/en/epg-ajax-template/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bein.com/en/tv-guide/",
    "X-Requested-With": "XMLHttpRequest"
}

OUT_XML = "bein_epg.xml"
OUT_GZ = "bein_epg.xml.gz"

def log(msg):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def load_channel_mapping():
    """加载mapping.json并构建映射"""
    try:
        with open('mapping.json', 'r', encoding='utf-8') as f:
            mapping_data = json.load(f)
        
        # 构建映射：临时ID -> 最终频道信息
        channel_map = {}
        
        for slider_key, channel_info in mapping_data.items():
            if 'name' in channel_info:
                channel_name = channel_info['name']
                # 生成最终频道ID（使用频道名转换）
                channel_id = re.sub(r'[^a-z0-9]+', '_', channel_name.lower()).strip('_')
                
                # 避免重复ID
                original_id = channel_id
                suffix = 1
                while channel_id in channel_map:
                    channel_id = f"{original_id}_{suffix}"
                    suffix += 1
                
                channel_map[slider_key] = {
                    'id': channel_id,
                    'name': channel_name,
                    'logo': channel_info.get('logo', ''),
                    'original_key': slider_key
                }
        
        sports_count = len([k for k in channel_map if k.startswith('sports_')])
        ent_count = len([k for k in channel_map if k.startswith('entertainment_')])
        log(f"✅ 已加载 {sports_count + ent_count} 个频道映射")
        return channel_map
        
    except Exception as e:
        log(f"❌ 加载mapping.json失败: {e}")
        return {}

def fetch_html(date_str, category):
    """获取HTML源代码 - 使用原始脚本的参数"""
    params = {
        "action": "epg_fetch",
        "offset": "0",
        "category": category,
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": date_str,
        "language": "EN",
        "postid": "25356",
        "loadindex": "0"
    }
    
    try:
        r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        log(f"✅ 获取{category}数据: {len(r.text):,} 字节")
        return r.text
    except Exception as e:
        log(f"❌ 获取{category}失败: {e}")
        return ""

def parse_html_original(html, category):
    """使用原始脚本的解析逻辑"""
    all_programs = []
    
    if not html:
        return all_programs
    
    # 👉 找所有 slider（唯一频道标识） - 原始脚本逻辑
    slider_ids = re.findall(r'id=["\'](slider_\d+)["\']', html)
    slider_ids = list(dict.fromkeys(slider_ids))  # 去重
    
    log(f"  [{category}] 找到 {len(slider_ids)} 个slider")
    
    for sid in slider_ids:
        cid = f"{category}_{sid}"
        
        # 👉 精确截取当前频道块 - 原始脚本逻辑
        block_match = re.search(
            rf'<div[^>]+id=["\']{sid}["\'][\s\S]*?<ul[\s\S]*?</ul>',
            html
        )
        
        if not block_match:
            continue
        
        slider_html = block_match.group(0)
        
        # ================= 解析节目 - 原始脚本逻辑 ================= #
        items = re.findall(r'<li[^>]*>(.*?)</li>', slider_html, re.S)
        
        for it in items:
            # 标题 - 原始脚本逻辑
            title_match = re.search(r'class=title[^>]*>(.*?)<', it, re.S)
            # 时间 - 原始脚本逻辑
            time_match = re.search(r'(\d{2}:\d{2}).*?(\d{2}:\d{2})', it)
            
            if not title_match or not time_match:
                continue
            
            title = title_match.group(1).strip()
            start, end = time_match.groups()
            
            all_programs.append({
                "channel_temp_id": cid,  # 临时ID，后面会替换
                "title": title,
                "start": start,
                "end": end
            })
    
    return all_programs

def process_all_days(channel_map):
    """处理所有天数的数据"""
    all_programs = []
    DAYS = 3  # 抓取3天节目
    
    for d in range(DAYS):
        date = (datetime.date.today() + timedelta(days=d)).strftime("%Y-%m-%d")
        log(f"\n📅 处理日期: {date} (第{d+1}天)")
        
        for category in ["sports", "entertainment"]:
            log(f"[FETCH] {category} {date}")
            
            html = fetch_html(date, category)
            if html:
                programs = parse_html_original(html, category)
                
                # 为每个节目添加日期
                for prog in programs:
                    prog["date"] = date
                    prog["category"] = category
                
                all_programs.extend(programs)
                log(f"  ✅ 找到 {len(programs)} 个{category}节目")
    
    return all_programs

def convert_programs_with_mapping(all_programs, channel_map):
    """将节目从临时ID转换为最终频道ID"""
    converted_programs = []
    channel_info_map = {}  # 最终频道ID -> 频道信息
    
    for prog in all_programs:
        temp_id = prog["channel_temp_id"]
        
        # 构建在mapping.json中的键
        if temp_id in channel_map:
            channel_info = channel_map[temp_id]
            final_channel_id = channel_info['id']
            final_channel_name = channel_info['name']
            
            # 存储频道信息
            if final_channel_id not in channel_info_map:
                channel_info_map[final_channel_id] = {
                    'name': final_channel_name,
                    'logo': channel_info.get('logo', '')
                }
            
            # 转换节目
            converted_prog = prog.copy()
            converted_prog['channel_id'] = final_channel_id
            converted_prog['channel_name'] = final_channel_name
            converted_programs.append(converted_prog)
        else:
            # 如果没有找到映射，尝试其他格式
            # 有些键可能是 "sports_slider_01" 而不是 "sports_slider_1"
            # 尝试清理前导零
            parts = temp_id.split('_')
            if len(parts) >= 3:
                try:
                    slider_num = int(parts[-1])
                    clean_temp_id = f"{parts[0]}_{parts[1]}_{slider_num}"
                    if clean_temp_id in channel_map:
                        channel_info = channel_map[clean_temp_id]
                        final_channel_id = channel_info['id']
                        final_channel_name = channel_info['name']
                        
                        if final_channel_id not in channel_info_map:
                            channel_info_map[final_channel_id] = {
                                'name': final_channel_name,
                                'logo': channel_info.get('logo', '')
                            }
                        
                        converted_prog = prog.copy()
                        converted_prog['channel_id'] = final_channel_id
                        converted_prog['channel_name'] = final_channel_name
                        converted_programs.append(converted_prog)
                except ValueError:
                    continue
    
    return converted_programs, channel_info_map

def generate_xml_using_original_times(channel_info_map, all_programs):
    """生成XML（直接使用抓取的原始时间，不进行时区转换）"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv>']
    
    # 1. 频道定义
    for channel_id, channel_info in channel_info_map.items():
        xml.append(f'  <channel id="{channel_id}">')
        xml.append(f'    <display-name>{channel_info["name"]}</display-name>')
        if channel_info["logo"]:
            xml.append(f'    <icon src="{channel_info["logo"]}"/>')
        xml.append('  </channel>')
    
    # 2. 节目信息
    # 使用UTC+0时区（或任何时区，但保持原样）
    # XMLTV格式需要时区信息，我们使用+0000表示不偏移
    timezone_offset = "+0000"
    
    for prog in all_programs:
        try:
            date_obj = datetime.datetime.strptime(prog["date"], "%Y-%m-%d")
            sh, sm = map(int, prog["start"].split(":"))
            eh, em = map(int, prog["end"].split(":"))
            
            # 创建时间对象
            start_dt = date_obj.replace(hour=sh, minute=sm, second=0)
            end_dt = date_obj.replace(hour=eh, minute=em, second=0)
            
            # 处理跨天节目
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            
            # 格式化为XMLTV格式，使用UTC+0时区
            start_str = start_dt.strftime("%Y%m%d%H%M%S") + timezone_offset
            end_str = end_dt.strftime("%Y%m%d%H%M%S") + timezone_offset
            
            # 转义XML特殊字符
            title = prog["title"]
            for old, new in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")]:
                title = title.replace(old, new)
            
            xml.append(f'  <programme start="{start_str}" stop="{end_str}" channel="{prog["channel_id"]}">')
            xml.append(f'    <title>{title}</title>')
            xml.append(f'    <desc>{prog["start"]} - {prog["end"]}</desc>')
            xml.append('  </programme>')
            
        except Exception as e:
            continue
    
    xml.append('</tv>')
    return "\n".join(xml)

def main():
    log("=" * 60)
    log("beIN EPG 终极版 - 使用原始解析逻辑 + 改进功能")
    log("=" * 60)
    
    # 1. 加载频道映射
    channel_map = load_channel_mapping()
    if not channel_map:
        return False
    
    # 2. 获取并解析所有节目（使用原始解析逻辑）
    log("\n🔍 开始解析节目数据...")
    raw_programs = process_all_days(channel_map)
    
    if not raw_programs:
        log("❌ 没有找到任何节目")
        return False
    
    log(f"\n📊 临时统计: 找到 {len(raw_programs)} 个节目（使用临时ID）")
    
    # 3. 将节目从临时ID转换为最终频道ID
    log("🔄 将临时频道ID转换为最终频道ID...")
    all_programs, channel_info_map = convert_programs_with_mapping(raw_programs, channel_map)
    
    if not all_programs:
        log("❌ 频道映射转换失败")
        return False
    
    log(f"📊 最终统计: {len(channel_info_map)} 个频道, {len(all_programs)} 个节目")
    
    # 4. 显示前几个节目
    if all_programs:
        log("\n📺 节目示例 (前5个):")
        for i, prog in enumerate(all_programs[:5]):
            log(f"  {i+1}. [{prog['channel_name']}] {prog['start']}-{prog['end']} {prog['title'][:40]}...")
    
    # 5. 生成XML
    log("\n🔨 生成XML文件...")
    xml_content = generate_xml_using_original_times(channel_info_map, all_programs)
    
    with open(OUT_XML, "w", encoding="utf-8") as f:
        f.write(xml_content)
    
    xml_size = os.path.getsize(OUT_XML)
    log(f"✅ XML已保存: {OUT_XML} ({xml_size:,} 字节)")
    
    # 6. 生成GZ压缩文件
    with gzip.open(OUT_GZ, "wb") as f:
        f.write(xml_content.encode("utf-8"))
    
    gz_size = os.path.getsize(OUT_GZ)
    log(f"✅ GZ压缩文件已保存: {OUT_GZ} ({gz_size:,} 字节)")
    
    # 7. 显示时区信息
    log(f"\n🌍 时区信息: 使用原始时间 (UTC+0)")
    log(f"📅 处理天数: 3天")
    log(f"📺 节目时间: 与电视台节目表完全一致，无时区转换")
    
    log("\n🎉 EPG生成完成!")
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("\n⏹️ 用户中断")
        sys.exit(130)
    except Exception as e:
        log(f"\n💥 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)