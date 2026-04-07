#!/usr/bin/env python3
import requests
import json
import time
from datetime import datetime

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")

def test_channel_batch(batch):
    """测试一批频道ID"""
    url = "https://nowplayer.now.com/tvguide/epglist"
    
    # 构建参数列表
    params = []
    for cid in batch:
        params.append(("channelIdList[]", cid))
    params.append(("day", "0"))
    params.append(("locale", "zh_HK"))
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list):
                for idx, programs in enumerate(data):
                    if idx < len(batch) and programs and len(programs) > 0:
                        cid = batch[idx]
                        yield cid, len(programs)
    except Exception as e:
        log(f"请求失败: {e}")
    time.sleep(0.3)

def main():
    log("开始发现NOWTV有效频道（三位数ID）...")
    
    # 生成所有三位数ID
    all_ids = [f"{i:03d}" for i in range(0, 1000)]
    
    batch_size = 10
    valid_channels = []
    
    for i in range(0, len(all_ids), batch_size):
        batch = all_ids[i:i+batch_size]
        log(f"测试批次: {batch}")
        
        for cid, count in test_channel_batch(batch):
            log(f"  发现有效频道: {cid}, 节目数: {count}")
            valid_channels.append((cid, count))
    
    log(f"\n发现完成！总共有效频道: {len(valid_channels)}")
    
    # 保存结果
    with open("valid_channels.json", "w") as f:
        json.dump(valid_channels, f, indent=2)
    
    # 只保存ID列表
    id_list = [cid for cid, _ in valid_channels]
    with open("channel_ids.json", "w") as f:
        json.dump(id_list, f, indent=2)
    
    log(f"有效频道ID列表已保存到 channel_ids.json")

if __name__ == "__main__":
    main()
