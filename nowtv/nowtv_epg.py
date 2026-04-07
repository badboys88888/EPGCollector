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
import sys
import traceback

# ================= 配置 ================= #
EPG_URL = "https://nowplayer.now.com/tvguide/epglist"

# 频道ID将从config.json中读取
CHANNEL_IDS = []
DAYS = 1
BATCH_SIZE = 5
SLEEP = 1.0
TIMEOUT = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
ERROR_LOG = os.path.join(BASE_DIR, "error.log")

# ================= 日志 ================= #
def log(msg, error=False):
    now = datetime.now().strftime("%H:%M:%S")
    log_msg = f"[{now}] {msg}"
    print(log_msg)
    
    if error:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")
            traceback.print_exc(file=f)

# ================= 检查基础环境 ================= #
def check_environment():
    """检查运行环境"""
    log("检查运行环境...")
    
    # 检查当前目录
    log(f"当前工作目录: {os.getcwd()}")
    log(f"脚本所在目录: {BASE_DIR}")
    
    # 检查配置文件
    if not os.path.exists(CONFIG_FILE):
        log(f"❌ 配置文件不存在: {CONFIG_FILE}", True)
        return False
    
    log(f"✅ 配置文件存在: {CONFIG_FILE}")
    
    # 检查网络连接
    try:
        response = requests.get("https://nowplayer.now.com", timeout=5)
        if response.status_code == 200:
            log("✅ 网络连接正常")
        else:
            log(f"⚠️ 网络连接异常: HTTP {response.status_code}")
    except Exception as e:
        log(f"❌ 网络连接失败: {e}", True)
        return False
    
    return True

# ================= 读取配置 ================= #
def load_config():
    """从config.json加载频道配置"""
    try:
        if not os.path.exists(CONFIG_FILE):
            log("❌ 错误: 未找到 config.json", True)
            return None
        
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        log(f"✅ 从 config.json 加载了 {len(config)} 个频道配置")
        
        # 显示前几个频道
        sample = list(config.items())[:5]
        for cid, info in sample:
            log(f"   📺 {cid}: {info.get('name', '未知')}")
        
        return config
        
    except json.JSONDecodeError as e:
        log(f"❌ config.json JSON格式错误: {e}", True)
        return None
    except Exception as e:
        log(f"❌ 读取 config.json 失败: {e}", True)
        return None

# ================= 时间转换 ================= #
def format_time(ts):
    """将时间戳转换为XMLTV格式"""
    try:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return dt.strftime("%Y%m%d%H%M%S +0000")
    except:
        return "19700101000000 +0000"

# ================= 获取中文标题 ================= #
def get_chinese_title(program):
    """从节目信息中提取中文标题"""
    if not isinstance(program, dict):
        return "Unknown"
    
    # 尝试多种可能的标题字段
    title_fields = [
        "name", "title", "programmeName", "displayName", 
        "localizedTitle", "nameZh", "titleZh"
    ]
    
    for field in title_fields:
        if field in program and program[field]:
            value = program[field]
            
            # 如果字段值是字典，尝试获取中文
            if isinstance(value, dict):
                for lang in ["zh", "zh-CN", "zh-HK", "zh_TW", "cn"]:
                    if lang in value and value[lang]:
                        return str(value[lang]).strip()
            
            # 如果直接是字符串
            elif isinstance(value, str) and value.strip():
                return value.strip()
    
    return "Unknown"

# ================= 请求EPG数据 ================= #
def fetch_epg_batch(batch_channels, day):
    """批量获取频道EPG数据"""
    
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
        log(f"  正在获取 {len(batch_channels)} 个频道的EPG (Day {day})...")
        
        response = requests.get(
            EPG_URL, 
            params=params, 
            headers=headers, 
            timeout=TIMEOUT
        )
        
        if response.status_code != 200:
            log(f"  ❌ HTTP {response.status_code}: 请求失败", True)
            return None
        
        data = response.json()
        
        if not isinstance(data, list):
            log(f"  ⚠️ 返回数据格式异常: {type(data)}", True)
            return None
        
        return data
        
    except requests.exceptions.Timeout:
        log(f"  ⏰ 请求超时 (Day {day})", True)
        return None
    except Exception as e:
        log(f"  ❌ 请求异常: {e}", True)
        return None

# ================= 生成和保存XML ================= #
def generate_and_save_xml(config, all_epg_data):
    """生成并保存XML文件"""
    
    if not all_epg_data:
        log("❌ 没有EPG数据，无法生成XML", True)
        return False
    
    try:
        # 创建XML根元素
        tv_element = ET.Element("tv")
        
        # 添加频道定义
        log("添加频道定义到XML...")
        channel_count = 0
        for channel_id, channel_info in config.items():
            channel_name = channel_info.get("name", f"频道{channel_id}")
            
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
                    
                    programme_element = ET.SubElement(tv_element, "programme", {
                        "channel": channel_name,
                        "start": start_str,
                        "stop": end_str
                    })
                    
                    ET.SubElement(programme_element, "title").text = title
                    program_count += 1
                    
                except Exception as e:
                    log(f"  ⚠️ 处理单个节目时出错: {e}", True)
                    continue
        
        log(f"✅ 添加了 {program_count} 个节目信息")
        
        if program_count == 0:
            log("❌ 没有有效的节目信息，XML将为空", True)
            return False
        
        # 生成XML字符串
        log("生成XML字符串...")
        xml_string = ET.tostring(tv_element, encoding="utf-8", xml_declaration=True)
        
        # 美化XML
        log("美化XML格式...")
        # 修复：使用正确的minidom引用
        dom = minidom.parseString(xml_string)  # 修复这里的bug
        pretty_xml = dom.toprettyxml(indent="  ", encoding="utf-8")
        
        # 保存XML文件
        log(f"保存XML文件到: {XML_FILE}")
        with open(XML_FILE, "wb") as f:
            f.write(pretty_xml)
        
        log(f"✅ XML文件保存成功: {XML_FILE}")
        log(f"   文件大小: {len(pretty_xml):,} 字节")
        
        # 保存GZ文件
        log(f"保存GZ文件到: {GZ_FILE}")
        with open(XML_FILE, "rb") as f_in:
            with gzip.open(GZ_FILE, "wb") as f_out:
                f_out.writelines(f_in)
        
        log(f"✅ GZ文件保存成功: {GZ_FILE}")
        
        # 验证文件是否存在
        if os.path.exists(XML_FILE) and os.path.getsize(XML_FILE) > 0:
            log(f"✅ 验证: XML文件存在，大小: {os.path.getsize(XML_FILE):,} 字节")
        else:
            log("❌ 验证失败: XML文件不存在或为空", True)
            return False
            
        if os.path.exists(GZ_FILE) and os.path.getsize(GZ_FILE) > 0:
            log(f"✅ 验证: GZ文件存在，大小: {os.path.getsize(GZ_FILE):,} 字节")
        else:
            log("❌ 验证失败: GZ文件不存在或为空", True)
            return False
        
        return True
        
    except Exception as e:
        log(f"❌ 生成/保存XML时出错: {e}", True)
        return False

# ================= 主程序 ================= #
def main():
    log("=" * 60)
    log("NOWTV EPG抓取工具 - 开始运行")
    log("=" * 60)
    
    # 0. 清理旧的错误日志
    if os.path.exists(ERROR_LOG):
        os.remove(ERROR_LOG)
    
    # 1. 检查环境
    if not check_environment():
        log("❌ 环境检查失败，程序退出", True)
        return
    
    # 2. 加载配置
    config = load_config()
    if not config:
        log("❌ 加载配置失败，程序退出", True)
        return
    
    # 3. 从config中提取频道ID列表
    channel_ids = list(config.keys())
    log(f"📊 从config.json获取到 {len(channel_ids)} 个频道ID")
    
    # 4. 测试抓取少量频道
    test_channels = channel_ids[:10]  # 只测试前10个频道
    log(f"🔧 测试模式: 只抓取前 {len(test_channels)} 个频道")
    
    # 5. 开始抓取EPG数据
    log("\n" + "=" * 60)
    log("开始抓取EPG数据")
    log("=" * 60)
    
    all_epg_data = {}
    
    for day in range(DAYS):
        log(f"\n📅 处理 Day {day} 的节目表")
        log("-" * 40)
        
        # 分批处理测试频道
        for i in range(0, len(test_channels), BATCH_SIZE):
            batch = test_channels[i:i + BATCH_SIZE]
            log(f"  处理批次 {i//BATCH_SIZE + 1}: 频道 {batch}")
            
            # 获取这批频道的EPG数据
            epg_data = fetch_epg_batch(batch, day)
            
            if epg_data and isinstance(epg_data, list):
                for idx, channel_programs in enumerate(epg_data):
                    if idx >= len(batch):
                        break
                    
                    channel_id = batch[idx]
                    
                    if not channel_programs or not isinstance(channel_programs, list):
                        log(f"  ⚠️ 频道 {channel_id} 无节目数据")
                        continue
                    
                    if channel_id not in all_epg_data:
                        all_epg_data[channel_id] = []
                    
                    all_epg_data[channel_id].extend(channel_programs)
                    
                    channel_name = config.get(channel_id, {}).get("name", channel_id)
                    log(f"  ✅ {channel_id} ({channel_name}): {len(channel_programs)}个节目")
            
            time.sleep(SLEEP)
    
    # 6. 检查是否抓取到数据
    if not all_epg_data:
        log("❌ 没有抓取到任何EPG数据，程序退出", True)
        return
    
    log(f"\n📈 抓取统计: {len(all_epg_data)} 个频道有数据")
    for channel_id, programs in all_epg_data.items():
        channel_name = config.get(channel_id, {}).get("name", channel_id)
        log(f"   {channel_id} ({channel_name}): {len(programs)} 个节目")
    
    # 7. 生成和保存XML
    log("\n" + "=" * 60)
    log("生成和保存XML/GZ文件")
    log("=" * 60)
    
    success = generate_and_save_xml(config, all_epg_data)
    
    if success:
        log("\n" + "=" * 60)
        log("✅ EPG抓取和文件生成完成！")
        log("=" * 60)
        
        # 显示文件信息
        log(f"\n📁 生成的文件:")
        log(f"  XML文件: {XML_FILE}")
        log(f"  GZ文件: {GZ_FILE}")
        log(f"  错误日志: {ERROR_LOG}")
        
        # 显示XML文件前几行
        try:
            with open(XML_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[:10]
                log("\n📄 XML文件前10行预览:")
                for i, line in enumerate(lines, 1):
                    log(f"  {i:2d}: {line.rstrip()}")
        except Exception as e:
            log(f"⚠️ 无法读取XML文件预览: {e}")
    else:
        log("\n" + "=" * 60)
        log("❌ EPG抓取失败，请检查错误日志")
        log("=" * 60)
    
    # 8. 检查是否有错误日志
    if os.path.exists(ERROR_LOG) and os.path.getsize(ERROR_LOG) > 0:
        log(f"\n⚠️ 发现错误日志，请查看: {ERROR_LOG}")
        with open(ERROR_LOG, "r", encoding="utf-8") as f:
            errors = f.read()
            if errors:
                log("错误内容:")
                log(errors[:500] + ("..." if len(errors) > 500 else ""))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n⚠️ 程序被用户中断")
    except Exception as e:
        log(f"❌ 程序发生未捕获的异常: {e}", True)
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            traceback.print_exc(file=f)
