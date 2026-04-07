import requests
import json
import time

CHANNEL_API = "https://proxies.bein-mena-production.eu-west-2.tuc.red/proxy/listChannels"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}


# -----------------------------
# ① 获取频道列表（核心）
# -----------------------------
def get_channels():
    r = requests.get(CHANNEL_API, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()

    # 兼容不同结构
    if isinstance(data, list):
        return data

    for key in ["channels", "data", "result"]:
        if key in data and isinstance(data[key], list):
            return data[key]

    raise Exception("Unknown channel structure")


# -----------------------------
# ② 统一字段解析（防错位关键）
# -----------------------------
def normalize_channel(c):
    return {
        "name": c.get("name") or c.get("title") or c.get("channel"),
        "postid": str(c.get("postid") or c.get("epgId") or c.get("id"))
    }


# -----------------------------
# ③ 拉 EPG
# -----------------------------
def fetch_epg(postid):
    url = (
        "https://www.bein.com/en/epg-ajax-template/"
        f"?action=epg_fetch&offset=+0&category=sports"
        f"&serviceidentity=bein.net&mins=00&postid={postid}"
    )

    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None

    return r.text


# -----------------------------
# ④ 主流程
# -----------------------------
def main():
    channels_raw = get_channels()

    channels = []
    for c in channels_raw:
        nc = normalize_channel(c)
        if nc["name"] and nc["postid"]:
            channels.append(nc)

    print(f"\n[INFO] Channels loaded: {len(channels)}\n")

    for idx, ch in enumerate(channels, 1):
        print(f"{idx}. {ch['name']} -> postid={ch['postid']}")

        epg = fetch_epg(ch["postid"])

        if epg:
            print("   ✔ EPG OK")
        else:
            print("   ✖ EPG FAIL")

        time.sleep(0.2)  # 防止请求过快


if __name__ == "__main__":
    main()
