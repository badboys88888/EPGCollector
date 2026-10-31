"""负责网络请求: 取页面 HTML、下载图片。支持代理和 playwright。"""
import requests

from config import HEADERS, TIMEOUT


class Fetcher:
    def __init__(self, proxy=None, use_browser=False):
        self.session = requests.Session()
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.use_browser = use_browser

    def get_html(self, url: str) -> str:
        if self.use_browser:
            return self._get_html_browser(url)
        r = self.session.get(url, headers=HEADERS, timeout=TIMEOUT, proxies=self.proxies)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding
        return r.text

    def get_bytes(self, url: str, referer: str = "") -> bytes:
        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer
        r = self.session.get(url, headers=headers, timeout=30, proxies=self.proxies)
        r.raise_for_status()
        return r.content

    def _get_html_browser(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SystemExit("需要先安装: pip install playwright && playwright install chromium")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            html = page.content()
            browser.close()
        return html
