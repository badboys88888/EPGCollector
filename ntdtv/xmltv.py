"""
XMLTV 写入模块 —— 不关心数据从哪来, 只负责把节目单写成 Emby 能导入的 XMLTV。

用法(给后面的解析模块调用):
    from xmltv import write_xmltv
    write_xmltv(
        "ntdtv.xml",
        channels={"美東台": "美東台", "美西台": "美西台"},     # id -> 显示名, id 要和 m3u 里的 tvg-id 一致
        programmes=[
            {"channel": "美東台", "start": dt1, "stop": dt2, "title": "新聞", "desc": "..."},
        ],
    )
start/stop 是带时区的 datetime。
"""
from datetime import timezone
from pathlib import Path
from xml.sax.saxutils import escape


def _fmt(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def write_xmltv(path, channels: dict, programmes: list):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<tv generator-info-name="emby_scraper">']
    for cid, name in channels.items():
        lines.append(f'  <channel id="{escape(cid, {chr(34): "&quot;"})}">')
        lines.append(f"    <display-name>{escape(name)}</display-name>")
        lines.append("  </channel>")
    for p in programmes:
        cid = escape(p["channel"], {'"': "&quot;"})
        lines.append(f'  <programme start="{_fmt(p["start"])}" stop="{_fmt(p["stop"])}" channel="{cid}">')
        lines.append(f"    <title>{escape(p['title'])}</title>")
        if p.get("desc"):
            lines.append(f"    <desc>{escape(p['desc'])}</desc>")
        lines.append("  </programme>")
    lines.append("</tv>")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
