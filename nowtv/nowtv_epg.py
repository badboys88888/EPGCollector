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
import traceback

# ================= 配置 ================= #
EPG_URL = "https://nowplayer.now.com/tvguide/epglist"

# 频道ID将从config.json中读取
CHANNEL_IDS = []  # 初始为空，会从config.json加载
DAYS = 2
BATCH_SIZE = 5  # 减少批量大小，避免超时
SLEEP = 0.5  # 增加等待时间
MAX_RETRIES = 3
TIMEOUT = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "epg_error.log")

# ================= 日志 ================= #
def log(msg, to_file=False):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{now}] {msg}"
    print(log_msg)
    
    if to_file:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")

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
        return config
        
    except Exception as e:
        log(f"❌ 读取 config.json 失败: {e}", True)
        return None

# ================= 请求EPG数据（带重试） ================= #
def fetch_epg_batch_with_retry(batch_channels, day, retry_count=0):
    """批量获取频道EPG数据，带重试机制"""
    
    params = []
    for channel_id in batch_channels:
        params.append(("channelIdList[]", channel_id))
    params.append(("day", str(day)))
    params.append(("locale", "zh_HK"))
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }
    
    try:
        log(f"  第{retry_count+1}次尝试获取 {len(batch_channels)} 个频道的EPG (Day {day})...")
        
        response = requests.get(
            EPG_URL, 
            params=params, 
            headers=headers, 
            timeout=TIMEOUT
        )
        
        if response.status_code != 200:
            error_msg = f"  ❌ HTTP {response.status_code}: {response.text[:200]}"
            log(error_msg, True)
            
            if retry_count < MAX_RETRIES - 1:
                wait_time = 2 * (retry_count + 1)  # 指数退避
                log(f"  ⏰ 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                return fetch_epg_batch_with_retry(batch_channels, day, retry_count + 1)
            return None
        
        data = response.json()
        
        if not isinstance(data, list):
            error_msg = f"  ⚠️ 返回数据格式异常: {type(data)}"
            log(error_msg, True)
            return None
        
        return data
        
    except requests.exceptions.Timeout:
        error_msg = f"  ⏰ 请求超时 (Day {day}, 频道: {batch_channels})"
        log(error_msg, True)
        
        if retry_count < MAX_RETRIES - 1:
            wait_time = 3 * (retry_count + 1)
            log(f"  ⏰ 等待 {wait_time} 秒后重试...")
            time.sleep(wait_time)
            return fetch_epg_batch_with_retry(batch_channels, day, retry_count + 1)
        return None
        
    except Exception as e:
        error_msg = f"  ❌ 请求异常: {e}"
        log(error_msg, True)
        
        if retry_count < MAX_RETRIES - 1:
            wait_time = 2 * (retry_count + 1)
            log(f"  ⏰ 等待 {wait_time} 秒后重试...")
            time.sleep(wait_time)
            return fetch_epg_batch_with_retry(batch_channels, day, retry_count + 1)
        return None

# ================= 时间转换 ================= #
def format_time(ts):
    try:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return dt.strftime("%Y%m%d%H%M%S +0000")
    except:
        return "19700101000000 +0000"

# ================= 获取中文标题 ================= #
def get_chinese_title(program):
    """从节目信息中提取中文标题"""
    try:
        if not isinstance(program, dict):
            return "未知节目"
        
        # 尝试多种可能的字段
        for field in ["nameZh", "titleZh", "name_cn", "title_cn", "name_zh", "title_zh"]:
            if field in program and program[field]:
                value = str(program[field]).strip()
                if value:
                    return value
        
        # 尝试多语言字段
        for field in ["name", "title"]:
            if field in program and isinstance(program[field], dict):
                for lang in ["zh", "zh_CN", "zh_HK", "zh_TW", "cn"]:
                    if lang in program[field] and program[field][lang]:
                        value = str(program[field][lang]).strip()
                        if value:
                            return value
        
        # 尝试直接字段
        for field in ["name", "title", "programmeName", "displayName", "localizedTitle"]:
            if field in program and program[field]:
                value = str(program[field]).strip()
                if value:
                    return value
        
        return "未知节目"
    except:
        return "未知节目"

# ================= 主程序 ================= #
def main():
    try:
        log("=" * 50)
        log("NOWTV EPG抓取工具 - 开始运行")
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
        
        all_epg_data = {}
        
        for day in range(DAYS):
            log(f"\n📅 处理 Day {day} 的节目表")
            log("-" * 40)
            
            day_epg_count = 0
            day_channel_count = 0
            
            # 分批处理频道
            for i in range(0, len(channel_ids), BATCH_SIZE):
                batch = channel_ids[i:i + BATCH_SIZE]
                log(f"  处理批次 {i//BATCH_SIZE + 1}/{(len(channel_ids)+BATCH_SIZE-1)//BATCH_SIZE}: 频道 {batch}")
                
                # 获取这批频道的EPG数据
                epg_data = fetch_epg_batch_with_retry(batch, day)
                
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
                
                # 请求间隔
                time.sleep(SLEEP)
            
            log(f"📈 Day {day} 完成: {day_channel_count}个频道, {day_epg_count}个节目")
        
        # 4. 检查是否有数据
        if not all_epg_data:
            log("❌ 没有获取到任何EPG数据")
            return
        
        # 5. 生成XMLTV格式
        log("\n" + "=" * 50)
        log("生成XMLTV格式文件")
        log("=" * 50)
        
        # 创建XML根元素
        tv_element = ET.Element("tv")
        
        # 添加频道定义
        log("添加频道定义...")
        for channel_id, channel_info in config.items():
            channel_element = ET.SubElement(tv_element, "channel", {"id": channel_id})
            
            # 频道名称
            display_name = ET.SubElement(channel_element, "display-name")
            display_name.text = channel_info.get("name", f"频道{channel_id}")
            
            # 频道图标
            logo = channel_info.get("logo", "")
            if logo:
                ET.SubElement(channel_element, "icon", {"src": logo})
        
        # 添加节目信息
        log("添加节目信息...")
        total_programs = 0
        
        for channel_id, programs in all_epg_data.items():
            if not programs:
                continue
            
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
                    
                    # 创建programme元素
                    programme_element = ET.SubElement(tv_element, "programme", {
                        "channel": channel_id,
                        "start": start_str,
                        "stop": end_str
                    })
                    
                    # 添加标题
                    title_element = ET.SubElement(programme_element, "title")
                    title_element.text = title
                    
                    total_programs += 1
                    
                except Exception as e:
                    log(f"  ⚠️ 处理节目时出错: {e}")
                    continue
        
        # 6. 保存XML文件
        log("\n" + "=" * 50)
        log("保存文件")
        log("=" * 50)
        
        # 生成格式化的XML
        xml_bytes = ET.tostring(tv_element, encoding="utf-8", xml_declaration=True)
        
        # 保存XML文件
        with open(XML_FILE, "wb") as f:
            f.write(xml_bytes)
        
        log(f"✅ XML文件保存成功: {XML_FILE}")
        log(f"   文件大小: {len(xml_bytes):,} 字节")
        
        # 保存压缩的GZ文件
        with open(XML_FILE, "rb") as f_in:
            with gzip.open(GZ_FILE, "wb") as f_out:
                f_out.writelines(f_in)
        
        log(f"✅ GZ文件保存成功: {GZ_FILE}")
        
        # 7. 显示统计信息
        log("\n" + "=" * 50)
        log("抓取完成 - 统计信息")
        log("=" * 50)
        log(f"📺 频道总数: {len(all_epg_data)}")
        log(f"🎬 节目总数: {total_programs}")
        
        log("\n" + "=" * 50)
        log("✅ EPG抓取完成！")
        log("=" * 50)
        
    except Exception as e:
        log(f"❌ 程序发生未捕获的异常: {e}", True)
        log(traceback.format_exc(), True)
        raise

if __name__ == "__main__":
    main()
