import re
import requests

def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def extract_postids(html: str):
    """
    多模式提取 postid（beIN 页面非常乱，必须多策略）
    """

    patterns = [
        r"postid\s*[:=]\s*['\"]?(\d+)['\"]?",
        r"postId\s*[:=]\s*['\"]?(\d+)['\"]?",
        r"\"postid\"\s*:\s*['\"]?(\d+)['\"]?",
        r"'postid'\s*[:=]\s*['\"]?(\d+)['\"]?",
    ]

    results = []

    for p in patterns:
        results.extend(re.findall(p, html, flags=re.IGNORECASE))

    # 去重 + 保持顺序
    return list(dict.fromkeys(results))


def main():
    url = "https://www.bein.com/en/tv-guide/?c=us&"
    html = fetch_html(url)

    postids = extract_postids(html)

    print(f"[INFO] Found postids: {len(postids)}")
    print(postids)

    # 如果你后面要抓 epg
    for pid in postids:
        epg_url = (
            "https://www.bein.com/en/epg-ajax-template/"
            f"?action=epg_fetch&offset=+0&category=sports"
            f"&serviceidentity=bein.net&mins=00&postid={pid}"
        )

        print(f"[FETCH] {epg_url}")
        # 这里可以继续 requests.get(epg_url)

if __name__ == "__main__":
    main()
