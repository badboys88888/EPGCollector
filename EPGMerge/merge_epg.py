#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EPG 合并工具:把多个 xml / xml.gz(本地、GitHub、外网 URL 均可)合并成
一份 xml + xml.gz + json,并自动补全频道 <icon>。

只用 Python 标准库,不需要 pip install。

用法:
    python EPGMerge/merge_epg.py
    python EPGMerge/merge_epg.py --prune-empty --keep-days 1
"""
import argparse
import gzip
import io
import json
import re
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; EPGMerge/1.0)"


# ----------------------------------------------------------------- 工具函数
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def norm_key(s):
    """频道名/ID 归一化:转小写、去空格和符号,用于匹配 icon / 别名。"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\W_]+", "", s)


def fix_github_url(url):
    """把 github.com/.../blob/... 自动转成 raw 直链。"""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url


def fetch(src):
    """读取一个源,返回 bytes;gz 按文件头自动识别,不依赖后缀。"""
    if re.match(r"https?://", src):
        url = fix_github_url(src)
        last = None
        for i in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = r.read()
                break
            except Exception as e:  # noqa
                last = e
                time.sleep(2 * (i + 1))
        else:
            raise RuntimeError(f"下载失败: {last}")
    else:
        p = Path(src)
        if not p.is_absolute():
            p = BASE / p
        data = p.read_bytes()

    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def parse_time(s):
    """XMLTV 时间 -> UTC datetime,失败返回 None。"""
    m = re.match(r"\s*(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?\s*([+-]\d{4})?", s or "")
    if not m:
        return None
    y, mo, d, h, mi, se, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(se or 0))
    except ValueError:
        return None
    off = 0
    if tz:
        off = (int(tz[1:3]) * 60 + int(tz[3:5])) * (1 if tz[0] == "+" else -1)
    return (dt - timedelta(minutes=off)).replace(tzinfo=timezone.utc)


def load_json(path, default):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa
            log(f"[警告] 读取 {p} 失败: {e}")
    return default


def read_sources(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# ----------------------------------------------------------------- 解析
def parse_epg(data, name):
    channels, programmes = [], []
    try:
        for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
            if el.tag == "channel":
                channels.append(el)
            elif el.tag == "programme":
                programmes.append(el)
    except ET.ParseError as e:
        log(f"  [警告] {name} XML 解析中断(已保留之前解析到的内容): {e}")
    return channels, programmes


# ----------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="合并多个 EPG (xml / xml.gz)")
    ap.add_argument("-s", "--sources", default=str(BASE / "sources.txt"))
    ap.add_argument("-o", "--output", default=str(BASE / "output"))
    ap.add_argument("--icons", default=str(BASE / "icons.json"),
                    help="手动 icon 表 {频道ID或名称: 图标URL}")
    ap.add_argument("--aliases", default=str(BASE / "aliases.json"),
                    help="频道ID别名表 {旧ID: 统一ID}")
    ap.add_argument("--prune-empty", action="store_true", help="删除没有节目的频道")
    ap.add_argument("--keep-days", type=int, default=0,
                    help="丢弃结束时间早于 N 天前的节目,0=不丢弃")
    ap.add_argument("--no-json-programmes", action="store_true",
                    help="epg.json 里不包含节目(只有频道),体积更小")
    args = ap.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = read_sources(args.sources)
    aliases = {norm_key(k): v for k, v in load_json(args.aliases, {}).items()}
    manual_icons = {norm_key(k): v for k, v in load_json(args.icons, {}).items()}

    channels = {}        # id -> {"id","names","icon","urls"}
    auto_icons = {}      # norm(name/id) -> icon url (来自各源自带的 icon)
    programmes = {}      # (channel,start) -> Element
    ok_sources = 0

    for idx, src in enumerate(sources, 1):
        log(f"[{idx}/{len(sources)}] {src}")
        try:
            data = fetch(src)
        except Exception as e:  # noqa
            log(f"  [失败] {e}")
            continue
        ch_els, pg_els = parse_epg(data, src)
        log(f"  频道 {len(ch_els)}  节目 {len(pg_els)}")
        if not ch_els and not pg_els:
            continue
        ok_sources += 1

        for el in ch_els:
            cid = el.get("id")
            if not cid:
                continue
            cid = aliases.get(norm_key(cid), cid)
            c = channels.setdefault(cid, {"id": cid, "names": [], "icon": "", "urls": []})
            for dn in el.findall("display-name"):
                t = (dn.text or "").strip()
                if t and t not in c["names"]:
                    c["names"].append(t)
            icon_el = el.find("icon")
            icon = (icon_el.get("src") if icon_el is not None else "") or ""
            if icon and not c["icon"]:
                c["icon"] = icon
            for u in el.findall("url"):
                if u.text and u.text.strip() not in c["urls"]:
                    c["urls"].append(u.text.strip())
            if icon:  # 记录到 icon 索引,供其他没有 icon 的源按名称借用
                for key in [cid] + [dn.text for dn in el.findall("display-name")]:
                    k = norm_key(key)
                    if k and k not in auto_icons:
                        auto_icons[k] = icon

        for el in pg_els:
            ch = el.get("channel")
            st = el.get("start")
            if not ch or not st:
                continue
            ch = aliases.get(norm_key(ch), ch)
            el.set("channel", ch)
            programmes.setdefault((ch, st), el)  # 先出现的源优先

    if ok_sources == 0:
        log("所有源都失败了,不覆盖旧文件。")
        sys.exit(1)

    # 节目里出现但频道表里没有的频道,补一个空频道
    for ch, _ in programmes:
        channels.setdefault(ch, {"id": ch, "names": [ch], "icon": "", "urls": []})

    # ---- 补 icon:手动表 > 源里自带 > 无
    filled = 0
    for c in channels.values():
        if c["icon"]:
            continue
        for key in [c["id"]] + c["names"]:
            k = norm_key(key)
            icon = manual_icons.get(k) or auto_icons.get(k)
            if icon:
                c["icon"] = icon
                filled += 1
                break
    # 手动表优先覆盖?如需强制覆盖,把下面改成 True
    FORCE_MANUAL = True
    if FORCE_MANUAL:
        for c in channels.values():
            for key in [c["id"]] + c["names"]:
                if norm_key(key) in manual_icons:
                    c["icon"] = manual_icons[norm_key(key)]
                    break

    # ---- 过滤旧节目
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.keep_days) if args.keep_days > 0 else None
    pg_list = []
    for (ch, st), el in programmes.items():
        t_start = parse_time(st)
        t_stop = parse_time(el.get("stop"))
        if cutoff and (t_stop or t_start) and (t_stop or t_start) < cutoff:
            continue
        pg_list.append((ch, t_start or datetime.min.replace(tzinfo=timezone.utc), st, el, t_start, t_stop))
    pg_list.sort(key=lambda x: (x[0], x[1]))

    counts = {}
    for ch, *_ in pg_list:
        counts[ch] = counts.get(ch, 0) + 1
    if args.prune_empty:
        channels = {k: v for k, v in channels.items() if counts.get(k)}

    # ---- 输出 XML
    root = ET.Element("tv", {
        "generator-info-name": "EPGMerge",
        "date": now.strftime("%Y%m%d%H%M%S +0000"),
    })
    for c in channels.values():
        ce = ET.SubElement(root, "channel", {"id": c["id"]})
        for n in c["names"] or [c["id"]]:
            ET.SubElement(ce, "display-name").text = n
        if c["icon"]:
            ET.SubElement(ce, "icon", {"src": c["icon"]})
        for u in c["urls"]:
            ET.SubElement(ce, "url").text = u
    kept = 0
    for ch, _, _, el, _, _ in pg_list:
        if ch in channels:
            root.append(el)
            kept += 1
    ET.indent(root, space="  ")
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    (out_dir / "merged.xml").write_bytes(xml_bytes)
    (out_dir / "merged.xml.gz").write_bytes(gzip.compress(xml_bytes, 9))

    # ---- 输出 JSON
    ch_json = [{
        "id": c["id"],
        "name": c["names"][0] if c["names"] else c["id"],
        "names": c["names"],
        "icon": c["icon"],
        "programmes": counts.get(c["id"], 0),
    } for c in channels.values()]

    meta = {
        "generated": now.isoformat(),
        "sources": sources,
        "channel_count": len(ch_json),
        "programme_count": kept,
    }
    (out_dir / "channels.json").write_text(
        json.dumps({**meta, "channels": ch_json}, ensure_ascii=False, indent=1), encoding="utf-8")

    full = {**meta, "channels": ch_json}
    if not args.no_json_programmes:
        plist = []
        for ch, _, st, el, t_start, t_stop in pg_list:
            if ch not in channels:
                continue
            icon_el = el.find("icon")
            plist.append({
                "channel": ch,
                "start": st,
                "stop": el.get("stop", ""),
                "start_iso": t_start.isoformat() if t_start else "",
                "stop_iso": t_stop.isoformat() if t_stop else "",
                "title": (el.findtext("title") or "").strip(),
                "desc": (el.findtext("desc") or "").strip(),
                "category": [(x.text or "").strip() for x in el.findall("category")],
                "icon": icon_el.get("src") if icon_el is not None else "",
            })
        full["programmes"] = plist
    (out_dir / "epg.json").write_text(
        json.dumps(full, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    with_icon = sum(1 for c in channels.values() if c["icon"])
    log(f"\n完成: 成功源 {ok_sources}/{len(sources)} | 频道 {len(channels)} "
        f"(有图标 {with_icon},本次自动补全 {filled}) | 节目 {kept}")
    log(f"输出目录: {out_dir}")
    missing = [c["id"] for c in channels.values() if not c["icon"]]
    if missing:
        (out_dir / "missing_icons.txt").write_text("\n".join(missing), encoding="utf-8")
        log(f"仍缺图标的频道 {len(missing)} 个,列表见 missing_icons.txt")


if __name__ == "__main__":
    main()
