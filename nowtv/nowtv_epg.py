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
DAYS = 2
BATCH_SIZE = 8
SLEEP = 0.5
TIMEOUT = 20

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
    根据NOWTV的API数据结构，标题可能在多个字段中
    """
    if not isinstance(program, dict):
        return "Unknown"
    
    # 首先检查节目信息的所有字段，看看数据结构
    debug_keys = [k for k in program.keys() if 'name' in k.lower() or 'title' in k.lower()]
    if debug_keys:
        log(f"  标题相关字段: {debug_keys}")
    
    # 尝试多种可能的标题字段，按优先级
    title_fields = [
        "name",  # 可能是中文
        "title",  # 可能是中文
        "programmeName",  # 节目名称
        "displayName",  # 显示名称
        "localizedTitle",  # 本地化标题
        "nameZh",  # 中文名称
        "titleZh",  # 中文标题
    ]
    
    for field in title_fields:
        if field in program and program[field]:
            value = program[field]
            
            # 如果字段值是字典，尝试获取中文
            if isinstance(value, dict):
                for lang in ["zh", "zh-CN", "zh-HK", "zh_TW", "cn", "zh_CN", "zh_HK"]:
                    if lang in value and value[lang]:
                        return str(value[lang]).strip()
            
            # 如果直接是字符串
            elif isinstance(value, str) and value.strip():
                return value.strip()
    
    # 如果没有找到，返回第一个非空字符串字段
    for key, value in program.items():
        if isinstance(value, str) and value.strip() and key not in ["start", "end", "channelId"]:
            return value.strip()
    
    return "Unknown"

# ================= 美化XML ================= #
def prettify_xml(xml_string):
    """美化XML输出"""
    # 使用minidom格式化XML
    dom = xml.dom.minidom.parseString(xml_string)
    pretty_xml = dom.toprettyxml(indent="  ", encoding="utf-8")
    return pretty_xml

# ================= 请求EPG数据 ================= #
def fetch_epg_batch(batch_channels, day):
    """批量获取频道EPG数据"""
    
    params = []
    for channel_id in batch_channels:
        params.append(("channelIdList[]", channel_id))
    params.append(("day", str(day)))
    
    # 添加语言参数，尝试获取中文
    params.append(("locale", "zh_HK"))
    params.append(("lang", "zh"))
    
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
    log("=" * 50)
    log("NOWTV EPG抓取工具 - 美化XML + ID替换为名称")
    log("=" * 50)
    
    # 1. 加载配置
    config = load_config()
    if not config:
        return
    
    # 2. 从config中提取频道ID列表
    channel_ids = list(config.keys())
    log(f"📊 从config.json获取到 {len(channel_ids)} 个频道ID")
    
    # 3. 开始抓取EPG数据
    log("\n" + "=" * 50)
    log("开始抓取EPG数据")
    log("=" * 50)
    
    all_epg_data = {}  # 存储所有频道的EPG数据
    
    for day in range(DAYS):
        log(f"\n📅 处理 Day {day} 的节目表")
        log("-" * 40)
        
        day_epg_count = 0
        day_channel_count = 0
        
        # 分批处理频道
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
                        continue
                    
                    # 初始化这个频道的EPG数据
                    if channel_id not in all_epg_data:
                        all_epg_data[channel_id] = []
                    
                    # 添加节目数据
                    all_epg_data[channel_id].extend(channel_programs)
                    
                    # 统计
                    day_epg_count += len(channel_programs)
                    day_channel_count += 1
                    
                    # 显示第一个节目的标题
                    if channel_programs:
                        first_program = channel_programs[0]
                        title = get_chinese_title(first_program)
                        channel_name = config.get(channel_id, {}).get("name", channel_id)
                        log(f"   ✅ {channel_id} ({channel_name}): {len(channel_programs)}个节目")
                        log(f"      首个节目: {title[:30]}{'...' if len(title) > 30 else ''}")
            
            # 请求间隔
            time.sleep(SLEEP)
        
        log(f"📈 Day {day} 完成: {day_channel_count}个频道, {day_epg_count}个节目")
    
    # 4. 生成XMLTV格式
    log("\n" + "=" * 50)
    log("生成XMLTV格式文件")
    log("=" * 50)
    
    if not all_epg_data:
        log("❌ 没有获取到任何EPG数据，无法生成XML")
        return
    
    # 创建XML根元素
    tv_element = ET.Element("tv")
    
    # 统计信息
    total_channels = len(all_epg_data)
    total_programs = 0
    
    # 添加频道定义 - 这里可以按您的要求修改
    log("添加频道定义...")
    for channel_id, channel_info in config.items():
        # 这里的关键修改：使用频道名称作为channel元素的id
        channel_name = channel_info.get("name", f"频道{channel_id}")
        
        # 创建频道元素，id使用频道名称
        channel_element = ET.SubElement(tv_element, "channel", {"id": channel_name})
        
        # 添加display-name，也使用频道名称
        display_name = ET.SubElement(channel_element, "display-name")
        display_name.text = channel_name
        
        # 频道图标
        logo = channel_info.get("logo", "")
        if logo:
            ET.SubElement(channel_element, "icon", {"src": logo})
    
    # 添加节目信息
    log("添加节目信息...")
    for channel_id, programs in all_epg_data.items():
        if not programs:
            continue
        
        # 获取频道名称
        channel_info = config.get(channel_id, {})
        channel_name = channel_info.get("name", channel_id)
        
        for program in programs:
            try:
                # 提取节目信息
                start_time = program.get("start", 0)
                end_time = program.get("end", 0)
                
                if not start_time or not end_time:
                    continue
                
                start_str = format_time(start_time)
                end_str = format_time(end_time)
                
                # 获取中文标题
                title = get_chinese_title(program)
                
                # 创建programme元素 - 这里的关键修改：channel属性使用频道名称
                programme_element = ET.SubElement(tv_element, "programme", {
                    "channel": channel_name,  # 使用频道名称而不是ID
                    "start": start_str,
                    "stop": end_str
                })
                
                # 添加标题
                title_element = ET.SubElement(programme_element, "title")
                title_element.text = title
                
                # 可选：添加节目描述
                if "description" in program and program["description"]:
                    desc_element = ET.SubElement(programme_element, "desc")
                    desc_element.text = str(program["description"]).strip()
                
                # 可选：添加节目分类
                if "category" in program and program["category"]:
                    category_element = ET.SubElement(programme_element, "category")
                    category_element.text = str(program["category"]).strip()
                
                total_programs += 1
                
            except Exception as e:
                log(f"  ⚠️ 处理节目时出错: {e}")
                continue
    
    # 5. 生成XML字符串并美化
    log("\n" + "=" * 50)
    log("美化和保存XML文件")
    log("=" * 50)
    
    try:
        # 生成原始的XML字符串
        xml_string = ET.tostring(tv_element, encoding="utf-8", xml_declaration=True)
        
        # 美化XML
        pretty_xml = prettify_xml(xml_string)
        
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
        
    except Exception as e:
        log(f"❌ 保存文件失败: {e}")
        return
    
    # 6. 显示统计信息
    log("\n" + "=" * 50)
    log("抓取完成 - 统计信息")
    log("=" * 50)
    log(f"📺 频道总数: {total_channels}")
    log(f"🎬 节目总数: {total_programs}")
    
    # 显示前10个频道的节目数量
    log("\n📈 各频道节目数量排行:")
    channel_stats = []
    for channel_id, programs in all_epg_data.items():
        channel_name = config.get(channel_id, {}).get("name", channel_id)
        channel_stats.append((channel_name, len(programs)))
    
    # 按节目数量排序
    channel_stats.sort(key=lambda x: x[1], reverse=True)
    
    for i, (name, count) in enumerate(channel_stats[:10]):
        log(f"   {i+1:2d}. {name[:20]:20s}: {count:3d} 个节目")
    
    if len(channel_stats) > 10:
        log(f"   ... 还有 {len(channel_stats)-10} 个频道")
    
    log("\n" + "=" * 50)
    log("✅ EPG抓取完成！")
    log("=" * 50)
    
    # 显示XML文件前几行作为示例
    try:
        with open(XML_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[:20]
            log("\n📄 XML文件前20行预览:")
            for i, line in enumerate(lines, 1):
                log(f"   {i:3d}: {line.rstrip()}")
    except:
        pass

if __name__ == "__main__":
    main()
