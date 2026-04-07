#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import requests
import xml.etree.ElementTree as ET
import gzip
from datetime import datetime
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
XML_FILE = os.path.join(BASE_DIR, "nowtv.xml")
GZ_FILE = os.path.join(BASE_DIR, "nowtv.xml.gz")
DEBUG_FILE = os.path.join(BASE_DIR, "debug_data.json")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def save_debug_data(data, filename=DEBUG_FILE):
    """保存调试数据"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"调试数据已保存到: {filename}")

def fetch_epg():
    """获取EPG数据，尝试多种方法"""
    
    # 方法1: 标准API
    urls = [
        "https://nowplayer.now.com/tvguide/epglist?day=1&lang=zh_HK",
        "https://nowplayer.now.com/tvguide/epglist?day=1&lang=zh",
        "https://nowplayer.now.com/tvguide/epglist?day=1",
        "https://nowplayer.now.com/tvguide/epg?day=1&lang=zh_HK",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }
    
    for i, url in enumerate(urls):
        try:
            log(f"尝试方法 {i+1}: {url}")
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            
            data = r.json()
            
            # 检查数据是否有效
            if data and isinstance(data, list):
                log(f"成功获取数据，数据组数: {len(data)}")
                
                # 保存原始数据用于调试
                save_debug_data(data, f"debug_epg_{i}.json")
                
                # 检查第一个节目
                if len(data) > 0 and isinstance(data[0], list) and len(data[0]) > 0:
                    first_program = data[0][0]
                    log(f"第一个节目: {json.dumps(first_program, ensure_ascii=False)}")
                    
                    # 检查字段
                    if "name" in first_program:
                        log(f"节目名称字段 'name' 的值: {first_program['name']}")
                
                return data
                
        except Exception as e:
            log(f"方法 {i+1} 失败: {e}")
            continue
    
    raise Exception("所有方法都失败了")

def parse_epg(data):
    """解析EPG数据"""
    epg = {}
    
    for group_idx, group in enumerate(data):
        if not isinstance(group, list):
            log(f"第 {group_idx} 组不是列表，跳过")
            continue
        
        log(f"处理第 {group_idx} 组，包含 {len(group)} 个节目")
        
        for prog_idx, p in enumerate(group):
            try:
                cid = str(p.get("channelId", ""))
                if not cid:
                    continue
                
                # 记录频道信息
                if cid not in epg:
                    log(f"发现新频道: {cid}")
                
                epg.setdefault(cid, []).append(p)
                
            except Exception as e:
                log(f"解析节目 {prog_idx} 时出错: {e}")
                continue
    
    log(f"总共解析了 {len(epg)} 个频道")
    return epg

def build_xml(config, epg):
    """构建XML"""
    tv = ET.Element("tv")
    
    channel_count = 0
    programme_count = 0
    
    for cid, info in config.items():
        channel_count += 1
        
        # channel
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = info.get("name", cid)
        
        if info.get("logo"):
            ET.SubElement(ch, "icon", {"src": info["logo"]})
        
        # programme
        programmes = epg.get(cid, [])
        log(f"频道 {info.get('name', cid)} 有 {len(programmes)} 个节目")
        
        for p in programmes:
            try:
                start = fmt(p["start"])
                stop = fmt(p["end"])
                
                # 尝试多种可能的名称字段
                title = p.get("name", "")
                
                # 如果有其他语言版本，优先使用中文
                for field in ["name_zh", "name_cn", "title_zh", "title_cn", "programmeName"]:
                    if field in p and p[field]:
                        title = p[field]
                        break
                
                title = title.strip()
                
                prog = ET.SubElement(tv, "programme", {
                    "channel": cid,
                    "start": start,
                    "stop": stop
                })
                
                ET.SubElement(prog, "title", {"lang": "zh"}).text = title
                programme_count += 1
                
            except Exception as e:
                log(f"构建节目时出错: {e}")
                continue
    
    log(f"总共处理了 {channel_count} 个频道，{programme_count} 个节目")
    return tv

def fmt(ts):
    return datetime.utcfromtimestamp(ts / 1000).strftime("%Y%m%d%H%M%S +0000")

def save_files(xml_root):
    xml_bytes = ET.tostring(xml_root, encoding="utf-8")
    
    with open(XML_FILE, "wb") as f:
        f.write(xml_bytes)
    
    with open(XML_FILE, "rb") as f:
        with gzip.open(GZ_FILE, "wb") as gz:
            gz.writelines(f)
    
    log(f"XML文件已保存: {XML_FILE} ({len(xml_bytes)} 字节)")
    log(f"GZ文件已保存: {GZ_FILE}")

def main():
    log("========== NOWTV EPG抓取开始 ==========")
    
    try:
        # 1. 加载配置
        config = load_config()
        log(f"配置中包含 {len(config)} 个频道")
        
        # 2. 获取EPG
        data = fetch_epg()
        epg = parse_epg(data)
        
        # 3. 构建XML
        xml_root = build_xml(config, epg)
        
        # 4. 保存文件
        save_files(xml_root)
        
        log("========== 完成 ==========")
        
    except Exception as e:
        log(f"主程序出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
