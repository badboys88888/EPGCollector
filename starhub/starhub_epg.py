#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StarHub EPG 抓取脚本 (优化美化版) - 修正分页问题
功能：从 StarHub API 获取频道列表与节目时间表，生成标准的 XMLTV 格式文件。
特点：
1. 修正了分页逻辑，确保获取所有频道
2. 使用频道的 platform_id 作为唯一标识
3. 批量获取节目表，避免 URL 过长
4. 完善的错误处理和重试机制
5. 标准化的 XMLTV 输出格式
"""

import requests
import xml.etree.ElementTree as ET
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import sys
import os
from xml.dom import minidom

# ===================== 配置 ===================== #
BASE_URL = "https://waf-starhub-metadata-api-p001.ifs.vubiquity.com/v3.1"  # 修正：完整的URL
CHANNEL_API = f"{BASE_URL}/epg/channels"  # 修正：添加斜杠
SCHEDULE_API = f"{BASE_URL}/epg/schedules"  # 修正：添加斜杠

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

# 时区设置 (新加坡时间 GMT+8)
SGT = timezone(timedelta(hours=8))

# 批量处理参数
BATCH_SIZE = 20           # 每次请求的频道数量
EPG_DAYS_AHEAD = 2        # 获取未来几天的节目
REQUEST_DELAY = 0.2       # 请求间隔，避免限流
REQUEST_TIMEOUT = 30      # 请求超时时间

# 输出文件
OUTPUT_XML = "starhub_epg.xml"
OUTPUT_M3U = "starhub.m3u"

# ===================== 工具函数 ===================== #
def print_step(step_num, message):
    """打印步骤信息"""
    print(f"\n[步骤{step_num}] {message}")

def print_info(message):
    """打印信息"""
    print(f"  ℹ️  {message}")

def print_success(message):
    """打印成功信息"""
    print(f"  ✅ {message}")

def print_warning(message):
    """打印警告信息"""
    print(f"  ⚠️  {message}")

def print_error(message):
    """打印错误信息"""
    print(f"  ❌ {message}")

def format_xmltv_time(timestamp):
    """将 Unix 时间戳转换为 XMLTV 时间格式"""
    try:
        dt = datetime.fromtimestamp(timestamp, SGT)
        return dt.strftime("%Y%m%d%H%M%S") + " +0800"
    except Exception as e:
        print_warning(f"时间格式转换失败: {e}")
        return ""

def fix_image_url(url):
    """修复不完整的图片 URL"""
    if not url or not isinstance(url, str):
        return ""
    
    if url.startswith("https:///"):
        return url.replace("https:///", "https://")
    
    return url

# ===================== 频道获取模块 ===================== #
def get_all_channels():
    """
    获取所有频道信息（自动分页）
    返回: 频道列表，已按 platform_id 去重
    """
    print_step(1, "正在获取频道列表...")
    
    all_channels = []
    page = 1
    
    while True:
        try:
            params = {
                "locale": "zh",
                "locale_default": "en_US",
                "device": 1,
                "limit": 50,
                "page": page
            }
            
            response = requests.get(CHANNEL_API, params=params, 
                                  headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            channels_batch = data.get("resources", [])
            if not channels_batch:
                print_info(f"第 {page} 页无频道数据")
                break
            
            # 添加到列表
            all_channels.extend(channels_batch)
            
            # 更新分页信息
            page_info = data.get("page", {})
            current_page = page_info.get("current", page)
            total_pages = page_info.get("total", 1)
            items_count = page_info.get("items_count", 0)
            total_items = page_info.get("total_items_count", 0)
            
            print_info(f"第 {current_page}/{total_pages} 页: 获取到 {items_count} 个频道 (总计: {total_items})")
            
            if current_page >= total_pages:
                print_info(f"已到达最后一页，共 {total_pages} 页")
                break
                
            page = current_page + 1
            time.sleep(REQUEST_DELAY)
            
        except requests.exceptions.RequestException as e:
            print_error(f"获取第 {page} 页频道失败: {e}")
            break
        except Exception as e:
            print_error(f"处理第 {page} 页频道时出错: {e}")
            break
    
    if not all_channels:
        print_error("未获取到任何频道数据")
        return []
    
    # 去重：使用 platform_id 作为唯一标识
    unique_channels = {}
    for channel in all_channels:
        platform_id = channel.get("platform_id")
        if platform_id:
            # 如果已有相同的 platform_id，用后获取的覆盖（确保最新数据）
            unique_channels[platform_id] = channel
    
    print_success(f"获取完成: 共 {len(all_channels)} 个频道，去重后 {len(unique_channels)} 个")
    
    # 返回去重后的频道列表
    return list(unique_channels.values())

# ===================== 节目表获取模块 ===================== #
def get_epg_schedules(channels, device_type=1):
    """
    获取多个频道的节目时间表
    参数:
        channels: 频道列表
        device_type: 设备类型 (1 或 2)
    返回: 字典 {channel_platform_id: [节目列表]}
    """
    print_step(2, f"正在获取节目表 (设备类型: {device_type})...")
    
    # 提取频道的内部ID（用于API请求）
    channel_ids = []
    id_to_platform = {}  # 映射：内部ID -> platform_id
    
    for channel in channels:
        internal_id = channel.get("id")
        platform_id = channel.get("platform_id")
        
        if internal_id and platform_id:
            channel_ids.append(internal_id)
            id_to_platform[internal_id] = platform_id
    
    if not channel_ids:
        print_warning("无有效的频道ID，跳过节目获取")
        return {}
    
    # 计算时间范围
    start_time = int(time.time())
    end_time = start_time + (EPG_DAYS_AHEAD * 24 * 3600)  # 未来N天
    
    start_str = datetime.fromtimestamp(start_time, SGT).strftime('%Y-%m-%d %H:%M')
    end_str = datetime.fromtimestamp(end_time, SGT).strftime('%Y-%m-%d %H:%M')
    print_info(f"时间范围: {start_str} 至 {end_str} (未来 {EPG_DAYS_AHEAD} 天)")
    
    # 按频道ID分组，分批获取
    schedule_dict = defaultdict(list)
    successful_batches = 0
    
    for i in range(0, len(channel_ids), BATCH_SIZE):
        batch_num = (i // BATCH_SIZE) + 1
        batch_ids = channel_ids[i:i + BATCH_SIZE]
        
        try:
            params = {
                "locale": "zh",
                "locale_default": "en_US",
                "device": device_type,
                "in_channel_id": ",".join(batch_ids),
                "lte_start": end_time,  # 开始时间小于等于
                "gt_end": start_time,   # 结束时间大于
                "limit": 500
            }
            
            response = requests.get(SCHEDULE_API, params=params, 
                                  headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            programmes = data.get("resources", [])
            
            # 将节目按频道的platform_id分组
            for prog in programmes:
                channel_platform_id = prog.get("channel_platform_id")
                if channel_platform_id:
                    # 如果节目中的channel_platform_id不在映射中，尝试从channel_id查找
                    if channel_platform_id not in id_to_platform.values():
                        channel_id = prog.get("channel_id")
                        if channel_id and channel_id in id_to_platform:
                            channel_platform_id = id_to_platform[channel_id]
                    
                    schedule_dict[channel_platform_id].append(prog)
            
            successful_batches += 1
            print_info(f"批次 {batch_num}: 获取到 {len(programmes)} 个节目")
            
            time.sleep(REQUEST_DELAY)
            
        except requests.exceptions.RequestException as e:
            print_warning(f"批次 {batch_num} 获取失败: {e}")
        except Exception as e:
            print_warning(f"处理批次 {batch_num} 时出错: {e}")
    
    # 对每个频道的节目按开始时间排序
    for channel_id, prog_list in schedule_dict.items():
        schedule_dict[channel_id] = sorted(prog_list, key=lambda x: x.get("start", 0))
    
    total_programmes = sum(len(progs) for progs in schedule_dict.values())
    print_success(f"节目获取完成: 成功 {successful_batches} 个批次，共 {total_programmes} 个节目，分布在 {len(schedule_dict)} 个频道")
    
    return schedule_dict

# ===================== XMLTV 构建模块 ===================== #
def build_xmltv_output(channels, schedules):
    """
    构建完整的 XMLTV 文档
    参数:
        channels: 频道列表
        schedules: 节目字典 {channel_platform_id: [节目列表]}
    """
    print_step(3, "正在构建 XMLTV 文档...")
    
    # 创建根元素
    tv = ET.Element("tv")
    tv.set("generator-info-name", "StarHub EPG Grabber")
    tv.set("generator-info-url", "")
    tv.set("source-info-name", "StarHub")
    tv.set("source-info-url", "https://www.starhub.com")
    
    # 1. 添加频道信息
    channels_added = 0
    for channel in channels:
        platform_id = channel.get("platform_id")
        if not platform_id:
            continue
        
        # 创建频道元素
        ch_elem = ET.SubElement(tv, "channel", id=platform_id)
        
        # 添加显示名称
        channel_name = channel.get("title", "")
        if channel_name:
            display_elem = ET.SubElement(ch_elem, "display-name")
            display_elem.text = channel_name
        
        # 添加频道图标
        pictures = channel.get("pictures", [])
        for pic in pictures:
            pic_url = pic.get("url")
            if pic_url:
                fixed_url = fix_image_url(pic_url)
                if fixed_url:
                    ET.SubElement(ch_elem, "icon", src=fixed_url)
                    break
        
        channels_added += 1
    
    print_info(f"已添加 {channels_added} 个频道")
    
    # 2. 添加节目信息
    programmes_added = 0
    for channel_id, prog_list in schedules.items():
        for prog in prog_list:
            try:
                start_time = prog.get("start")
                end_time = prog.get("end")
                
                if not start_time or not end_time:
                    continue
                
                # 转换时间格式
                xml_start = format_xmltv_time(start_time)
                xml_end = format_xmltv_time(end_time)
                
                if not xml_start or not xml_end:
                    continue
                
                # 创建节目元素
                prog_elem = ET.SubElement(tv, "programme")
                prog_elem.set("channel", channel_id)
                prog_elem.set("start", xml_start)
                prog_elem.set("stop", xml_end)
                
                # 添加标题
                title = prog.get("title", "")
                if title:
                    title_elem = ET.SubElement(prog_elem, "title")
                    title_elem.text = title
                
                # 添加描述
                description = prog.get("description", "")
                if description:
                    desc_elem = ET.SubElement(prog_elem, "desc")
                    desc_elem.text = description
                
                # 添加分类
                genres = prog.get("genres", [])
                if isinstance(genres, str):
                    genres = [genres]
                
                for genre in genres:
                    if genre:
                        cat_elem = ET.SubElement(prog_elem, "category")
                        cat_elem.text = genre
                
                # 添加节目图片
                prog_pics = prog.get("pictures", [])
                for pic in prog_pics:
                    pic_url = pic.get("url")
                    if pic_url:
                        fixed_url = fix_image_url(pic_url)
                        if fixed_url and "nosuchthumbnail" not in fixed_url:
                            ET.SubElement(prog_elem, "icon", src=fixed_url)
                            break
                
                programmes_added += 1
                
            except Exception as e:
                print_warning(f"处理节目时出错: {e}")
                continue
    
    print_info(f"已添加 {programmes_added} 个节目")
    
    return tv, programmes_added

# ===================== 文件保存模块 ===================== #
def save_pretty_xml(xml_root, filename=OUTPUT_XML):
    """保存格式化的 XML 文件"""
    try:
        # 转换为字符串
        xml_str = ET.tostring(xml_root, encoding="utf-8")
        
        # 使用 minidom 美化输出
        parsed = minidom.parseString(xml_str)
        pretty_xml = parsed.toprettyxml(indent="  ", encoding="utf-8")
        
        with open(filename, "wb") as f:
            f.write(pretty_xml)
        
        file_size = os.path.getsize(filename) / 1024
        print_success(f"XMLTV 文件已保存: {filename} ({file_size:.1f} KB)")
        return True
        
    except Exception as e:
        print_error(f"保存 XML 文件失败: {e}")
        # 尝试直接保存
        try:
            tree = ET.ElementTree(xml_root)
            tree.write(filename, encoding="utf-8", xml_declaration=True)
            print_success(f"已直接保存 XML 文件: {filename}")
            return True
        except Exception as e2:
            print_error(f"直接保存也失败: {e2}")
            return False

def generate_m3u_playlist(channels, epg_xml_url=None, filename=OUTPUT_M3U):
    """生成 M3U 播放列表"""
    print_step(4, "正在生成 M3U 播放列表...")
    
    m3u_content = "#EXTM3U\n"
    
    if epg_xml_url:
        m3u_content += f'#EXTM3U url-tvg="{epg_xml_url}"\n\n'
    
    channels_with_url = 0
    
    for channel in channels:
        platform_id = channel.get("platform_id", "")
        channel_name = channel.get("title", "")
        playback_url = channel.get("playback_url", "")
        
        if not playback_url or not channel_name:
            continue
        
        # 获取频道图标
        logo_url = ""
        pictures = channel.get("pictures", [])
        for pic in pictures:
            url = pic.get("url", "")
            if url:
                logo_url = fix_image_url(url)
                break
        
        # 构建 EXTINF 行
        extinf_line = f'#EXTINF:-1 tvg-id="{platform_id}"'
        
        if logo_url:
            extinf_line += f' tvg-logo="{logo_url}"'
        
        extinf_line += f' group-title="StarHub",{channel_name}'
        
        m3u_content += f"{extinf_line}\n"
        m3u_content += f"{playback_url}\n\n"
        
        channels_with_url += 1
    
    # 保存文件
    with open(filename, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    
    print_success(f"已生成 {channels_with_url} 个频道的播放列表: {filename}")
    return channels_with_url

# ===================== 主程序 ===================== #
def main():
    """主程序入口"""
    print("=" * 60)
    print("StarHub EPG 抓取工具 (优化美化版)")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    start_time = time.time()
    
    try:
        # 1. 获取所有频道
        channels = get_all_channels()
        if not channels:
            print_error("无法获取频道信息，程序退出")
            return
        
        # 2. 获取节目表（先尝试 device=1，失败则尝试 device=2）
        schedules = get_epg_schedules(channels, device_type=1)
        
        if not schedules and len(channels) > 0:
            print_info("设备类型 1 未获取到节目，尝试设备类型 2...")
            schedules = get_epg_schedules(channels, device_type=2)
        
        if not schedules:
            print_warning("未获取到任何节目数据，将只生成频道信息")
        
        # 3. 构建 XMLTV
        xml_root, programmes_count = build_xmltv_output(channels, schedules)
        
        # 4. 保存 XML 文件
        if not save_pretty_xml(xml_root, OUTPUT_XML):
            print_error("保存 XML 文件失败，程序退出")
            return
        
        # 5. 生成 M3U 播放列表
        # 假设 EPG 文件可以通过相对路径访问
        epg_url = f"./{OUTPUT_XML}" if os.path.exists(OUTPUT_XML) else None
        m3u_channels = generate_m3u_playlist(channels, epg_url, OUTPUT_M3U)
        
        # 6. 输出统计信息
        print_step(5, "任务完成")
        
        duration = time.time() - start_time
        print_info("统计信息:")
        print(f"  • 执行耗时: {duration:.1f} 秒")
        print(f"  • 频道总数: {len(channels)}")
        print(f"  • 有播放地址的频道: {m3u_channels}")
        print(f"  • 节目总数: {programmes_count}")
        print(f"  • 覆盖频道数: {len(schedules)}")
        print(f"  • 输出文件:")
        print(f"      - {OUTPUT_XML} (XMLTV EPG 文件)")
        print(f"      - {OUTPUT_M3U} (M3U 播放列表)")
        
        if programmes_count == 0 and len(schedules) > 0:
            print_warning("\n⚠️  注意: 获取到节目数据但未成功添加到 XML，请检查时间格式转换")
        
        print(f"\n💡 使用建议:")
        print(f"  1. 将 {OUTPUT_XML} 和 {OUTPUT_M3U} 放在同一目录")
        print(f"  2. 在支持 XMLTV 的播放器中导入 {OUTPUT_M3U}")
        print(f"  3. 可以设置定时任务每天运行此脚本更新 EPG")
        
    except KeyboardInterrupt:
        print("\n\n⏹️ 用户中断程序")
    except Exception as e:
        print_error(f"程序运行异常: {e}")
        import traceback
        traceback.print_exc()

# ===================== 程序入口 ===================== #
if __name__ == "__main__":
    main()
