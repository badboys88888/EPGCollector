import json
import time
from playwright.sync_api import sync_playwright


URL = "https://www.bein.com/en/tv-guide/"


# ----------------------------
# ① 抓取所有 XHR 数据
# ----------------------------
def grab_network_data():
    data_store = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )

        page = browser.new_page()

        # 捕获所有 response
        def handle_response(response):
            try:
                url = response.url.lower()

                # 重点：抓 epg / channel / guide
                if any(k in url for k in ["epg", "channel", "guide"]):
                    try:
                        data = response.json()
                        data_store.append(data)
                        print(f"[XHR] Captured: {url}")
                    except:
                        pass
            except:
                pass

        page.on("response", handle_response)

        print("[INFO] Loading TV Guide...")
        page.goto(URL, timeout=60000)

        # 等 JS 完全加载
        page.wait_for_timeout(12000)

        browser.close()

    return data_store


# ----------------------------
# ② 提取频道列表（通用结构扫描）
# ----------------------------
def extract_channels(all_data):

    def find_channels(obj):
        if isinstance(obj, dict):

            for k, v in obj.items():

                lk = k.lower()

                # 常见字段
                if lk in ["channels", "channel_list", "items", "data"]:
                    if isinstance(v, list) and len(v) > 0:
                        # 判断是否像频道
                        if isinstance(v[0], dict):
                            if any(x in v[0] for x in ["name", "title", "id"]):
                                return v

                res = find_channels(v)
                if res:
                    return res

        elif isinstance(obj, list):
            for i in obj:
                res = find_channels(i)
                if res:
                    return res

        return None

    for d in all_data:
        channels = find_channels(d)
        if channels:
            return channels

    return []


# ----------------------------
# ③ 标准化频道
# ----------------------------
def normalize(ch):
    return {
        "name": ch.get("name") or ch.get("title") or ch.get("channel"),
        "postid": str(
            ch.get("postid")
            or ch.get("epgId")
            or ch.get("id")
            or ""
        )
    }


# ----------------------------
# ④ EPG抓取
# ----------------------------
def fetch_epg(postid):
    import requests

    url = (
        "https://www.bein.com/en/epg-ajax-template/"
        f"?action=epg_fetch&offset=0&category=sports"
        f"&serviceidentity=bein.net&mins=00&postid={postid}"
    )

    r = requests.get(url, timeout=20)

    if r.status_code != 200:
        return None

    return r.text


# ----------------------------
# ⑤ 主流程
# ----------------------------
def main():

    all_data = grab_network_data()

    print(f"\n[INFO] XHR packets: {len(all_data)}")

    channels_raw = extract_channels(all_data)

    if not channels_raw:
        print("[ERROR] No channels found in XHR data")
        return

    channels = []
    seen = set()

    for c in channels_raw:
        nc = normalize(c)

        if not nc["name"] or not nc["postid"]:
            continue

        key = (nc["name"], nc["postid"])
        if key in seen:
            continue

        seen.add(key)
        channels.append(nc)

    print(f"\n[INFO] Channels found: {len(channels)}\n")

    for i, ch in enumerate(channels, 1):
        print(f"{i}. {ch['name']} -> {ch['postid']}")

        epg = fetch_epg(ch["postid"])

        if epg:
            print("   ✔ EPG OK")
        else:
            print("   ✖ EPG FAIL")

        time.sleep(0.3)


if __name__ == "__main__":
    main()
