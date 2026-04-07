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
import re

# ================= 配置 ================= #
EPG_URL = "https://nowplayer.now.com/tvguide/epglist"

# 频道ID将从config.json中读取
CHANNEL_IDS = []
DAYS = 1
BATCH_SIZE = 8
SLEEP = 0.8
TIMEOUT = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# ================= 日志 ================= #
def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")

# ================= 读取配置 ================= #
def load_config():
    """从config.json加载频道配置"""
    try:
        if not os.path.exists(CONFIG_FILE):
            log("❌ 错误: 未找到 config.json")
            return None
        
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        log(f"✅ 从 config.json 加载了 {len(config)} 个频道配置")
        
        # 显示前几个频道
        sample = list(config.items())[:3]
        for cid, info in sample:
            log(f"   📺 {cid}: {info.get('name', '未知')}")
        
        return config
        
    except Exception as e:
        log(f"❌ 读取 config.json 失败: {e}")
        return None

# ================= 时间转换 ================= #
def format_time(ts):
    """将时间戳转换为XMLTV格式"""
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S +0000")

# ================= 获取中文标题 ================= #
def get_chinese_title(program):
    """
    从节目信息中提取中文标题
    首先检查数据结构，然后尝试多种可能的字段
    """
    if not isinstance(program, dict):
        return "Unknown"
    
    # 记录调试信息，只记录第一个节目
    if "DEBUG" not in get_chinese_title.__dict__:
        get_chinese_title.DEBUG = True
        log(f"🔍 检查第一个节目的数据结构:")
        for key, value in program.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                log(f"   {key}: {repr(value)[:50]}")
            else:
                log(f"   {key}: {type(value)}")
    
    # 按优先级尝试各种可能的中文标题字段
    title_sources = [
        # 直接中文字段
        ("nameZh", "titleZh", "name_zh", "title_zh", "name_cn", "title_cn"),
        # 本地化字段
        ("localizedTitle", "localizedName"),
        # 通用字段
        ("name", "title", "programmeName", "displayName", "displayTitle"),
        # 多语言字典字段
        ("name_dict", "title_dict"),
    ]
    
    for field_group in title_sources:
        for field in field_group:
            if field in program and program[field]:
                value = program[field]
                
                # 处理字典类型的多语言字段
                if field.endswith("_dict") and isinstance(value, dict):
                    for lang in ["zh", "zh-CN", "zh_HK", "zh_TW", "cn", "zh-CN", "zh-HK"]:
                        if lang in value and value[lang]:
                            title = str(value[lang]).strip()
                            if title:
                                return title
                
                # 处理字符串字段
                elif isinstance(value, str) and value.strip():
                    title = value.strip()
                    # 检查是否包含中文字符
                    if any('\u4e00' <= char <= '\u9fff' for char in title):
                        return title
                    elif title:  # 即使不是中文也返回
                        return title
    
    # 尝试从name或title字段的字典中提取
    for field in ["name", "title"]:
        if field in program and isinstance(program[field], dict):
            for lang in ["zh", "zh-CN", "zh_HK", "zh_TW", "cn"]:
                if lang in program[field] and program[field][lang]:
                    title = str(program[field][lang]).strip()
                    if title:
                        return title
    
    return "Unknown"

# ================= 请求EPG数据 ================= #
def fetch_epg_batch(batch_channels, day):
    """批量获取频道EPG数据"""
    
    params = []
    for channel_id in batch_channels:
        params.append(("channelIdList[]", channel_id))
    params.append(("day", str(day)))
    
    # 尝试多种语言参数
    params.append(("locale", "zh_HK"))
    params.append(("lang", "zh"))
    params.append(("language", "zh-CN"))
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }
    
    try:
        log(f"  正在获取 {len(batch_channels)} 个频道的EPG (Day {day})...")
        
        response = requests.get(
            EPG_URL, 
            params=params, 
            headers=headers, 
            timeout=TIMEOUT
        )
        
        if response.status_code != 200:
            log(f"  ❌ HTTP {response.status_code}: 请求失败")
            return None
        
        data = response.json()
        
        if not isinstance(data, list):
            log(f"  ⚠️ 返回数据格式异常: {type(data)}")
            return None
        
        return data
        
    except Exception as e:
        log(f"  ❌ 请求异常: {e}")
        return None

# ================= 主程序 ================= #
def main():
    log("=" * 60)
    log("NOWTV EPG抓取工具 - 抓取所有频道")
    log("=" * 60)
    
    # 1. 加载配置
    config = load_config()
    if not config:
        return
    
    # 2. 从config中提取所有频道ID列表
    channel_ids = list(config.keys())
    log(f"📊 从config.json获取到 {len(channel_ids)} 个频道ID")
    log(f"🔧 开始抓取所有 {len(channel_ids)} 个频道")
    
    # 3. 开始抓取EPG数据
    log("\n" + "=" * 60)
    log("开始抓取EPG数据")
    log("=" * 60)
    
    all_epg_data = {}  # 存储所有频道的EPG数据
    
    for day in range(DAYS):
        log(f"\n📅 处理 Day {day} 的节目表")
        log("-" * 40)
        
        day_epg_count = 0
        day_channel_count = 0
        
        # 分批处理所有频道
        for i in range(0, len(channel_ids), BATCH_SIZE):
            batch = channel_ids[i:i + BATCH_SIZE]
            
            # 获取这批频道的EPG数据
            epg_data = fetch_epg_batch(batch, day)
            
            if epg_data and isinstance(epg_data, list):
                # 处理返回的EPG数据
                for idx, channel_programs in enumerate(epg_data):
                    if idx >= len(batch):
                        break
                    
                    channel_id = batch[idx]
                    
                    if not channel_programs or not isinstance(channel_programs, list):
                        # 记录没有节目的频道
                        if channel_id in config:  # 只在config中有定义的情况下记录
                            channel_name = config.get(channel_id, {}).get("name", channel_id)
                            log(f"  ⚠️ 频道 {channel_id} ({channel_name}) 无节目数据")
                        continue
                    
                    # 初始化这个频道的EPG数据
                    if channel_id not in all_epg_data:
                        all_epg_data[channel_id] = []
                    
                    # 添加节目数据
                    all_epg_data[channel_id].extend(channel_programs)
                    
                    # 统计
                    day_epg_count += len(channel_programs)
                    day_channel_count += 1
                    
                    # 显示频道信息
                    channel_name = config.get(channel_id, {}).get("name", channel_id)
                    
                    # 检查第一个节目的标题
                    if channel_programs:
                        first_program = channel_programs[0]
                        title = get_chinese_title(first_program)
                        
                        # 检查标题是否包含中文字符
                        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in title)
                        
                        log(f"  ✅ {channel_id} ({channel_name}): {len(channel_programs)}个节目")
                        log(f"      首个节目: {title[:30]}{'...' if len(title) > 30 else ''}")
                        if not has_chinese and title != "Unknown":
                            log(f"      ⚠️ 注意: 节目标题不是中文")
            
            # 请求间隔
            time.sleep(SLEEP)
            
            # 显示进度
            progress = min(i + BATCH_SIZE, len(channel_ids))
            log(f"  📈 进度: {progress}/{len(channel_ids)} 个频道")
        
        log(f"📈 Day {day} 完成: {day_channel_count}个频道, {day_epg_count}个节目")
    
    # 4. 检查是否抓取到数据
    if not all_epg_data:
        log("❌ 没有获取到任何EPG数据，无法生成XML")
        return
    
    # 5. 统计信息
    log("\n" + "=" * 60)
    log("抓取统计信息")
    log("=" * 60)
    
    total_programs = 0
    chinese_programs = 0
    english_programs = 0
    
    for channel_id, programs in all_epg_data.items():
        for program in programs:
            title = get_chinese_title(program)
            total_programs += 1
            
            # 检查是否包含中文字符
            if any('\u4e00' <= char <= '\u9fff' for char in title):
                chinese_programs += 1
            elif title != "Unknown":
                english_programs += 1
    
    log(f"📺 有数据的频道数: {len(all_epg_data)}/{len(channel_ids)}")
    log(f"🎬 节目总数: {total_programs}")
    log(f"🇨🇳 中文节目: {chinese_programs} ({chinese_programs/total_programs*100:.1f}%)" if total_programs > 0 else "🇨🇳 中文节目: 0")
    log(f"🇺🇸 英文节目: {english_programs} ({english_programs/total_programs*100:.1f}%)" if total_programs > 0 else "🇺🇸 英文节目: 0")
    
    # 显示各频道节目数量排行
    log("\n📈 各频道节目数量排行:")
    channel_stats = []
    for channel_id, programs in all_epg_data.items():
        channel_name = config.get(channel_id, {}).get("name", channel_id)
        channel_stats.append((channel_name, len(programs)))
    
    # 按节目数量排序
    channel_stats.sort(key=lambda x: x[1], reverse=True)
    
    for i, (name, count) in enumerate(channel_stats[:15]):
        log(f"   {i+1:2d}. {name[:20]:20s}: {count:3d} 个节目")
    
    if len(channel_stats) > 15:
        log(f"   ... 还有 {len(channel_stats)-15} 个频道")
    
    # 6. 生成XMLTV格式
    log("\n" + "=" * 60)
    log("生成XMLTV格式文件")
    log("=" * 60)
    
    # 创建XML根元素
    tv_element = ET.Element("tv")
    
    # 添加频道定义
    log("添加频道定义到XML...")
    channel_count = 0
    for channel_id, channel_info in config.items():
        channel_name = channel_info.get("name", f"频道{channel_id}")
        
        # 只添加有EPG数据的频道
        if channel_id in all_epg_data:
            channel_element = ET.SubElement(tv_element, "channel", {"id": channel_name})
            ET.SubElement(channel_element, "display-name").text = channel_name
            
            logo = channel_info.get("logo", "")
            if logo:
                ET.SubElement(channel_element, "icon", {"src": logo})
            
            channel_count += 1
    
    log(f"✅ 添加了 {channel_count} 个频道定义")
    
    # 添加节目信息
    log("添加节目信息到XML...")
    program_count = 0
    for channel_id, programs in all_epg_data.items():
        if not programs:
            continue
        
        channel_info = config.get(channel_id, {})
        channel_name = channel_info.get("name", channel_id)
        
        for program in programs:
            try:
                start_time = program.get("start", 0)
                end_time = program.get("end", 0)
                
                if not start_time or not end_time:
                    continue
                
                start_str = format_time(start_time)
                end_str = format_time(end_time)
                title = get_chinese_title(program)
                
                # 创建programme元素
                programme_element = ET.SubElement(tv_element, "programme", {
                    "channel": channel_name,
                    "start": start_str,
                    "stop": end_str
                })
                
                # 添加标题
                title_element = ET.SubElement(programme_element, "title")
                title_element.text = title
                
                # 可选：添加描述
                if "description" in program and program["description"]:
                    desc_element = ET.SubElement(programme_element, "desc")
                    desc_element.text = str(program["description"])
                
                program_count += 1
                
            except Exception as e:
                log(f"  ⚠️ 处理节目时出错: {e}")
                continue
    
    log(f"✅ 添加了 {program_count} 个节目信息")
    
    # 7. 美化和保存XML文件
    log("\n" + "=" * 60)
    log("美化和保存XML/GZ文件")
    log("=" * 60)
    
    try:
        # 生成原始的XML字符串
        xml_string = ET.tostring(tv_element, encoding="utf-8", xml_declaration=True)
        
        # 美化XML
        dom = minidom.parseString(xml_string)
        pretty_xml = dom.toprettyxml(indent="  ", encoding="utf-8")
        
        # 保存XML文件
        with open(XML_FILE, "wb") as f:
            f.write(pretty_xml)
        
        log(f"✅ XML文件保存成功: {XML_FILE}")
        log(f"   文件大小: {len(pretty_xml):,} 字节")
        
        # 保存压缩的GZ文件
        with open(XML_FILE, "rb") as f_in:
            with gzip.open(GZ_FILE, "wb") as f_out:
                f_out.writelines(f_in)
        
        log(f"✅ GZ文件保存成功: {GZ_FILE}")
        
        # 验证文件
        if os.path.exists(XML_FILE) and os.path.getsize(XML_FILE) > 0:
            log(f"✅ 验证: XML文件存在，大小: {os.path.getsize(XML_FILE):,} 字节")
        else:
            log("❌ 验证失败: XML文件不存在或为空")
            
        if os.path.exists(GZ_FILE) and os.path.getsize(GZ_FILE) > 0:
            log(f"✅ 验证: GZ文件存在，大小: {os.path.getsize(GZ_FILE):,} 字节")
        
    except Exception as e:
        log(f"❌ 保存文件失败: {e}")
        return
    
    # 8. 显示文件预览
    log("\n" + "=" * 60)
    log("文件预览")
    log("=" * 60)
    
    try:
        with open(XML_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[:15]
            log("📄 XML文件前15行预览:")
            for i, line in enumerate(lines, 1):
                log(f"  {i:2d}: {line.rstrip()}")
    except Exception as e:
        log(f"⚠️ 无法读取XML文件预览: {e}")
    
    log("\n" + "=" * 60)
    log("✅ EPG抓取完成！")
    log("=" * 60)
    
    # 9. 生成统计报告
    report = f"""
================== EPG抓取报告 ==================
抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
配置频道数: {len(channel_ids)}
有数据的频道数: {len(all_epg_data)}
节目总数: {total_programs}
中文节目比例: {chinese_programs/total_programs*100:.1f}% ({chinese_programs}/{total_programs})
英文节目比例: {english_programs/total_programs*100:.1f}% ({english_programs}/{total_programs})
生成文件:
  - {XML_FILE} ({os.path.getsize(XML_FILE) if os.path.exists(XML_FILE) else 0} 字节)
  - {GZ_FILE} ({os.path.getsize(GZ_FILE) if os.path.exists(GZ_FILE) else 0} 字节)
==================================================
    """
    
    log(report)

if __name__ == "__main__":
    main()
