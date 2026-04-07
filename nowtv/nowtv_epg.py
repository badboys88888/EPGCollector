#!/usr/bin/env python3
import requests
import json

def test_single_channel(channel_id):
    url = "https://nowplayer.now.com/tvguide/epglist"
    params = {
        "channelIdList[]": str(channel_id),
        "day": "0",
        "locale": "zh_HK"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"
    }
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list) and len(data) > 0:
                programs = data[0] if data[0] else []
                return len(programs) > 0
    except:
        pass
    return False

# 测试几个频道
for cid in [1, 2, 3, 10, 20, 30, 100, 101, 200]:
    has_data = test_single_channel(cid)
    print(f"频道 {cid:3d}: {'有数据' if has_data else '无数据'}")
