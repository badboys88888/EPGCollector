#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import time
from datetime import datetime

EPG_URL = "https://nowplayer.now.com/tvguide/epglist"

# 👉 你可以先用扫描模式
CHANNEL_IDS = [str(i) for i in range(1, 300)]

DAYS = 2
BATCH_SIZE = 10
SLEEP = 0.3
RETRY = 2

OUTPUT_FILE = "now_epg.json"


# ================= 日志函数 ================= #
def log(msg, level="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] [{level}] {msg}")


# ================= 请求函数 ================= #
def fetch_epg(batch, day):
    params = []
    for cid in batch:
        params.append(("channelIdList[]", cid))
    params.append(("day", str(day)))

    for attempt in range(RETRY + 1):
        try:
            r = requests.get(EPG_URL, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log(f"请求失败 day={day} batch={batch} attempt={attempt} 错误={e}", "WARN")
            time.sleep(1)

    return []


# ================= 主程序 ================= #
def main():
    all_data = {}
    valid_channels = set()

    log(f"开始抓取，共频道扫描: {len(CHANNEL_IDS)}")

    for day in range(DAYS):
        log(f"====== DAY {day} ======")

        for i in range(0, len(CHANNEL_IDS), BATCH_SIZE):
            batch = CHANNEL_IDS[i:i+BATCH_SIZE]

            log(f"请求频道: {batch}")

            data = fetch_epg(batch, day)

            if not data:
                log(f"返回空数据: {batch}", "ERROR")
                continue

            for idx, programs in enumerate(data):
                if idx >= len(batch):
                    continue

                cid = batch[idx]

                if not programs:
                    log(f"频道 {cid} 无节目", "DEBUG")
                    continue

                valid_channels.add(cid)

                if cid not in all_data:
                    all_data[cid] = []

                all_data[cid].extend(programs)

                log(f"频道 {cid} 获取 {len(programs)} 条节目")

            time.sleep(SLEEP)

    log(f"有效频道数: {len(valid_channels)}")

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    log(f"保存完成: {OUTPUT_FILE}", "OK")


if __name__ == "__main__":
    main()
