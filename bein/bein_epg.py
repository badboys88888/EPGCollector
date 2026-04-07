import json
import re
from playwright.sync_api import sync_playwright


URL = "https://www.bein.com/en/tv-guide/"


# ----------------------------
# ① 用浏览器打开页面
# ----------------------------
def get_rendered_html():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )

        page = browser.new_page()
        page.goto(URL, timeout=60000)

        # 等 JS 加载完成（关键）
        page.wait_for_timeout(8000)

        html = page.content()
        browser.close()

        return html


# ----------------------------
# ② 提取 JS 数据（核心）
# ----------------------------
def extract_channels(html):
    """
    从 Next.js / window state 中提取频道
    """

    # 找 __NEXT_DATA__
    match = re.search(
        r'__NEXT_DATA__"\s*type="application/json"\s*>(.*?)</script>',
        html
    )

    if not match:
        raise Exception("No JS state found")

    data = json.loads(match.group(1))

    # 递归找 channels
    def find(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in ["channels", "channel", "items"]:
                    if isinstance(v, list) and len(v) > 0:
                        return v
                res = find(v)
                if res:
                    return res

        elif isinstance(obj, list):
            for i in obj:
                res = find(i)
                if res:
                    return res
        return None

    channels = find(data)

    if not channels:
        raise Exception("No channels found")

    return channels


# ----------------------------
# ③ 标准化频道结构
# ----------------------------
def normalize(c):
    return {
        "name": c.get("name") or c.get("title"),
        "postid": str(
            c.get("postid")
            or c.get("epgId")
            or c.get("id")
            or ""
        )
    }


# ----------------------------
# ④ epg请求
# ----------------------------
def fetch_epg(postid):
    url = (
        "https://www.bein.com/en/epg-ajax-template/"
        f"?action=epg_fetch&offset=0&category=sports"
        f"&serviceidentity=bein.net&mins=00&postid={postid}"
    )

    import requests
    r = requests.get(url, timeout=20)

    if r.status_code != 200:
        return None

    return r.text


# ----------------------------
# ⑤ 主流程
# ----------------------------
def main():

    print("[INFO] Loading page via Playwright...")

    html = get_rendered_html()

    print("[INFO] Extracting channels...")

    raw_channels = extract_channels(html)

    channels = []
    seen = set()

    for c in raw_channels:
        nc = normalize(c)

        if not nc["name"] or not nc["postid"]:
            continue

        key = (nc["name"], nc["postid"])
        if key in seen:
            continue

        seen.add(key)
        channels.append(nc)

    print(f"\n[INFO] Channels: {len(channels)}\n")

    for i, ch in enumerate(channels, 1):
        print(f"{i}. {ch['name']} -> {ch['postid']}")

        epg = fetch_epg(ch["postid"])

        if epg:
            print("   ✔ EPG OK")
        else:
            print("   ✖ EPG FAIL")


if __name__ == "__main__":
    main()
