#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epg_all.py — 全部 EPG 抓取 + 合并，一个文件搞定。

来源：用户 GitHub 上的 EPGCollector / epg 两个库。
  - 10 个平台抓取脚本（都是爬各电视台网站 API/页面，直出 XMLTV）：
    mytvsuper（香港 myTV SUPER）、nowtv（香港 Now TV）、hami（台湾 Hami Video）、
    hoytv（香港 HOY TV）、mewatch（新加坡 meWATCH）、bein（beIN）、
    beinsports（beIN SPORTS）、mncvision（印尼 MNC Vision）、
    starhub（新加坡 StarHub）、ntdtv（新唐人）、epg_script（open-epg.com 转换）
  - epg 库 merge_epg.py 的合并逻辑（2026-11-22 修好版）：
    alias_map.json 名称映射归一——先查 display-name 再查原始 id、
    去 HD/高清/4K 后缀、两遍合并、补齐只有节目的频道。

用法：
  python3 epg_all.py [--days N] [--only mytvsuper,nowtv] [--skip mewatch]
输出：当前目录下的 epg.xml.gz 和 channels.json。

依赖：只用 Python 标准库 + requests。
注意：
  - 各平台 API 地址、请求头、解析逻辑、重试/延时全部原样保留，
    只是把“写文件”改成“返回内存里的 XML 根节点”，最后统一合并。
  - 某个平台失败只记日志跳过，不中断整个运行。
  - mewatch 原脚本有个 bug：icon 的 URL 里有未转义的 &，导致 XML
    解析失败（line 6 col 107）。这里在生成时对 icon/src 做转义，
    其余逻辑不变；万一仍解析失败，合并时跳过该源不崩。
  - ntdtv 原来用 bs4 + lxml（非标准库），这里用标准库 html.parser /
    正则改写等价逻辑；浏览器模式（playwright）不包含，只用静态模式。
"""

import argparse
import gzip
import html as html_mod
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from xml.dom import minidom
from xml.sax.saxutils import escape as xml_escape

import requests


# ===================== 配置区 ===================== #
DAYS = 3                                # 抓取天数（各平台按自己的方式用这个天数）
OUTPUT_XML_GZ = "epg.xml.gz"            # 合并输出
OUTPUT_CHANNELS_JSON = "channels.json"  # 合并输出

# 各平台开关，默认全开；命令行 --only / --skip 可覆盖
ENABLE = {
    "mytvsuper": True,
    "nowtv": True,
    "hami": True,
    "hoytv": True,
    "mewatch": True,
    "bein": True,
    "beinsports": True,
    "mncvision": True,
    "starhub": True,
    "ntdtv": True,
    "epg_script": True,
}

# 平台 -> 地区标签（写进合并后频道的 source 字段）
PLATFORM_REGION = {
    "mytvsuper": "HK",
    "nowtv": "HK",
    "hami": "TW",
    "hoytv": "HK",
    "mewatch": "SG",
    "bein": "HK",
    "beinsports": "HK",
    "mncvision": "ID",
    "starhub": "SG",
    "ntdtv": "US",
    "epg_script": "OPENEPG",
}

ALIAS_MAP_FILE = "alias_map.json"  # 同目录下有就优先读它


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ===================== 名字归一化 / 别名映射 ===================== #
def norm_name(s):
    """归一化频道名/ID：NFKC、全角转半角、去空白、去常见符号、小写，
    并去掉末尾的 HD/高清/4K 等清晰度后缀——同一频道的高清版和标清版
    播的是同一套节目，EPG 里应归一成同一个频道（例如民視HD -> 民視）。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.strip().lower()
    s = re.sub(r"[\s\-_·•]+", "", s)
    s = re.sub(r"(hd|高清|4k|uhd|fhd)$", "", s)
    return s


def _build_alias_maps(exact):
    """由 canonical id -> 别名列表，建反向表。先到先得，冲突汇总。"""
    reverse = {}
    conflicts = []
    for cid, aliases in (exact or {}).items():
        cid = str(cid).strip()
        if not cid:
            continue
        reverse.setdefault(norm_name(cid), cid)
        for name in aliases or []:
            key = norm_name(name)
            if not key:
                continue
            if key in reverse and reverse[key] != cid:
                conflicts.append((name, reverse[key], cid))
                continue
            reverse[key] = cid
    return reverse, conflicts


def load_alias_map():
    """读 alias_map.json，返回 (canonical_id -> [别名], 别名归一化 -> canonical_id)。
    优先读脚本同目录下的 alias_map.json，找不到就用内嵌的完整映射。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, ALIAS_MAP_FILE), ALIAS_MAP_FILE):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    exact = json.load(f).get("exact", {}) or {}
                reverse, conflicts = _build_alias_maps(exact)
                log(f"[ALIAS] 从 {path} 加载 {len(exact)} 条映射，"
                    f"别名 {len(reverse)} 个，冲突 {len(conflicts)} 组")
                return exact, reverse
            except Exception as e:
                log(f"[WARN] 读取 {path} 失败: {e}，改用内嵌映射")
                break
    exact = json.loads(EMBEDDED_ALIAS_MAP_JSON).get("exact", {}) or {}
    reverse, conflicts = _build_alias_maps(exact)
    log(f"[ALIAS] 使用内嵌映射 {len(exact)} 条，别名 {len(reverse)} 个，"
        f"冲突 {len(conflicts)} 组")
    return exact, reverse


def canonical_id(cid, display_names, reverse):
    """把一个频道的原始 id 归一成 canonical id。
    顺序很关键：先查 display-name，再查原始 id。
    因为 alias_map 本身可能被污染——同一个频道名的多个重复 id 各占一条
    （如民視 = 370139 / 457209 / 460831 / 501116），若先查原始 id，
    501116 会映射回它自己，归一失败。先查名字才能把所有重复 id
    收敛到第一个 canonical id（370139）。"""
    for name in display_names or []:
        key = norm_name(name)
        if key in reverse:
            return reverse[key]
    key = norm_name(cid)
    if key in reverse:
        return reverse[key]
    return cid


# ===================== XML 通用 ===================== #
def indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for child in elem:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def parse_epg_bytes(content):
    """解析 XML / XML.GZ 字节，返回 ET 根节点。"""
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return ET.fromstring(content)


# ===================== 合并逻辑（epg 库 merge_epg.py 修好版） ===================== #
def merge_roots(roots, exact, reverse):
    """roots: [(ET根节点, 地区标签), ...]。返回 (tv 根节点, channel_map)。"""
    tv = ET.Element("tv")

    # ---------------- 第一遍：CHANNEL ---------------- #
    # 先走完所有频道，建 raw id -> canonical id 的映射表，
    # 因为 programme 里只有原始 channel id，没有 display-name，
    # 必须靠这张表才能跟着归一。
    channel_map = {}
    id_map = {}

    for root, region in roots:
        for ch in root.findall("channel"):
            raw_cid = ch.attrib.get("id")
            if not raw_cid:
                continue
            names = []
            for dn in ch.findall("display-name"):
                if dn.text and dn.text.strip():
                    names.append(dn.text.strip())
            cid = canonical_id(raw_cid, names, reverse)
            id_map.setdefault(raw_cid, cid)
            if cid not in channel_map:
                channel_map[cid] = {"names": set(), "icon": None, "sources": set()}
            channel_map[cid]["sources"].add(region)
            for name in names:
                channel_map[cid]["names"].add(name)
            icon = ch.find("icon")
            if icon is not None and icon.get("src"):
                if not channel_map[cid]["icon"]:
                    channel_map[cid]["icon"] = icon.get("src")

    # ---------------- 第二遍：PROGRAMME ---------------- #
    prog_map = {}
    for root, region in roots:
        for prog in root.findall("programme"):
            raw_cid = prog.attrib.get("channel")
            start = prog.attrib.get("start")
            stop = prog.attrib.get("stop")
            title_node = prog.find("title")
            title = title_node.text.strip() if title_node is not None and title_node.text else ""
            cid = id_map.get(raw_cid)
            if cid is None:
                cid = canonical_id(raw_cid, None, reverse)
            key = (cid, start, stop, title)
            if key not in prog_map:
                prog_map[key] = {
                    "attrib": dict(prog.attrib),
                    "title": title,
                    "regions": set(),
                }
                prog_map[key]["attrib"]["channel"] = cid
            prog_map[key]["regions"].add(region)

    # ---------------- 补齐只有节目的频道 ---------------- #
    for (cid, _start, _stop, _title) in prog_map:
        if cid not in channel_map:
            channel_map[cid] = {"names": {cid}, "icon": None, "sources": set()}

    # ---------------- 写 CHANNEL ---------------- #
    for cid, info in channel_map.items():
        ch = ET.SubElement(tv, "channel", {
            "id": cid,
            "source": ",".join(sorted(info["sources"])),
        })
        if cid in exact and exact[cid]:
            name = exact[cid][0]
        else:
            name = sorted(info["names"])[0] if info["names"] else cid
        ET.SubElement(ch, "display-name").text = name
        if info["icon"]:
            ET.SubElement(ch, "icon", {"src": info["icon"]})

    # ---------------- 写 PROGRAMME ---------------- #
    for (cid, start, stop, title), info in prog_map.items():
        prog = ET.SubElement(tv, "programme", {
            "channel": cid,
            "start": start,
            "stop": stop,
        })
        ET.SubElement(prog, "title").text = title
        ET.SubElement(prog, "category").text = ",".join(sorted(info["regions"]))

    return tv, channel_map, len(prog_map)


def write_outputs(tv, channel_map, exact):
    indent(tv)
    xml_str = ET.tostring(tv, encoding="utf-8")
    with gzip.open(OUTPUT_XML_GZ, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
        f.write(xml_str)
    log(f"[OK] {OUTPUT_XML_GZ} 已保存")

    data = {}
    for cid, info in channel_map.items():
        if cid in exact and exact[cid]:
            all_names = [exact[cid][0]] + [n for n in sorted(info["names"]) if n != exact[cid][0]]
        else:
            all_names = sorted(info["names"])
        data[cid] = {
            "epgid": cid,
            "name": ",".join(all_names),
            "logo": info["icon"] or "",
            "source": ",".join(sorted(info["sources"])),
        }
    with open(OUTPUT_CHANNELS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"[OK] {OUTPUT_CHANNELS_JSON} 已保存")


# ===================== 内嵌数据（由生成脚本注入） =====================
# EMBEDDED_ALIAS_MAP_JSON / EMBEDDED_NOWTV_CONFIG_JSON /
# EMBEDDED_HAMI_CONFIG_JSON / EMBEDDED_BEIN_MAPPING_JSON /
# EMBEDDED_BEINSPORTS_CONFIG_JSON 在文件后面定义。
# ---- 以下为内嵌数据（与各平台原仓库里的 config/mapping/alias 文件内容一致） ----
# alias_map.json：epg 库的别名映射表（2354 条），同目录有该文件时优先读文件。
EMBEDDED_ALIAS_MAP_JSON = '{"exact":{"370137":["民視第一台","民视第一台"],"370138":["民視台灣台"],"370139":["民視","民视"],"370140":["大愛電視","大爱一台","大爱1台","大爱电视台","大愛電視台"],"370141":["中視","中视"],"370143":["華視","华视","CTS","华视HD","CTS HD"],"370144":["三立綜合台","三立综合台","三立综合"],"370145":["客家","客家电视台","客家電視台"],"370146":["八大綜藝台","八大综艺台"],"370148":["TVBS精采台"],"370149":["愛爾達娛樂台","爱尔达娱乐台","爱尔达娱乐"],"370150":["靖天綜合台"],"370151":["靖天日本台"],"370152":["ARIRANG阿里郎頻道"],"370153":["Global Trekker"],"370154":["原住民族"],"370156":["東森購物四台"],"370157":["LiveABC互動英語頻道"],"370158":["達文西頻道"],"370160":["Nick Jr. 兒童頻道"],"370161":["尼克兒童頻道"],"370162":["DreamWorks 夢工廠動畫"],"370163":["靖天卡通台"],"370164":["靖洋卡通Nice Bingo"],"370167":["MOMO親子台"],"370168":["東森購物一台"],"370169":["鏡電視新聞台"],"370170":["東森新聞台"],"370171":["華視新聞"],"370172":["民視新聞台"],"370173":["三立新聞iNEWS"],"370174":["TVBS新聞"],"370175":["東森財經新聞台"],"370176":["中視新聞"],"370177":["中天新聞台"],"370178":["Bloomberg TV"],"370179":["寰宇新聞台"],"370181":["SBN全球財經台"],"370184":["東森購物二台"],"370185":["民視綜藝台"],"370186":["豬哥亮歌廳秀"],"370187":["靖天育樂台"],"370188":["KLT-靖天國際台"],"370189":["Nice TV 靖天歡樂台"],"370190":["靖天資訊台"],"370192":["ROCK Entertainment"],"370193":["Lifetime 娛樂頻道"],"370194":["電影原聲台CMusic"],"370195":["TRACE Urban"],"370197":["Mezzo Live"],"370198":["CLASSICA 古典樂"],"370199":["東森購物三台"],"370207":["TRACE Sport Stars"],"370208":["智林體育台"],"370209":["時尚運動X"],"370210":["車迷TV"],"370211":["GINX Esports TV"],"370214":["民視旅遊台"],"370215":["滾動力rollor"],"370217":["fun探索娛樂台"],"370218":["幸福空間居家台"],"370219":["Love Nature"],"370220":["History 歷史頻道"],"370222":["愛爾達生活旅遊台"],"370223":["LUXE TV Channel"],"370224":["TV5MONDE STYLE HD 生活時尚"],"370226":["公視戲劇"],"370227":["民視影劇台"],"370229":["HITS頻道"],"370231":["八大精彩台"],"370233":["靖天戲劇台"],"370234":["靖洋戲劇台"],"370235":["CI 罪案偵查頻道"],"370236":["視納華仁紀實頻道"],"370238":["金光布袋戲"],"370239":["ROCK Action"],"370240":["采昌影劇台"],"370241":["靖天映畫"],"370242":["靖天電影台"],"370244":["影迷數位電影台"],"370245":["amc電影台"],"370246":["CinemaWorld"],"370247":["My Cinema Europe HD 我的歐洲電影"],"370248":["好消息2台"],"370249":["好消息"],"370250":["大愛二台"],"370251":["人間衛視"],"370252":["半島國際新聞台"],"370253":["CNBC Asia 財經台"],"370254":["DW德國之聲"],"370255":["國會頻道1"],"370256":["國會頻道2"],"370259":["經典電影台"],"370260":["經典卡通台"],"370262":["精選動漫台"],"374123":["FRANCE24 英文台"],"408104":["寰宇新聞台灣台"],"408106":["亞洲旅遊台"],"410773":["影迷數位紀實台"],"412028":["韓國娛樂台 KMTV"],"414181":["INULTRA"],"414182":["MagellanTV頻道 (麥哲倫頻道)"],"434232":["NHK WORLD-JAPAN"],"434391":["Pet Club TV"],"456500":["南桃園節目總表"],"456501":["南桃園公用頻道"],"456502":["CNN"],"456503":["民視無線台"],"456504":["TBC桃園生活台"],"456505":["台視"],"456507":["大愛"],"456508":["中視"],"456509":["人間衛視"],"456510":["華視"],"456511":["公共電視"],"456513":["公視台語台"],"456514":["好消息頻道"],"456516":["原住民頻道"],"456517":["客家"],"456518":["BBC Earth"],"456519":["Discovery"],"456520":["TLC旅遊生活頻道"],"456521":["動物星球"],"456522":["卡通頻道"],"456523":["小尼克頻道"],"456524":["MOMO親子台"],"456525":["東森幼幼台"],"456526":["緯來綜合台"],"456527":["八大第一台"],"456528":["八大綜合台"],"456529":["三立台灣台"],"456530":["三立都會台"],"456531":["華藝中文台"],"456532":["東森綜合台"],"456533":["東森超視"],"456534":["東森購物2台"],"456535":["MOMO 2台"],"456536":["中天綜合台"],"456537":["東風衛視"],"456538":["年代MUCH TV"],"456539":["中天娛樂台"],"456541":["東森戲劇台"],"456542":["八大戲劇台"],"456543":["TVBS歡樂台"],"456544":["緯來戲劇台"],"456545":["高點"],"456546":["JET綜合台"],"456547":["東森購物3台"],"456548":["東森購物1台"],"456549":["MOMO 1台"],"456550":["壹電視新聞台"],"456551":["年代新聞台"],"456552":["東森新聞台"],"456553":["民視新聞台"],"456555":["三立新聞台"],"456556":["TVBS 新聞台"],"456557":["TVBS"],"456558":["東森財經新聞台"],"456560":["非凡新聞台"],"456561":["ViVa1台"],"456562":["東森購物5台"],"456563":["CATCHPLAY電影台"],"456564":["東森電影台"],"456565":["緯來電影台"],"456566":["LS Time電影台"],"456567":["HBO"],"456568":["東森洋片台"],"456569":["AXN"],"456570":["好萊塢電影台"],"456571":["amc電影台"],"456572":["緯來育樂台"],"456574":["CINEMAX"],"456575":["緯來體育台"],"456576":["DAZN 1"],"456577":["DAZN 2"],"456578":["MOMO綜合台"],"456579":["緯來日本台"],"456580":["國興衛視"],"456581":["BBC Lifestyle"],"456582":["靖天資訊台"],"456583":["信吉"],"456584":["信大"],"456585":["中台灣生活網頻道"],"456586":["TBC台中生活台"],"456587":["鏡電視新聞台"],"456588":["台灣藝術台"],"456589":["樂視台"],"456590":["非凡商業台"],"456591":["三立財經新聞台"],"456592":["冠軍"],"456593":["運通財經綜合台"],"456594":["SBN全球財經台"],"456595":["誠心"],"456596":["NHK"],"456597":["MTV"],"456598":["Animax"],"456599":["霹靂台灣台"],"456600":["海豚綜合台"],"456601":["八大娛樂台"],"456602":["十方法界"],"456603":["壹電視電影台"],"456604":["華藏衛視"],"456605":["壹電視資訊綜合台"],"456606":["佛衛慈悲台"],"456607":["紅豆"],"456608":["全大"],"456609":["華藝台灣台"],"456610":["正德"],"456611":["天良綜合台"],"456612":["番薯衛星"],"456613":["富立"],"456615":["Z頻道"],"456617":["冠軍夢想台"],"456618":["新天地民俗台"],"456619":["三聖"],"456620":["威達超舜生活台"],"456621":["天美麗"],"456622":["大立"],"456623":["雙子衛視"],"456624":["小公視"],"456625":["華視教育體育文化台"],"456626":["國會頻道1台"],"456627":["國會頻道2台"],"456628":["幸福空間居家台"],"456629":["高點育樂台"],"456630":["台灣綜合台"],"456631":["唯心"],"456632":["美麗人生購物台"],"456633":["大愛二台"],"456634":["靖天映畫"],"456635":["靖洋戲劇台"],"456636":["ROCK Action"],"456637":["Global Trekker 探索世界"],"456638":["靖天綜合台"],"456639":["寶島文化台"],"456640":["TaiwanPlus"],"456641":["BBC News"],"456642":["民視第一台"],"456643":["民視台灣台"],"456644":["中視新聞台"],"456645":["台視新聞台"],"456646":["台視財經台"],"456647":["台視綜合台"],"456648":["彭博財經台"],"456649":["Channel NewsAsia"],"456650":["韓國阿里郎"],"456651":["DREAMWORKS"],"456652":["Warner TV"],"456653":["靖天電影台"],"456654":["CinemaWorld"],"456655":["HBO"],"456656":["HBO 強檔鉅獻"],"456657":["HBO 原創鉅獻"],"456658":["HBO 溫馨家庭"],"456659":["靖天日本台"],"456660":["EVE"],"456661":["tvN"],"456662":["靖天歡樂台"],"456663":["HITS"],"456664":["韓國娛樂台KMTV"],"456665":["靖天育樂台"],"456666":["Lifetime"],"456667":["罪案偵緝頻道"],"456668":["寵物頻道"],"456670":["歷史頻道"],"456671":["Discovery Asia"],"456807":["Discovery科學頻道"],"456808":["DMAX"],"456809":["寰宇新聞台灣台"],"456810":["亞洲旅遊台"],"456811":["梅迪奇藝術"],"456812":["博斯運動二台"],"456813":["博斯網球台"],"456814":["博斯無限台"],"456815":["博斯高球1台"],"456816":["博斯高球2台"],"456817":["博斯魅力網"],"456818":["博斯運動一台"],"456819":["博斯無限二台"],"456821":["尼克兒童頻道"],"456822":["達文西頻道"],"456823":["CBeebies"],"456824":["CARTOONITO"],"456825":["靖洋卡通台"],"456826":["靖天卡通台"],"456827":["LOVE NATURE 4K"],"456828":["ROCK ENTERTAINMENT"],"456829":["ROCK EXTREME"],"456830":["彩虹頻道"],"456831":["彩虹e台"],"456832":["彩虹電影台"],"456833":["K 頻道"],"456834":["松視1台"],"456835":["松視2台"],"456836":["松視3台"],"456837":["松視4台"],"456838":["潘朵啦高畫質玩美台"],"456839":["潘朵啦高畫質粉紅台"],"456840":["JStar極限台電影頻道"],"456841":["驚豔成人電影台"],"456842":["香蕉台"],"456843":["樂活頻道"],"456844":["玩家頻道"],"456845":["Happy"],"456846":["Hot"],"457208":["頻道總表"],"457209":["民視"],"457210":["人間衛視"],"457211":["台視"],"457212":["大愛電視"],"457213":["中視"],"457214":["華視"],"457215":["公視(HD)"],"457216":["公視台語台"],"457217":["好消息"],"457218":["原住民族電視"],"457219":["客家電視"],"457220":["華視教育體育文化台"],"457221":["公視兒少台"],"457222":["大愛二台"],"457223":["CARTOONITO"],"457224":["ELTV生活英語台"],"457225":["龍華動畫"],"457226":["尼克兒童頻道"],"457227":["靖洋卡通台Nice Bingo"],"457228":["靖天卡通台"],"457229":["Cbeebies"],"457230":["達文西頻道"],"457231":["Nick Jr."],"457232":["DREAMWORKS"],"457233":["國會頻道1"],"457234":["國會頻道2"],"457235":["Fun探索娛樂台 4K"],"457236":["Medici-arts"],"457238":["MTV綜合台"],"457239":["Mezzo Live"],"457240":["TRACE Urban"],"457241":["古典音樂台"],"457242":["CMusic"],"457243":["愛爾達體育1台"],"457244":["愛爾達體育2台"],"457245":["愛爾達體育3台"],"457246":["愛爾達體育4台"],"457247":["博斯高球一台"],"457248":["博斯高球二台"],"457249":["博斯網球"],"457250":["博斯魅力"],"457251":["博斯無限台"],"457252":["博斯無限二台"],"457253":["TRACE Sport Stars"],"457254":["DAZN 3"],"457255":["博斯運動一台"],"457256":["博斯運動二台"],"457257":["DAZN 2"],"457258":["智林體育"],"457259":["EUROSPORT"],"457260":["麥哲倫頻道"],"457261":["LOVE NATURE 4K"],"457262":["BBC Earth"],"457263":["Discovery Asia"],"457264":["Discovery科學"],"457265":["DMAX"],"457266":["EVE"],"457267":["Global Trekker"],"457268":["歷史頻道"],"457269":["罪案偵緝頻道"],"457270":["BBC Lifestyle"],"457271":["INULTRA"],"457272":["PET CLUB TV"],"457273":["LUXE TV"],"457274":["Lifetime"],"457275":["KLT-靖天國際台"],"457276":["幸福空間居家台"],"457277":["滾動力 rollor"],"457278":["HGTV居家樂活頻道"],"457279":["車迷TV"],"457280":["美食星球"],"457281":["亞洲美食頻道"],"457282":["Food Network 美食台"],"457283":["亞洲旅遊"],"457284":["EYE TV旅遊台"],"457285":["Travel Channel"],"457286":["Fashion TV"],"457288":["愛爾達綜合台"],"457289":["三立綜合台"],"457290":["靖天資訊"],"457291":["靖天綜合台"],"457292":["靖天育樂"],"457293":["Smart 知識"],"457294":["台視綜合"],"457295":["亞洲綜合"],"457296":["中視經典台"],"457297":["寰宇HD綜合"],"457298":["中視菁采台"],"457299":["TVBS精采台"],"457300":["壹電視綜合"],"457301":["緯來精采"],"457302":["高點育樂台"],"457303":["ETtoday綜合台"],"457304":["民視第一台"],"457305":["民視台灣台"],"457306":["Nice TV 靖天歡樂台"],"457307":["八大優頻道"],"457308":["momo綜合台"],"457309":["momo2台"],"457310":["東森購物3台"],"457311":["三立戲劇台"],"457312":["台灣戲劇"],"457313":["龍華戲劇"],"457314":["龍華偶像"],"457315":["龍華影劇"],"457316":["愛爾達影劇台"],"457317":["靖天戲劇"],"457319":["EYE TV戲劇台"],"457320":["東森購物2台"],"457321":["靖天日本"],"457322":["曼迪日本台"],"457323":["Animax"],"457324":["OUTtv"],"457325":["tvN"],"457326":["韓國娛樂台KMTV"],"457327":["華藝中文台"],"457328":["ROCK Extreme"],"457329":["ROCK Entertainment"],"457330":["美好2台"],"457331":["momo1台"],"457332":["東森購物4台"],"457333":["中視新聞"],"457334":["寰宇新聞"],"457335":["寰宇新聞台灣台"],"457336":["台視新聞"],"457337":["三立財經新聞台"],"457338":["華視新聞資訊"],"457339":["壹電視新聞"],"457340":["民視新聞台"],"457341":["鏡電視新聞台"],"457342":["寰宇財經"],"457343":["台視財經"],"457344":["美好1台"],"457345":["東森購物5台"],"457346":["CNNI"],"457347":["BBC NEWS"],"457348":["Bloomberg"],"457349":["Channel NewsAsia"],"457350":["CNBC Asia"],"457351":["Euronews"],"457352":["德國之聲"],"457354":["TaiwanPlus"],"457355":["NHK新聞資訊台"],"457356":["半島英語新聞台Al Jazeera English"],"457358":["視納華仁紀實台"],"457359":["影迷數位紀實台"],"457360":["美亞電影"],"457361":["龍華電影"],"457362":["靖天映畫"],"457363":["靖天電影"],"457364":["龍華洋片"],"457365":["Warner TV"],"457366":["ROCK Action"],"457367":["wedotv Movies"],"457368":["amc電影台"],"457369":["HITS"],"457370":["龍華經典"],"457371":["華藝影劇台"],"457372":["CatchPlay"],"457373":["CinemaWorld"],"457374":["壹電視電影台"],"457375":["采昌影劇台"],"457376":["影迷數位電影台"],"457377":["MCE 我的歐洲電影台"],"457378":["唯心電視"],"457379":["佛衛慈悲台"],"457381":["正德電視台"],"457382":["信吉"],"457383":["冠軍"],"457384":["華藝台灣台"],"457385":["信吉藝文台"],"457386":["大立電視台"],"457387":["富立電視台"],"457388":["新天地民俗台"],"457389":["天良綜合台"],"457390":["番薯"],"457391":["TV5MONDE"],"457393":["FRANCE24F"],"460724":["DMAX"],"460725":["壹電視新聞"],"460726":["東森新聞台"],"460727":["華藏衛視"],"460728":["美好1台"],"460729":["wedotv Movies"],"460730":["博斯運動二台"],"460731":["龍華動畫"],"460732":["天良綜合台"],"460733":["博斯高球二台"],"460734":["TRACE Sport Stars"],"460735":["Euronews"],"460738":["東森財經新聞台"],"460740":["三立財經新聞台"],"460741":["愛爾達綜合台"],"460742":["ROCK Action"],"460744":["Lifetime"],"460745":["Cinemax"],"460746":["影迷數位紀實台"],"460747":["博斯運動一台"],"460748":["民視台灣台"],"460749":["罪案偵緝頻道"],"460750":["靖洋卡通台Nice Bingo"],"460751":["公視台語台"],"460753":["HBO Signature"],"460754":["HBO Family"],"460755":["中視菁采台"],"460756":["BBC Earth"],"460757":["Animax"],"460758":["MCE 我的歐洲電影台"],"460759":["寰宇財經"],"460760":["華視新聞資訊"],"460761":["華藝中文台"],"460762":["東森購物2台"],"460764":["靖天綜合台"],"460765":["華藝台灣台"],"460766":["EVE"],"460767":["BBC Lifestyle"],"460768":["博斯魅力"],"460769":["博斯無限二台"],"460770":["佛衛慈悲台"],"460772":["東森購物1台"],"460773":["BBC NEWS"],"460774":["Channel NewsAsia"],"460775":["ELTV生活英語台"],"460776":["東森電影台"],"460777":["壹電視綜合"],"460778":["愛爾達體育4台"],"460779":["中視"],"460780":["東森戲劇台"],"460781":["德國之聲"],"460782":["靖天電影"],"460783":["龍華電影"],"460784":["正德電視台"],"460785":["LUXE TV"],"460786":["Global Trekker"],"460787":["NHK新聞資訊台"],"460788":["龍華影劇"],"460789":["冠軍"],"460790":["鏡電視新聞台"],"460791":["龍華偶像"],"460792":["東森購物3台"],"460793":["Cbeebies"],"460794":["東森購物4台"],"460795":["大愛二台"],"460796":["台視財經"],"460797":["HBO"],"460798":["台灣戲劇"],"460799":["CARTOONITO"],"460800":["國會頻道2"],"460801":["OUTtv"],"460802":["DMAX頻道"],"460803":["達文西頻道"],"460804":["國會頻道1"],"460805":["動物星球頻道"],"460806":["華視教育體育文化台"],"460807":["Fun探索娛樂台 4K"],"460808":["台視"],"460809":["大愛電視"],"460811":["Discovery"],"460812":["CinemaWorld"],"460813":["CatchPlay"],"460814":["tvN"],"460815":["愛爾達體育1台"],"460816":["龍華經典"],"460817":["三立綜合台"],"460818":["車迷TV"],"460821":["TV5MONDE"],"460822":["台視新聞"],"460823":["靖天映畫"],"460824":["曼迪日本台"],"460825":["東森購物5台"],"460826":["美好2台"],"460827":["Smart 知識"],"460828":["韓國娛樂台KMTV"],"460829":["中視經典台"],"460830":["東森綜合台"],"460831":["民視"],"460832":["民視新聞台"],"460833":["EUROSPORT"],"460834":["龍華戲劇"],"460835":["INULTRA"],"460836":["新天地民俗台"],"460837":["EYE TV戲劇台"],"460838":["Nick Jr."],"460839":["亞洲旅遊"],"460840":["半島英語新聞台Al Jazeera English"],"460841":["愛爾達體育2台"],"460842":["民視第一台"],"460843":["博斯無限台"],"460844":["AXN"],"460845":["東森幼幼台"],"460846":["TRACE Urban"],"460847":["公視(HD)"],"460848":["客家電視"],"460849":["ETtoday綜合台"],"460850":["公視兒少台"],"460851":["momo1台"],"460852":["美食星球"],"460853":["三立戲劇台"],"460854":["LOVE NATURE 4K"],"460855":["華藝影劇台"],"460856":["靖天資訊"],"460857":["HBO-Hits"],"460858":["ROCK Entertainment"],"460859":["中視新聞"],"460860":["台視綜合"],"460861":["寰宇HD綜合"],"460862":["momo2台"],"460863":["愛爾達影劇台"],"460864":["八大優頻道"],"460865":["HGTV居家樂活頻道"],"460866":["博斯高球一台"],"460868":["amc電影台"],"460869":["EYE TV旅遊台"],"460870":["滾動力 rollor"],"460871":["寰宇新聞台灣台"],"460872":["古典音樂台"],"460873":["美亞電影"],"460874":["影迷數位電影台"],"460875":["CNBC Asia"],"460876":["Discovery科學"],"460877":["Bloomberg"],"460878":["人間衛視"],"460879":["Discovery Asia"],"460880":["高點育樂台"],"460881":["靖天卡通台"],"460882":["MTV綜合台"],"460883":["緯來精采"],"460884":["靖天日本"],"460885":["歷史頻道"],"460886":["尼克兒童頻道"],"460887":["富立電視台"],"460888":["PET CLUB TV"],"460889":["智林體育"],"460890":["亞洲綜合"],"460892":["采昌影劇台"],"460893":["HBO"],"460894":["博斯網球"],"460895":["momo綜合台"],"460896":["大立電視台"],"460897":["Warner TV"],"460898":["靖天育樂"],"460899":["愛爾達體育3台"],"460900":["幸福空間居家台"],"460901":["番薯"],"460902":["寰宇新聞"],"460903":["CNNI"],"460904":["HITS"],"460905":["Fashion TV"],"460906":["DREAMWORKS"],"460907":["麥哲倫頻道"],"460908":["KLT-靖天國際台"],"460909":["Mezzo Live"],"460910":["Travel Channel"],"460911":["好消息"],"460912":["CMusic"],"460913":["FRANCE24E"],"460914":["原住民族電視"],"460915":["Food Network 美食台"],"460916":["TaiwanPlus"],"460918":["亞洲美食頻道"],"460919":["超級"],"460920":["信吉"],"460921":["Nice TV 靖天歡樂台"],"460922":["ROCK Extreme"],"460925":["東森洋片台"],"460926":["靖天戲劇"],"460927":["Medici-arts"],"460928":["華視"],"460929":["龍華洋片"],"460930":["視納華仁紀實台"],"460931":["信吉藝文台"],"460932":["壹電視電影台"],"460933":["FRANCE24F"],"460934":["唯心電視"],"460935":["頻道總表"],"460936":["DAZN 2"],"460937":["DAZN 3"],"473812":["大台中生活頻道"],"474640":["Arirang TV"],"476521":["東森購物1台"],"485011":["彰化生活台"],"490090":["好消息二台"],"491169":["好消息二台"],"492811":["TVBS 新聞台"],"492812":["TVBS"],"495607":["TVBS歡樂台"],"501114":["民視第一台"],"501115":["民視台灣台"],"501116":["民視"],"501117":["八大綜藝台"],"501118":["華視新聞"],"501119":["民視新聞台"],"501120":["中視新聞"],"501121":["中天新聞台"],"501122":["寰宇新聞台"],"501123":["時尚運動X"],"501124":["亞洲旅遊台"],"501874":["TVBS"],"501875":["FashionTV 時尚頻道"],"501876":["ROCK Xstream搖滾極限頻道"],"519418":["ELTV生活英語台"],"521219":["博斯高球台"],"521220":["博斯運動一台"],"521221":["博斯無限台"],"521222":["博斯網球台"],"535632":["TV5MONDE STYLE HD 生活時尚"],"536123":["TV5MONDE STYLE HD 生活時尚"],"460891":["靖洋戲劇"],"474641":["Arirang TV"],"457380":["華藏衛視"],"457353":["FRANCE24E"],"457318":["靖洋戲劇"],"491":["ERA FM"],"492":["MY FM"],"493":["OPUS"],"494":["HITZ FM"],"495":["MIX FM"],"531":["LITE FM"],"532":["CLASSIC ROCK"],"533":["GOLD"],"556":["宁夏公共","THR GEGAR"],"601":["INDIA BEAT"],"643":["JAZZ"],"702":["SINAR FM"],"733":["OSAI"],"771":["MELODY FM"],"848":["THR RAAGA"],"854":["BAYU"],"863":["KENYALANG"],"878":["Phoenix Info News"],"902":["TV9"],"946":["Adithya"],"997":["Asian Food Network"],"1029":["NTV7"],"1072":["TV3"],"1122":["8TV"],"1205":["AXN"],"1242":["ONE"],"1298":["Celestial Movies"],"1358":["SCTV7","Discovery Asia"],"1477":["东风卫视","History"],"1584":["EVE","Al-Hijrah"],"1781":["Astro Quan Jia"],"1835":["BERNAMA","EUROSPORT"],"1889":["KBS World"],"1951":["Astro Hua Hee Dai"],"2058":["Astro Vinmeen"],"2124":["Astro AOD 311"],"2181":["ABO Movies Thangathirai"],"2208":["HITS"],"2226":["Astro AEC"],"2280":["Celestial Classic Movies"],"2300":["Golf Channel"],"2323":["tvN"],"2384":["Astro Ria"],"2473":["HGTV"],"2524":["TVB Jade"],"2552":["Astro Arena"],"2580":["beIN Sports"],"2636":["BOO"],"2652":["K-Plus"],"2726":["tvN Movies"],"2756":["DW English"],"2781":["France24"],"2801":["ZAYAN"],"2830":["GOXUAN"],"2852":["CNA"],"2885":["Zee Tamil"],"2917":["Colors Tamil"],"2937":["Astro Citra"],"3018":["Astro SuperSport UHD 1"],"3118":["beIN Sports 3"],"3145":["Astro Oasis"],"3158":["Astro Prima"],"3179":["CNN"],"3234":["TLC"],"3290":["iQIYI"],"3308":["Star Vijay"],"3313":["SUN TV"],"3325":["Colors Hindi"],"3329":["BBC News"],"3362":["Crime & Investigation"],"3385":["Nickelodeon"],"3403":["Cartoon Network"],"3416":["Al Jazeera English"],"3437":["Discovery Channel"],"3478":["Phoenix Chinese Channel"],"3493":["TVB Xing He"],"3509":["TVBS Asia"],"3518":["CCTV4"],"3533":["Astro Ceria"],"3575":["HITS Movies"],"3600":["Nick Jr."],"3625":["Premier Sports"],"3683":["TV1"],"3711":["TV2"],"3735":["Astro Vaanavil"],"3760":["Astro Vellithirai"],"3770":["Astro Aura"],"3784":["Astro Rania"],"3809":["Astro Tutor TV SMK"],"3835":["Sun Music"],"3858":["Bloomberg TV"],"3873":["CNBC Asia"],"3885":["CTI Asia"],"3895":["TVB Classic"],"3906":["CGTN"],"3920":["TVB Entertainment News"],"3929":["NHK World Premium"],"3942":["TVS"],"3958":["Astro Awani"],"3968":["Lifetime"],"4019":["BBC Lifestyle"],"4030":["BBC Earth"],"4057":["SHOWCASE MOVIES"],"4100":["Astro Arena 2"],"4130":["ABC Australia"],"4136":["Moonbug"],"4146":["beIN Sports 2"],"4160":["TV Okey"],"4171":["Love Nature 4K"],"4181":["Sun Life"],"4183":["KTV"],"4185":["Sun News"],"4187":["CBeebies"],"4196":["Love Nature"],"4209":["Arena Bola"],"4215":["Arena Bola 2"],"4221":["Zee Cinema"],"48630":["W-Sport"],"186250":["HITS NOW"],"369578":["GOLD"],"369580":["tvN Movies"],"369585":["TVBS Asia"],"369586":["Discovery Channel"],"369587":["Al Jazeera English"],"369589":["Astro Vinmeen"],"369590":["BBC Earth"],"369592":["TV1"],"369594":["Astro AOD 311"],"369595":["BBC Lifestyle"],"369596":["Crime & Investigation"],"369599":["THR RAAGA"],"369600":["HITS Movies"],"369601":["beIN Sports 2"],"369604":["HGTV"],"369605":["Astro Oasis"],"369608":["Astro Ria"],"369609":["LITE FM"],"369610":["CNBC Asia"],"369612":["Nick Jr."],"369613":["Zee Tamil"],"369614":["Celestial Classic Movies"],"369616":["AXN"],"369621":["CGTN"],"369623":["INDIA BEAT"],"369624":["ZAYAN"],"369625":["Astro Awani"],"369626":["CCTV4"],"369627":["Bloomberg TV"],"369628":["Astro AEC"],"369631":["KENYALANG"],"369632":["DW English"],"369633":["THR GEGAR"],"369634":["NHK World Premium"],"369635":["NTV7"],"369636":["Astro Ceria"],"369638":["SINAR FM"],"369639":["CNA"],"369641":["MIX FM"],"369643":["ERA FM"],"369645":["BOO"],"369646":["Colors Tamil"],"369648":["Discovery Asia"],"369649":["History"],"369651":["Lifetime"],"369653":["KBS World"],"369654":["Astro Vellithirai"],"369655":["CNN"],"369656":["Celestial Movies"],"369657":["Phoenix Chinese Channel"],"369658":["France24"],"369659":["ABO Movies Thangathirai"],"369660":["Sun Music"],"369661":["Astro Citra"],"369662":["beIN Sports 3"],"369663":["Cartoon Network"],"369664":["CLASSIC ROCK"],"369666":["Astro Aura"],"369668":["Astro Hua Hee Dai"],"369670":["K-Plus"],"369671":["iQIYI"],"369673":["Colors Hindi"],"369675":["MELODY FM"],"369676":["Adithya"],"369677":["SUN TV"],"369678":["Astro Vaanavil"],"369679":["TVB Xing He"],"369680":["Al-Hijrah"],"369681":["TV Okey"],"369682":["Phoenix Info News"],"369683":["Asian Food Network"],"369684":["TVB Classic"],"369686":["Nickelodeon"],"369687":["OSAI"],"369689":["CTI Asia"],"369690":["TV3"],"369691":["ONE"],"369693":["TV9"],"369694":["Astro Rania"],"369695":["BERNAMA"],"369696":["Astro Prima"],"369697":["TV2"],"369698":["8TV"],"369700":["BAYU"],"369701":["TVB Jade"],"369703":["TLC"],"369704":["TVB Entertainment News"],"369705":["ABC Australia"],"369706":["tvN"],"369708":["JAZZ"],"369709":["Star Vijay"],"369710":["HITS"],"369712":["HITZ FM"],"370131":["TV6"],"370132":["Berita RTM"],"370133":["Sukan RTM"],"370134":["Okey"],"370135":["TV2"],"370136":["TV1"],"397396":["Astro Premier League"],"397397":["Astro Premier League 2"],"397399":["Astro Football"],"397400":["Astro Badminton"],"397402":["Astro Sports Plus"],"397403":["Astro Grandstand"],"399481":["Arena Bola"],"399482":["beIN SPORTS 1"],"399483":["SHOWCASE"],"399484":["HITS NOW"],"399486":["Astro Badminton"],"399487":["Arena Bola 2"],"399489":["Golf Channel"],"399490":["Sun Life"],"399491":["Astro Premier League 2"],"399493":["Moonbug"],"399494":["W-Sport"],"399495":["Sun News"],"399496":["CBeebies"],"399498":["Astro Football"],"399500":["Astro Tutor TV"],"399501":["Zee Cinema"],"399502":["GOXUAN"],"399504":["Love Nature 4K"],"399505":["Astro Grandstand"],"399506":["Premier Sports Rugby"],"399507":["KTV"],"399508":["Astro Arena 2"],"399509":["TV Sarawak"],"399510":["Astro Sports Plus"],"399511":["Astro Premier League"],"399514":["BBC News"],"399515":["Love Nature"],"399518":["Astro Sports U"],"399519":["Astro QJ"],"399521":["Astro Arena"],"400446":["Blippi & Friends"],"411231":["Wai FM Iban"],"411236":["Bintulu FM"],"411240":["Red FM"],"411241":["Wai FM"],"411251":["KL FM"],"411253":["Labuan FM"],"411255":["SabahV FM"],"411256":["Selangor FM"],"411257":["Asyik FM"],"411308":["TRAXXfm"],"421399":["MY FM"],"428785":["Perak FM"],"461591":["CGTN Documentary"],"479449":["Fitrah"],"479618":["Aura"],"479619":["Apetito"],"491618":["Keningau FM"],"491619":["Miri FM"],"502308":["JR."],"510886":["Sandakan FM"],"517640":["Rock Action"],"517641":["Astro Showtime"],"517642":["Rock X Stream"],"519419":["Astro First"],"519420":["Astro First"],"519423":["Astro First"],"519425":["Astro First"],"519428":["Astro First"],"519430":["Astro First"],"519886":["Astro First"],"519887":["Astro First"],"519888":["Astro First"],"519889":["Astro First"],"519890":["Astro First"],"519891":["Astro First"],"519892":["Astro First"],"520015":["Astro First"],"520016":["Astro First"],"520017":["Astro First"],"520490":["OPUS"],"521957":["ROLL"],"523496":["CricBuzz"],"524198":["Astro Sports Plus 2"],"535446":["Astro FAM Time"],"535942":["Astro Premier League 3"],"535993":["RTM Asean"],"535994":["RTM World"],"535996":["AIfm"],"535995":["Saluran\xa01"],"535447":["Astro Sports Plus 3"],"369713":["FOX News Channel"],"369716":["AXN (HD)"],"369717":["mio Sports 2 (HD)"],"369719":["ROCK Entertainment (HD)"],"369720":["TLC (HD)"],"369722":["NHK World Premium"],"369723":["Asianet Movies"],"369726":["France 24 (English)"],"369727":["ROCK Action (HD)"],"369728":["ONE HD (Mandarin)"],"369729":["BBC News"],"369730":["Celestial Movies (HD)"],"369734":["KBS World (Mandarin) (HD)"],"369737":["TRACE Sports Stars"],"369740":["HITS NOW (HD)"],"369741":["beIN SPORTS 2 (HD)"],"369742":["Citra Drama (HD)"],"369744":["Discovery Channel (HD)"],"369745":["Crime + Investigation™ (HD)"],"369746":["SET (HINDI)"],"369748":["CM+"],"369750":["beIN SPORTS (HD)"],"369752":["ONE HD (Malay)"],"369754":["tvN HD (Malay)"],"369757":["Sky News"],"369758":["HBO Signature (HD)"],"369760":["AFN (HD)"],"369765":["HISTORY™ (HD)"],"369766":["Deutsche Welle (English)"],"369767":["KBS World (HD)"],"369770":["HITS Movies"],"369771":["Bloomberg Television (HD)"],"369772":["Colors"],"369773":["mio Sports (HD)"],"369774":["Jia Le Channel (HD)"],"369775":["Hub Premier 4"],"369779":["HGTV (HD)"],"369780":["Cartoon Network (HD)"],"369783":["Phoenix InfoNews"],"369784":["Hub Premier 1"],"369785":["Animal Planet (HD)"],"369786":["HBO Family (HD)"],"369789":["CTI Asia (HD)"],"369790":["Premier Sports"],"369793":["Hub Premier 3"],"369795":["CINEMAX (HD)"],"369796":["TVB Xing He (HD)"],"369802":["mio Sports 3 (HD)"],"369804":["MTV India"],"369805":["TVB Jade (HD)"],"369806":["e-Le (HD)"],"369809":["HBO"],"369811":["Sony YAY!"],"369812":["Star Bharat"],"369813":["CNBC Asia"],"369814":["STAR Gold"],"369816":["CBeebies (HD)"],"369817":["beIN SPORTS 3 (HD)"],"369818":["tvN HD (Mandarin)"],"369819":["CCM"],"369822":["Hub Premier 2"],"369824":["Drama Channel"],"369826":["BBC Lifestyle (HD)"],"369827":["Fight Sports (HD)"],"369828":["ZEE TV"],"369832":["Lifetime (HD)"],"369833":["ABC Australia"],"369836":["NHK World – Japan (HD)"],"369837":["BBC Earth (HD)"],"369839":["France 24 (French)"],"369840":["HBO Hits (HD)"],"369844":["STAR Plus"],"369846":["SONY MAX"],"369848":["Animax (HD)"],"369849":["SAB TV"],"369851":["HITS (HD)"],"369854":["Colors Tamil"],"412088":["HBO"],"412089":["BBC Lifestyle"],"412090":["COLORS Tamil"],"412091":["ROCK Entertainment"],"412092":["Bloomberg Television"],"412093":["HBO Hits"],"412094":["Sun Music"],"412095":["DreamWorks Channel"],"412096":["Cinemax"],"412097":["Vannathirai"],"412098":["BBC World News"],"412099":["Celestial Movies"],"412101":["KBS World"],"412102":["ONE (Malay)"],"412103":["Asianet"],"412104":["Citra Entertainment"],"412105":["Hub Sports 3"],"412106":["TVB Xing He"],"412109":["CGTN"],"412110":["Zee TV"],"412112":["HBO Signature"],"412113":["Astro Sensasi"],"412115":["Zee Tamil"],"412116":["Kalaignar TV"],"412117":["Karisma"],"412119":["Nickelodeon Asia"],"412120":["Travelxp"],"412122":["Euronews"],"412123":["ADITHYA TV"],"412124":["Cartoon Network"],"412125":["Crime + Investigation"],"412126":["Asianet Movies"],"412128":["Sun TV"],"412129":["beIN Sports 2"],"412130":["NHK World Premium"],"412132":["Fox News Channel"],"412133":["Premier Sports"],"412134":["HITS MOVIES"],"412135":["TVB Jade"],"412136":["Nick Jr"],"412138":["SONY MAX"],"412139":["CCTV-4"],"412140":["Hub Premier 1"],"412141":["Vijay TV"],"412142":["Dragon TV"],"412143":["The Filipino Channel"],"412144":["Animax"],"412145":["Phoenix InfoNews Channel"],"412146":["BBC Earth"],"412147":["Arirang TV"],"412148":["Preview Channel"],"412149":["TVBS-NEWS"],"412150":["COLORS"],"412151":["CNBC"],"412153":["HGTV"],"412154":["Hub Sports 2"],"412155":["Astro Warna"],"412156":["Hub VVDrama"],"412157":["ABC Australia"],"412160":["Hub Sports 5"],"412161":["Discovery"],"412162":["CCM"],"412164":["Hub Premier 2 (HD)"],"412165":["FIGHT SPORTS"],"412167":["beIN Sports"],"412168":["ETTV ASIA"],"412169":["Hub Sports 1"],"412170":["Sony Entertainment Television"],"412172":["HISTORY"],"412174":["Cbeebies"],"412175":["beIN Sports 3"],"412176":["CTI TV"],"412178":["DW English"],"412179":["NHK WORLD - JAPAN"],"412180":["ONE"],"412181":["SPOTV"],"412182":["Hub Sports 6"],"412183":["TV5MONDE"],"412184":["Lifetime"],"412185":["Zee Cinema"],"412186":["Cinema One Global"],"412187":["SPOTV2"],"412188":["AXN"],"412189":["Phoenix Chinese Channel"],"412190":["France24"],"412191":["Hub E City"],"412192":["Zee Thirai"],"412193":["TVBS Asia"],"412194":["Hits"],"412195":["HBO Family"],"412197":["FashionTV"],"412198":["Hub Sports 4"],"412199":["Bloomberg Originals"],"412200":["Hub E City"],"412201":["CNN"],"412203":["KTV"],"413372":["TestChannel 993"],"429564":["Pesona"],"429984":["Sky News"],"430462":["Cricbuzz"],"430760":["Cricbuzz 2"],"435717":["Asianet"],"447056":["Hub Ruyi"],"450710":["Hub Premier 8"],"450711":["Hub Premier 3"],"451931":["Star Maa"],"451933":["maa movies"],"452962":["Hub Premier 5"],"455185":["Hub Sports 7"],"455186":["Hub Premier 6"],"455187":["Hub Sports 8"],"455314":["The Filipino Channel"],"459681":["StarHub"],"472551":["Hub Premier 4"],"476879":["ANC"],"482820":["Hub Sports 3"],"482821":["Hub Premier 8"],"482822":["Hub Sports 2"],"482823":["Hub Sports 7"],"482824":["Hub Sports 8"],"482827":["Hub Sports 1"],"482829":["Hub Premier 5"],"482830":["Hub Premier 6"],"482831":["Hub Sports 5"],"482832":["Hub Sports 4"],"482833":["Hub Sports 6"],"483423":["Hub Premier 7"],"484241":["Cinema One Global"],"484245":["(HD) Outdoor Channel"],"485760":["HITS NOW"],"491151":["ETTV Asia (HD)"],"491154":["GMA Life TV"],"491156":["Zee Tamil"],"491157":["CNN International (HD)"],"491159":["ANIPLUS"],"491163":["TVBS Asia"],"491164":["GMA News TV"],"491166":["Nick Jr."],"491167":["KTV"],"491168":["GMA Pinoy TV"],"491170":["Nickelodeon Asia"],"491172":["China Movie Channel"],"491348":["Sun Music"],"492453":["TVBS News"],"492456":["Adithya TV"],"492707":["beIN SPORTS 5"],"492708":["beIN SPORTS 4"],"492816":["Channel 5"],"492817":["Vasantham"],"492819":["KTV"],"492823":["CinemaWorld"],"492824":["CNN"],"492825":["Nick Jr."],"492826":["CBeebies"],"492827":["Hub Sports 2"],"492830":["FIFA+"],"492832":["Animax"],"492833":["Fox News Channel"],"492834":["Hub Sports 1"],"492836":["Hub Sports 3"],"492837":["CGTN"],"492838":["ONE"],"492839":["Euronews"],"492840":["ANC"],"492842":["Cinema One Global"],"492843":["HITS MOVIES"],"492845":["Hub Premier 4"],"492846":["Hub Premier 5"],"492847":["Hub Premier 6"],"492848":["Hub Premier 10"],"492850":["AXN"],"492851":["Hub VV Drama"],"492853":["Phoenix Chinese Channel"],"492856":["ETTV Asia"],"492858":["FoodON"],"492860":["SPL CH01"],"492861":["HGTV"],"492863":["BBC Lifestyle"],"492864":["Channel U"],"492865":["Cartoon Network"],"492866":["Crime + Investigation"],"492867":["Discovery"],"492868":["Lifetime"],"492869":["Astro Sensasi"],"492870":["ONE (Malay)"],"492871":["BBC News"],"492872":["CCTV-4"],"492873":["Hub Premier 11"],"492874":["CNA"],"492875":["COLORS Tamil"],"492876":["HISTORY"],"492877":["DreamWorks"],"492878":["TG Junior"],"492879":["Nickelodeon Asia"],"492880":["W-Sport"],"492881":["Dragon TV"],"492882":["Celestial Movies"],"492883":["CTI Asia"],"492885":["Hub Premier 3"],"492886":["Hub Premier 9"],"492887":["Hub Premier 7"],"492888":["Hub Premier 2"],"492889":["Hub Premier 8"],"492890":["ADITHYA TV"],"492891":["Kalaignar TV"],"492892":["BBC Earth"],"492893":["CNBC"],"492894":["Channel 8"],"492895":["Hub E City"],"492896":["TRACE Sport Stars"],"492897":["KBS World"],"492898":["Hub Ruyi"],"492899":["CCM"],"492900":["River Monsters"],"492901":["Suria"],"492902":["HITS"],"492903":["Hub Premier 1"],"492904":["COLORS"],"492905":["LIVE 1"],"492906":["Asianet"],"492907":["Asianet Movies"],"492908":["Kartoon Channel!"],"492909":["Citra Entertainment"],"492910":["Action Hollywood Movies"],"492914":["Astro Warna"],"492915":["Karisma"],"493279":["TestChannel 996"],"495472":["Fear Factor"],"495473":["Deal or No Deal"],"495493":["TestChannel2"],"501125":["BRTV International"],"501126":["WION"],"501127":["ABS-CBN News Channel"],"502440":["CGTN-Documentary"],"503775":["Hub Sports 4"],"503776":["Hub Sports 5"],"507899":["MasterChef"],"512067":["CCTV Entertainment"],"512068":["now Jelli (HD)"],"512070":["Dragon TV Intl"],"513276":["Ch 8 (HD)"],"513278":["Vasantham (HD)"],"513280":["Ch U (HD)"],"513282":["CNA (HD)"],"513284":["Suria (HD)"],"513286":["Ch 5 (HD)"],"516575":["Astro Prima"],"516576":["SPOTV (HD)"],"516577":["SPOTV 2 (HD)"],"516579":["Astro Ria"],"516581":["TechStorm (HD)"],"522552":["CGTN"],"522554":["Vijay"],"522555":["Sun TV"],"522557":["Times Now"],"522558":["Zoom TV"],"522559":["CCTV-4 (HD)"],"524981":["Hub Sports 7"],"524982":["Hub Sports 8"],"524983":["beIN SPORTS 4"],"524984":["beIN SPORTS 5"],"524985":["beIN SPORTS 3"],"524986":["Hub Sports 6"],"524987":["beIN SPORTS 2"],"524988":["beIN SPORTS"],"524989":["Cricbuzz"],"524990":["Cricbuzz 2"],"524991":["FIGHT SPORTS"],"530390":["Vijay Takkar"],"535294":["LIVE 2"],"535443":["That\'s 80s"],"535444":["That\'s Rock"],"535445":["That\'s 70s"],"536324":["ROCK Action (HD)"],"536325":["MTV India"],"536326":["STAR Plus"],"536327":["TVBS News"],"536328":["Lifetime (HD)"],"536329":["HITS (HD)"],"536330":["TechStorm (HD)"],"536331":["HITS MOVIES (HD)"],"536332":["HITS NOW (HD)"],"536333":["AXN (HD)"],"536334":["ETTV Asia (HD)"],"536335":["e-Le (HD)"],"536336":["Times Now"],"536337":["Zoom TV"],"536338":["HBO Family (HD)"],"536339":["MOLA Sport (HD)"],"536340":["EUROSPORT (HD)"],"536341":["BBC Earth (HD)"],"536342":["(HD) Outdoor Channel"],"536343":["HBO Signature (HD)"],"536344":["Cartoonito (HD)"],"536345":["Cartoon Network (HD)"],"536346":["SAB TV"],"536347":["BRTV International"],"536348":["Hunan International"],"536349":["Dragon TV Intl"],"536350":["CCTV Entertainment"],"536351":["China Movie Channel"],"536352":["CNBC Asia (HD)"],"536353":["Bloomberg TV (HD)"],"536354":["Celestial Movies(HD)"],"536355":["CCM"],"536356":["Adithya TV"],"536357":["Sun Music"],"536358":["KTV"],"536359":["Zee Thirai"],"536360":["Colors Tamil"],"536361":["Sun TV"],"536362":["Drama Channel"],"536363":["tvN"],"536364":["The Filipino Ch (HD)"],"536365":["GMA News TV Intl"],"536366":["SEA Today"],"536367":["Food Network (HD)"],"536368":["AFN (HD)"],"536369":["TRACE Sport Stars"],"536370":["TLC (HD)"],"536371":["KBS World (HD)"],"536372":["STAR Gold"],"536373":["Animal Planet (HD)"],"536374":["Sony MAX"],"536375":["Pesona"],"536376":["CGTN-Documentary"],"536377":["NHK World Premium"],"536378":["Jia Le Channel (HD)"],"536379":["Nickelodeon Asia"],"536380":["Animax (HD)"],"536381":["Citra Drama (HD)"],"536382":["beIN SPORTS 2 (HD)"],"536383":["WION"],"536384":["BBC Lifestyle (HD)"],"536385":["MTV LIVE"],"536386":["GMA Life TV"],"536387":["CM+"],"536388":["maa movies"],"536389":["Star Maa"],"536390":["CCTV-4 (HD)"],"536391":["CTI Asia (HD)"],"536392":["HGTV (HD)"],"536393":["Star Bharat"],"536394":["Colors"],"536395":["ABC Australia"],"536396":["mio Sports"],"536397":["mio Sports 2"],"536398":["TVBS Asia"],"536399":["Premier Sports"],"536400":["GMA Pinoy TV"],"536401":["Discovery Ch (HD)"],"536402":["Discovery Asia (HD)"],"536403":["C+I (HD)"],"536404":["HISTORY (HD)"],"536405":["tvN HD (Mandarin)"],"536406":["beIN SPORTS (HD)"],"536407":["TVB Jade (HD)"],"536408":["ONE HD (Mandarin)"],"536409":["now Jelli (HD)"],"536410":["TVB Xing He (HD)"],"536411":["Sky News"],"536412":["CGTN (HD)"],"536413":["NHK World - Japan"],"536414":["ONE HD (Malay)"],"536415":["Astro Prima"],"536416":["Astro Ria"],"536417":["CNN International"],"536418":["ETTV Asia News (HD)"],"536419":["beIN SPORTS 4 (HD)"],"536420":["CINEMAX (HD)"],"536421":["HBO"],"536422":["HBO Hits (HD)"],"536423":["BBC News (HD)"],"536424":["Nick Jr (HD)"],"536425":["DreamWorks (HD)"],"536426":["ANIPLUS"],"536427":["Fight Sports (HD)"],"536428":["SPOTV (HD)"],"536429":["SPOTV 2 (HD)"],"536430":["beIN SPORTS 3 (HD)"],"536431":["Sony YAY"],"536432":["tvN Movies On Demand"],"536433":["SET (HINDI)"],"536434":["Zee TV"],"536435":["DW (English)"],"536436":["FOX News Channel"],"536437":["ROCK ENT (HD)"],"536438":["France 24 (English)"],"536439":["France 24 (French)"],"536440":["Phoenix InfoNews"],"536441":["Asianet"],"536442":["Asianet Movies"],"536443":["Vijay TV (HD)"],"536444":["Zee Tamil"],"536445":["CBeebies (HD)"],"516580":["Zee Thirai"],"535941":["SPL CH02"],"522556":["Hunan International"],"492911":["NOW 80s"],"492912":["NOW 70s"],"492913":["NOW Rock"],"CWIN":["SUPER FREE","华娱台","華娛台"],"SVAR":["SUPER獎門人"],"SEYT":["SUPER EYT"],"SFOO":["SUPER識食"],"STRA":["SUPER識嘆"],"SMUS":["SUPER Music"],"SGOL":["SUPER金曲"],"SSIT":["SUPER煲劇"],"STVM":["SUPER單元劇"],"SDOC":["真情SUPER煲"],"SSPT":["SUPER Sports"],"C18":["myTV SUPER 18台"],"TVG":["黄金翡翠台"],"J":["翡翠台","翡翠台(超高清)","翡翠台4K"],"B":["TVB Plus"],"C":["無綫新聞台","互動窗 1","互動窗 2"],"P":["明珠台"],"CTVC":["千禧經典台"],"CTVS":["亞洲劇台"],"CDR3":["華語劇台"],"TVO":["黃金華劇台"],"CTVE":["娛樂新聞台"],"CCOC":["戲曲台"],"KID":["SUPER Kids Channel"],"ZOO":["ZooMoo"],"CNIKO":["Nickelodeon"],"CNIJR":["Nick Jr"],"CCLM":["粵語片台"],"CMAM":["美亞電影台"],"CTHR":["Thrill"],"CCCM":["天映經典頻道"],"CMC":["中國電影頻道"],"CRTX":["ROCK Action"],"POPC":["PopC"],"ACTM":["Action Hollywood Movies"],"RCM":["Rialto Classic Movies (RCM)"],"CKIX":["KIX"],"TRSP":["TRACE Sport Stars"],"LNH":["Love Nature"],"LN4":["Love Nature 4K"],"SMS":["Global Trekker"],"PETC":["Pet Club TV"],"GLBT":["Globetrotter"],"DOCV":["Docsville"],"PULS":["Pulse Documentaries"],"CRTE":["ROCK綜藝娛樂"],"CAXN":["AXN"],"RKEX":["ROCK Extreme"],"CANI":["Animax"],"CJTV":["tvN"],"CTS1":["無線衛星亞洲台"],"CRE":["創世電視"],"FBX":["FashionBox"],"CMEZ":["Mezzo Live"],"CC1":["中央電視台綜合頻道 (港澳版)"],"CGD":["CGTN (中國環球電視網)记录頻道"],"CGE":["CGTN (中國環球電視網)英語頻道"],"DTV":["東方衛視國際頻道"],"PCC":["鳳凰衛視中文台"],"PIN":["鳳凰衛視資訊台"],"PHK":["鳳凰衛視香港台"],"CC4":["中國中央電視台中文國際頻道"],"CCE":["中國中央電視台娛樂頻道"],"CCO":["中國中央電視台戲曲頻道"],"YNTV":["雲南瀾湄國際衛視"],"AHTV":["安徽廣播電視台國際頻道"],"BJTV":["北京電視台國際頻道"],"GXTV":["廣西電視台國際頻道"],"FJTV":["福建海峽衛視國際頻道"],"HNTV":["湖南電視台國際頻道"],"JSTV":["江蘇電視台國際頻道"],"GBTV":["廣東廣播電視台大灣區衛視頻道"],"ZJTV":["浙江電視台國際頻道"],"SZTV":["深圳衛視國際頻道"],"NOW7":["NOW 70s"],"NOW8":["NOW 80s"],"NOWR":["NOW ROCK"],"NOW9":["NOW 90s00s"],"CONC":["Concerto"],"TRUR":["TRACE Urban"],"CTSN":["無線衛星新聞台"],"CCNA":["亞洲新聞台"],"CJAZ":["半島電視台英語頻道"],"CF24":["France 24"],"CDW1":["DW"],"CNHK":["NHK World-Japan"],"CARI":["Arirang TV"],"NSWD":["NewsWorld"],"OL00":["全運800台"],"OL01":["全運801台"],"OL02":["全運802台"],"OL03":["全運803台"],"OL04":["全運804台"],"OL05":["全運805台"],"OL06":["全運806台"],"EVT2":["myTV SUPER直播足球2台"],"EVT3":["myTV SUPER直播足球3台"],"EVT4":["myTV SUPER直播足球4台"],"EVT5":["myTV SUPER直播足球5台"],"EVT6":["myTV SUPER直播足球6台"],"EVT7":["myTV SUPER直播足球7台"],"TEST":["測試頻道"],"1":["CCTV-1","CCTV 1","CCTV综合","CCTV-1 综合"],"2":["CCTV2"],"3":["CCTV3"],"4":["CCTV4"],"5":["CCTV5"],"6":["CCTV5+"],"7":["CCTV6"],"8":["CCTV7"],"9":["CCTV8"],"10":["CCTV9"],"11":["CCTV10"],"12":["CCTV11"],"13":["CCTV12"],"14":["CCTV13"],"15":["CCTV14"],"16":["CCTV15"],"17":["CCTV-17","CCTV-17 农业农村"],"21":["海峡卫视"],"22":["CCTV4EUO"],"23":["CCTV4AME"],"27":["湖南卫视"],"28":["浙江卫视"],"29":["江苏卫视"],"30":["北京卫视"],"31":["东方卫视"],"32":["安徽卫视"],"33":["广东卫视"],"34":["深圳卫视"],"36":["辽宁卫视"],"37":["旅游卫视"],"38":["山东卫视"],"39":["天津卫视"],"40":["重庆卫视"],"41":["东南卫视"],"42":["甘肃卫视"],"43":["广西卫视"],"44":["贵州卫视"],"45":["河北卫视"],"46":["黑龙江卫视"],"47":["河南卫视"],"48":["湖北卫视"],"50":["江西卫视"],"51":["吉林卫视"],"52":["内蒙古卫视"],"53":["宁夏卫视"],"54":["山西卫视"],"55":["陕西卫视"],"56":["四川卫视"],"57":["新疆卫视"],"58":["云南卫视"],"59":["青海卫视"],"61":["兵团卫视"],"62":["哈哈炫动"],"63":["延边卫视"],"65":["黄河卫视"],"68":["厦门卫视"],"69":["金鹰卡通"],"70":["康巴卫视"],"71":["西藏卫视"],"72":["三沙卫视"],"73":["中国教育1台"],"74":["中国教育2台"],"106":["CCTV4K"],"141":["凤凰中文"],"142":["凤凰资讯"],"143":["凤凰香港"],"189":["HKS"],"1424":["风云足球"],"1639":["CHC高清电影"],"1642":["求索记录"],"1643":["CHC动作电影"],"1644":["CHC家庭电影"],"1645":["梨园"],"1646":["风云音乐"],"1647":["第一剧场"],"1648":["风云剧场"],"1649":["世界地理"],"1651":["怀旧剧场"],"1652":["兵器科技"],"1653":["女性时尚"],"1657":["高尔夫网球"],"1658":["央视精品"],"1662":["汽摩"],"1667":["天元围棋"],"1670":["早期教育"],"1671":["证券资讯"],"1673":["央视台球"],"1674":["茶"],"1675":["武术世界"],"1677":["发现之旅"],"1678":["环球奇观"],"1679":["国学"],"1680":["文物宝库"],"1683":["老故事"],"1684":["快乐垂钓"],"1685":["书画"],"1695":["四海钓鱼"],"1697":["环球旅游"],"1712":["财富天下"],"1860":["中国教育4台"],"1864":["CCTV16"],"1969":["CHC影迷电影"],"9999":["noepg"],"20":["CGTN"],"67":["卡酷动画"],"75":["中国教育3台"],"334":["天津文艺"],"335":["天津影视"],"336":["天津都市"],"337":["天津体育"],"340":["天津新闻"],"344":["江苏城市"],"345":["江苏影视"],"347":["江苏休闲体育"],"348":["优漫卡通"],"349":["江苏新闻"],"351":["江苏综艺"],"534":["贵州影视"],"535":["贵州大众"],"537":["贵州科教"],"555":["宁夏经济"],"557":["宁夏少儿"],"565":["河北都市"],"566":["河北影视"],"567":["河北少儿"],"568":["河北经济"],"609":["新动漫"],"657":["浙江经视"],"658":["浙江钱江都市"],"659":["浙江科教影视"],"660":["浙江民生休闲"],"661":["浙江公共新闻"],"662":["浙江少儿"],"728":["山东齐鲁"],"729":["山东体育"],"730":["山东农科"],"732":["山东少儿"],"734":["山东综艺"],"735":["山东生活"],"832":["河南民生"],"833":["河南法制"],"834":["河南电视剧"],"835":["河南新闻"],"837":["河南公共"],"838":["河南新农村"],"839":["河南都市"],"892":["广西综艺"],"893":["广西都市"],"895":["广西影视"],"897":["广西新闻"],"932":["云南娱乐"],"933":["云南都市"],"934":["云南影视"],"936":["云南少儿"],"1025":["安徽经济生活"],"1026":["安徽影视"],"1027":["安徽公共"],"1028":["安徽农业科教"],"1079":["湖北综合"],"1080":["湖北影视"],"1081":["湖北教育"],"1082":["湖北生活"],"1084":["湖北公共"],"1085":["湖北经济"],"1086":["湖北垄上"],"1128":["海南综合"],"1129":["海南新闻"],"1130":["海南公共"],"1138":["重庆影视"],"1139":["重庆新闻"],"1146":["重庆少儿"],"1152":["甘肃少儿"],"1153":["甘肃公共"],"1155":["甘肃文化"],"1173":["北京纪实"],"1174":["BTV文艺"],"1176":["BTV影视"],"1177":["BTV财经"],"1178":["BTV体育"],"1179":["BTV生活"],"1181":["BTV新闻"],"1185":["内蒙蒙语卫视"],"1186":["内蒙新闻综合"],"1187":["内蒙经济生活"],"1188":["内蒙文体娱乐"],"1189":["内蒙农牧"],"1190":["内蒙少儿"],"1215":["黑龙江影视"],"1216":["黑龙江文体"],"1217":["黑龙江都市"],"1218":["黑龙江新闻法治"],"1220":["黑龙江少儿"],"1258":["东方财经"],"1263":["新视觉"],"1273":["法治天地"],"1274":["金色"],"1276":["全纪实"],"1277":["欢笑剧场"],"1279":["动漫秀场"],"1281":["生活时尚"],"1282":["都市剧场"],"1283":["游戏风云"],"1284":["上视新闻"],"1285":["第一财经"],"1286":["东方影视"],"1287":["五星体育"],"1293":["中国交通"],"1317":["湖南娱乐"],"1318":["湖南电视剧"],"1320":["湖南电影"],"1323":["湖南经视"],"1324":["金鹰纪实"],"1353":["SCTV2"],"1354":["SCTV3"],"1355":["SCTV4"],"1356":["SCTV5"],"1359":["峨嵋电影"],"1361":["SCTV8"],"1363":["CDTV1"],"1364":["CDTV2"],"1365":["CDTV3"],"1366":["CDTV4"],"1367":["CDTV5"],"1368":["CDTV6"],"1425":["辽宁都市"],"1426":["辽宁影视剧"],"1427":["辽宁青少"],"1428":["辽宁生活"],"1429":["辽宁公共"],"1430":["辽宁北方"],"1431":["辽宁体育"],"1432":["辽宁经济"],"1434":["嘉佳卡通"],"1436":["广东体育"],"1439":["广东新闻"],"1638":["求索科学"],"1640":["求索动物"],"1759":["珠江"],"1857":["魅力足球"],"1865":["广东影视"],"1866":["广东综艺"],"1867":["广东经济"],"1868":["广东少儿"],"1940":["新疆哈语"],"1942":["新疆维语"],"1943":["新疆汉语"],"1944":["新疆体育"],"1945":["新疆少儿"],"1953":["宁夏文旅"],"1954":["青海都市"],"1955":["青海经视"],"1957":["甘肃科教"],"1960":["陕西新闻"],"1961":["陕西都市"],"1962":["陕西银龄"],"1963":["陕西秦腔"],"1964":["陕西体育"],"1967":["云南康旅"],"1971":["贵州生态"],"1984":["四川乡村"],"1985":["重庆社会与法"],"1986":["重庆红岩文化"],"1987":["重庆新农村"],"1988":["重庆红叶"],"1990":["海南自贸"],"1991":["海南文旅"],"1992":["海南少儿"],"1995":["大湾区卫视"],"1996":["广东民生"],"2002":["湖南爱晚"],"2008":["山东新闻"],"2011":["江西都市"],"2012":["江西经济"],"2014":["江西公共"],"2015":["江西新闻"],"2019":["江西少儿"],"2020":["福建综合"],"2021":["福建公共"],"2022":["福建新闻"],"2023":["福建电视剧"],"2024":["福建旅游"],"2025":["福建经济"],"2026":["福建体育"],"2027":["福建少儿"],"2029":["安徽综艺体育"],"2033":["黑龙江农业科教"],"2034":["吉视都市"],"2035":["吉视影视"],"2036":["吉视乡村"],"2037":["吉视综艺文化"],"2042":["河北文旅公共"],"2043":["河北三农"],"2045":["天津教育"],"2046":["山西经济与科技"],"2047":["山西影视"],"2048":["山西社会与法治"],"2049":["山西文体生活"],"2051":["吉视生活"],"120":["翡翠台"],"121":["明珠台"],"122":["TVB经典台"],"123":["无线新闻"],"132":["美亚高清电影台"],"157":["NHKWorld"],"164":["now财经台"],"187":["ViuTV"],"190":["J2"],"194":["MEZZOLIVE"],"199":["Lifetime"],"214":["公视2"],"217":["华视新闻"],"218":["GoodTV2"],"220":["创世电视"],"222":["壹电视综合台"],"223":["壹电视电影台"],"226":["BBCWorldNews"],"229":["BBC"],"230":["CN卡通"],"232":["CNBCHongKong"],"234":["FashionTV"],"244":["DW"],"245":["TV5Monde"],"247":["france24"],"249":["SkyNews"],"267":["天映经典"],"276":["TFC"],"287":["cinemaworld"],"296":["CGTNDocumentary"],"310":["TraceSports"],"311":["Outdoor"],"321":["HITS"],"1446":["CNN"],"1447":["民视无线台"],"1449":["台视"],"1450":["大爱一台"],"1451":["中视"],"1452":["人间卫视"],"1453":["华视"],"1454":["公视"],"1456":["GoodTV"],"1457":["原住民"],"1458":["客家"],"1460":["Discovery"],"1461":["TLC旅游生活"],"1462":["动物星球"],"1464":["MOMO亲子台"],"1465":["东森幼幼台"],"1466":["纬来综合台"],"1467":["八大第一台"],"1468":["八大综合台"],"1469":["三立台湾台"],"1470":["三立都会台"],"1472":["东森综合台"],"1473":["东森超视"],"1476":["中天综合台"],"1478":["年代MUCH"],"1479":["中天娱乐台"],"1480":["东森戏剧台"],"1481":["八大戏剧台"],"1482":["TVBS欢乐台"],"1483":["纬来戏剧台"],"1484":["高点综合台"],"1485":["JET综合台"],"1489":["壹电视新闻台"],"1490":["年代新闻台"],"1491":["东森新闻台"],"1493":["民视新闻台"],"1494":["三立新闻台"],"1495":["TVBS新闻台"],"1496":["TVBS"],"1497":["东森财经新闻台"],"1498":["非凡新闻台"],"1502":["东森电影台"],"1503":["纬来电影台"],"1504":["LSTime电影台"],"1505":["HBO"],"1506":["东森洋片台"],"1507":["AXN"],"1508":["好莱坞电影台"],"1511":["CINEMAX"],"1512":["纬来体育台"],"1517":["纬来日本台"],"1518":["国兴卫视"],"1520":["靖天综合台"],"1521":["靖天资讯台"],"1522":["信吉"],"1523":["靖洋戏剧台"],"1526":["冠军"],"1527":["台湾艺术台"],"1528":["全大"],"1529":["非凡商业台"],"1530":["三立财经新闻台"],"1532":["运通财经"],"1533":["SBN全球财经台"],"1534":["诚心"],"1536":["MTV"],"1537":["靖天映画"],"1538":["海豚综合台"],"1539":["霹雳台湾台"],"1540":["十方法界"],"1541":["信大"],"1542":["华藏卫视"],"1543":["Z"],"1544":["佛卫慈悲台"],"1547":["天良综合台"],"1548":["正德"],"1551":["高点育乐台"],"1552":["冠军梦想台"],"1554":["八大娱乐台"],"1555":["大立"],"1559":["幸福空间居家台"],"1560":["ChannelNewsAsia"],"1561":["BloombergTV"],"1562":["ArirangTV"],"1563":["大爱二台"],"1564":["台视新闻台"],"1565":["台视财经台"],"1566":["台视综合台"],"1568":["BBCLifestyle"],"1569":["DREAMWORKS"],"1570":["WarnerTV"],"1571":["靖天欢乐台"],"1572":["靖天育乐台"],"1573":["HBO"],"1574":["HBOHits"],"1575":["HBOSignature"],"1576":["HBOFamily"],"1577":["BlueAntEntertainment"],"1578":["靖天日本台"],"1579":["FoodNetwork美食台"],"1580":["tvN"],"1582":["韩国娱乐台KMTV"],"1585":["Discovery科学"],"1586":["DiscoveryAsia"],"1588":["TravelChannel"],"1589":["亚洲美食"],"1590":["DMAX"],"1591":["寰宇新闻"],"1592":["亚洲旅游台"],"1593":["梅迪奇艺术"],"1594":["博斯运动二台"],"1595":["博斯网球台"],"1596":["博斯无限台"],"1597":["博斯高球1台"],"1598":["博斯高球2台"],"1599":["博斯魅力网"],"1600":["博斯运动一台"],"1601":["博斯无限二台"],"1603":["Euronews"],"1604":["Nickelodeon"],"1605":["NickJr."],"1606":["达文西"],"1607":["CBeebies"],"1609":["BBCEarth"],"1611":["靖洋卡通台"],"1612":["靖天卡通台"],"1637":["三立综合台"],"1736":["ABCAustralia"],"1745":["NHKWorldPremium"],"1760":["龙华偶像"],"1761":["龙华戏剧"],"1762":["龙华电影"],"1764":["龙华经典"],"1765":["龙华影剧"],"1766":["龙华洋片"],"1767":["民视第一台"],"1768":["民视台湾台"],"1769":["民视"],"1770":["中视菁采台"],"1772":["TVBS精采台"],"1778":["华艺中文"],"1779":["TRACEUrban"],"1783":["History"],"1786":["SMART知识"],"1790":["靖天戏剧台"],"1791":["靖天电影台"],"1792":["AMC"],"1797":["中视经典"],"1801":["中视新闻"],"1803":["CI"],"1804":["cnex"],"1805":["采昌影剧"],"1806":["智林体育"],"1807":["aljazeera"],"1808":["影迷数位纪实"],"1809":["影迷数位电影"],"1810":["ELTV"],"1811":["靖天国际"],"1812":["龙华动画"],"1813":["MTV综合"],"1815":["爱尔达体育2"],"1816":["LUXETV"],"1817":["rollor"],"1818":["亚洲综合"],"1819":["寰宇HD综合"],"1820":["纬来精采"],"1821":["Ettoday"],"1822":["八大优"],"1823":["台湾戏剧"],"1824":["爱尔达影剧"],"1827":["寰宇财经"],"1828":["CatchPlay电影"],"1829":["MyCinemaEurope"],"1832":["爱尔达体育1"],"1833":["爱尔达体育3"],"1836":["美食星球"],"1837":["EYE旅游"],"1838":["爱尔达综合"],"1840":["三立戏剧"],"1841":["EYE戏剧"],"1842":["曼迪日本"],"1845":["华艺影剧"],"1846":["唯心电视"],"1853":["Animax"],"1870":["globaltrekker"],"368550":["港台电视31"],"368551":["港台电视32"],"368552":["港台电视33"],"368553":["港台电视34"],"368554":["港台电视35"],"410273":["ViuTVsix"],"410274":["ViuTV"],"410275":["Viu 頻道"],"410276":["Now華劇台"],"410278":["NowJelli"],"410279":["HBO Hits"],"410280":["HBO Family"],"410281":["CINEMAX"],"410282":["HBO Signature"],"410283":["HBO"],"410284":["MOVIE MOVIE"],"410285":["Now 爆谷台"],"410286":["Now爆谷星影台"],"410287":["Animax"],"410288":["tvN"],"410289":["KBS World"],"410291":["MOOV 演唱會/MV台"],"410292":["熊貓 TV"],"410293":["Discovery Asia"],"410294":["Discovery Channel"],"410295":["動物星球頻道"],"410296":["Discovery 科學頻道"],"410297":["DMAX"],"410298":["TLC旅遊生活頻道"],"410299":["Love Nature"],"410300":["Love Nature 4K"],"410301":["BBC Earth"],"410302":["戶外頻道"],"410303":["罪案 + 偵緝"],"410304":["HISTORY"],"410305":["CNN 國際新聞網絡"],"410306":["CNBC"],"410307":["BBC News"],"410308":["Bloomberg Television"],"410309":["亞洲新聞台"],"410310":["Sky News"],"410311":["DW (English)"],"410312":["半島電視台英語頻道"],"410314":["France 24"],"410315":["NHK WORLD-JAPAN"],"410316":["RT"],"410342":["中國環球電視網"],"410343":["Now直播台"],"410344":["Now新聞台"],"410345":["Now財經台"],"410346":["Now報價台"],"410355":["鳳凰衛視資訊台"],"410359":["Cartoon Network"],"410360":["Nickelodeon"],"410361":["CBeebies"],"410362":["Moonbug"],"410363":["Nick Jr."],"410364":["Da Vinci"],"410365":["BBC Lifestyle"],"410366":["AXN"],"410367":["ROCK Entertainment"],"410368":["Lifetime"],"410369":["Food Network"],"410370":["亞洲美食台"],"410371":["Netflix"],"410372":["中天亞洲台"],"410374":["CCTV-1"],"410375":["CCTV-4"],"410376":["大灣區衛視"],"410377":["中央電視台新聞頻道"],"410378":["鳳凰衛視中文台"],"410379":["東方衛視國際頻道"],"410381":["三沙衛視"],"410383":["ABC Australia"],"410384":["Now Sports 4K 1"],"410385":["Now Sports 4K 2"],"410386":["Now Sports 4K 3"],"410387":["Now Sports Premier League TV"],"410388":["Now Sports 英超1台"],"410389":["Now Sports 英超2台"],"410390":["Now Sports 英超3台"],"410391":["Now Sports 英超4台"],"410392":["Now Sports 英超5台"],"410395":["Now Sports 精選"],"410396":["Now Sports 1"],"410397":["Now Sports 2"],"410398":["Now Sports 3"],"410399":["Now Sports 4"],"410400":["Now Sports 5"],"410401":["Now Sports 6"],"410402":["Now Sports 7"],"410403":["beIN SPORTS 1"],"410404":["beIN SPORTS 2"],"410405":["曼聯電視頻道"],"410406":["Now Sports 641"],"410407":["NBA TV"],"410408":["beIN SPORTS 3"],"410409":["beIN SPORTS 4"],"410410":["beIN SPORTS 5"],"410411":["beIN SPORTS 6"],"410412":["Now Sports 651"],"410413":["Now Sports 652"],"410414":["Now668"],"410417":["Astro Cricket"],"410418":["Premier Sports"],"410419":["Now Sports Plus"],"410420":["Now Golf 2"],"410421":["Now Golf 3"],"410422":["NHK World Premium"],"412087":["HITS MOVIES"],"412204":["HITS"],"415544":["Now Sports 英超6台"],"415545":["Now Sports 英超7台"],"420138":["TV5MONDE Style"],"421332":["Now Sports 647"],"460736":["TVBS精采台"],"460810":["TVB 互動新聞台"],"460917":["TVB 翡翠台"],"460923":["TVB Pearl"],"460924":["TVB J2"],"473830":["Now 影視點播站"],"534189":["euronews"],"534827":["Now Sports 618"],"368322":["PopC"],"368323":["娛樂新聞台"],"368325":["千禧經典台"],"368326":["tvN"],"368327":["半島電視台英語頻道"],"368328":["myTV SUPER直播足球4台"],"368329":["創世電視"],"368330":["中國電影頻道"],"368332":["ROCK綜藝娛樂"],"368333":["Animax"],"368334":["myTV SUPER 18台"],"368335":["亞洲劇台"],"368336":["Nickelodeon"],"368337":["NHK World-Japan"],"368338":["鳳凰衛視中文台"],"368339":["Thrill"],"368340":["東方衛視國際頻道"],"368341":["Mezzo Live"],"368342":["France 24"],"368343":["鳳凰衛視資訊台"],"368344":["華語劇台"],"368345":["myTV SUPER直播足球3台"],"368346":["CGTN (中國環球電視網)英語頻道"],"368347":["CGTN (中國環球電視網)記錄頻道"],"368348":["美亞電影台"],"368350":["KIX"],"368351":["黃金華劇台"],"368353":["戲曲台"],"368355":["Love Nature"],"368356":["Global Trekker"],"368357":["無線衛星亞洲台"],"368358":["黃金翡翠台"],"368359":["無綫新聞台"],"368361":["TVB Plus"],"368362":["ROCK Action"],"368363":["無線衛星新聞台"],"368364":["Love Nature 4K"],"368365":["AXN"],"368366":["翡翠台"],"368367":["Nick Jr"],"368368":["ZooMoo"],"368369":["明珠台"],"368370":["Arirang TV"],"368371":["天映經典頻道"],"368372":["鳳凰衛視香港台"],"368373":["DW"],"368374":["FashionBox"],"368375":["中央電視台綜合頻道 (港澳版)"],"368376":["SUPER FREE"],"368377":["亞洲新聞台"],"368378":["測試頻道"],"368379":["myTV SUPER直播足球5台"],"368380":["SUPER Kids Channel"],"368381":["粵語片台"],"397763":["myTV SUPER直播足球2台"],"398976":["myTV SUPER直播足球6台"],"410424":["TV5MONDE ASIE"],"410425":["France 24 (French)"],"410426":["GMA Pinoy TV"],"410427":["GMA Life TV"],"410428":["GMA News TV"],"410429":["TFC"],"410430":["Sony TV (India)"],"410431":["Sony MAX"],"410432":["Sony SAB"],"410433":["MTV India"],"410435":["Zee Cinema International"],"410436":["Zee TV"],"410437":["Zee News"],"410438":["Star Gold"],"410439":["STAR PLUS"],"410440":["Star Bharat"],"410445":["冰火頻道"],"410450":["成人極品台"],"416310":["myTV SUPER直播足球7台"],"420293":["COLORS"],"425921":["福建海峽衛視國際頻道"],"425922":["北京電視台國際頻道"],"425923":["湖南電視台國際頻道"],"425924":["中國中央電視台戲曲頻道"],"425925":["中國中央電視台娛樂頻道"],"425926":["廣東廣播電視台大灣區衛視頻道"],"425927":["深圳衛視國際頻道"],"425928":["江蘇電視台國際頻道"],"425930":["安徽廣播電視台國際頻道"],"425931":["中國中央電視台中文國際頻道"],"425932":["浙江電視台國際頻道"],"425933":["雲南瀾湄國際衛視"],"430245":["TRACE Urban"],"430246":["Concerto"],"430247":["NewsWorld"],"430248":["Action Hollywod Movies"],"430249":["TRACE Sport Stars"],"430250":["PULSE Documentaries"],"430251":["NOW 80s"],"430252":["NOW 90s00s"],"430253":["NOW 70s"],"430254":["Globetrotter"],"430255":["Rialto Classic Movies (RCM)"],"430256":["Pet Club TV"],"430257":["DocsVille"],"430258":["NOW ROCK"],"430550":["SUPER EYT"],"430551":["SUPER煲劇"],"430552":["SUPER識嘆"],"430553":["SUPER Music"],"430554":["SUPER識食"],"430555":["SUPER劇場"],"430853":["SUPER Sports"],"430854":["SUPER獎門人"],"431161":["SUPER金曲"],"431446":["SUPER話當年 (免費)"],"443416":["ROCK Extreme"]},"regex":[]}'

# nowtv/config.json：Now TV 频道表（140 个频道）。
EMBEDDED_NOWTV_CONFIG_JSON = '{"096":{"name":"ViuTV","logo":"https://images.now-tv.com/shares/channelPreview/img/zh_tw/color/ch96_170_122"},"099":{"name":"ViuTV6","logo":"https://images.now-tv.com/shares/channelPreview/img/zh_tw/color/ch99_170_122"},"102":{"name":"Viu 頻道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch102_170_122"},"105":{"name":"Now华剧台","logo":"https://images.now-tv.com/shares/channelPreview/img/zh_tw/color/ch105_170_122"},"106":{"name":"Now 影視點播站","logo":"https://images.now-tv.com/shares/channelPreview/img/zh_tw/color/ch106_170_122"},"108":{"name":"NowJelli","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch108_170_122"},"111":{"name":"HBO Hits","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch111_170_122"},"112":{"name":"HBO Family","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch112_170_122"},"113":{"name":"CINEMAX","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch113_170_122"},"114":{"name":"HBO Signature","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch114_170_122"},"115":{"name":"HBO","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch115_170_122"},"116":{"name":"MOVIE MOVIE","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch116_170_122"},"119":{"name":"HITS MOVIES","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch119_170_122"},"133":{"name":"Now 爆谷台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch133_170_122"},"138":{"name":"Now爆谷星影台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch138_170_122"},"150":{"name":"Animax","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch150_170_122"},"155":{"name":"tvN","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch155_170_122"},"156":{"name":"KBS World","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch156_170_122"},"168":{"name":"MOOV 演唱會.MV台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch168_170_122"},"200":{"name":"熊貓 TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch200_170_122"},"208":{"name":"Discovery Asia","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch208_170_122"},"209":{"name":"Discovery Channel","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch209_170_122"},"210":{"name":"动物星球频道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch210_170_122"},"211":{"name":"Discovery 科学頻道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch211_170_122"},"212":{"name":"DMAX","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch212_170_122"},"213":{"name":"TLC旅遊生活頻道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch213_170_122"},"217":{"name":"Love Nature","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch217_170_122"},"218":{"name":"Love Nature 4K","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch218_170_122"},"220":{"name":"BBC Earth","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch220_170_122"},"221":{"name":"戶外頻道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch221_170_122"},"222":{"name":"罪案 + 偵緝","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch222_170_122"},"223":{"name":"HISTORY","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch223_170_122"},"316":{"name":"CNN","logo":"https://images.now-tv.com/shares/channelPreview/img/zh_tw/color/ch316_170_122"},"319":{"name":"CNBC","logo":"https://images.now-tv.com/shares/channelPreview/img/zh_tw/color/ch319_170_122"},"320":{"name":"BBC News","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch320_170_122"},"321":{"name":"Bloomberg Television","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch321_170_122"},"322":{"name":"亚洲新闻台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch322_170_122"},"323":{"name":"Sky News","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch323_170_122"},"324":{"name":"DW (English)","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch324_170_122"},"325":{"name":"半岛电视台英语频道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch325_170_122"},"326":{"name":"euronews","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch326_170_122"},"327":{"name":"France 24","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch327_170_122"},"328":{"name":"NHK WORLD-JAPAN","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch328_170_122"},"329":{"name":"RT","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch329_170_122"},"330":{"name":"CGTN","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch330_170_122"},"331":{"name":"Now直播台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch331_170_122"},"332":{"name":"Now新闻台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch332_170_122"},"333":{"name":"Now财经台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch333_170_122"},"336":{"name":"Now报价台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch336_170_122"},"366":{"name":"凤凰卫视资讯台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch366_170_122"},"400":{"name":"智力乐园","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch400_170_122"},"443":{"name":"Cartoon Network","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch443_170_122"},"444":{"name":"Nickelodeon","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch444_170_122"},"447":{"name":"CBeebies","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch447_170_122"},"448":{"name":"Moonbug","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch448_170_122"},"449":{"name":"Nick Jr.","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch449_170_122"},"460":{"name":"Da Vinci","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch460_170_122"},"502":{"name":"BBC Lifestyle","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch502_170_122"},"512":{"name":"AXN","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch512_170_122"},"513":{"name":"HITS","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch513_170_122"},"517":{"name":"ROCK Entertainment","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch517_170_122"},"525":{"name":"Lifetime","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch525_170_122"},"526":{"name":"Food Network","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch526_170_122"},"527":{"name":"亚洲美食台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch527_170_122"},"535":{"name":"Netflix","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch535_170_122"},"538":{"name":"中天亚洲台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch538_170_122"},"540":{"name":"深圳卫视","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch540_170_122"},"541":{"name":"CCTV1","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch541_170_122"},"542":{"name":"CCTV4","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch542_170_122"},"543":{"name":"大湾区卫视","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch543_170_122"},"545":{"name":"CCTV13","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch545_170_122"},"548":{"name":"凤凰卫视中文台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch548_170_122"},"551":{"name":"东方卫视国际频道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch551_170_122"},"553":{"name":"三沙卫视","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch553_170_122"},"561":{"name":"ABC Australia","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch561_170_122"},"600":{"name":"体育节目精选","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch600_170_122"},"611":{"name":"Now Sports 4K 1","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch611_170_122"},"612":{"name":"Now Sports 4K 2","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch612_170_122"},"613":{"name":"Now Sports 4K 3","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch613_170_122"},"616":{"name":"Now Sports 4K 616","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch616_170_122"},"617":{"name":"Now Sports 4K 617","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch617_170_122"},"618":{"name":"Now Sports 618","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch618_170_122"},"619":{"name":"Now Sports 619","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch619_170_122"},"620":{"name":"Now Sports Premier League TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch620_170_122"},"621":{"name":"Now Sports 英超1台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch621_170_122"},"622":{"name":"Now Sports 英超2台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch622_170_122"},"623":{"name":"Now Sports 英超3台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch623_170_122"},"624":{"name":"Now Sports 英超4台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch624_170_122"},"625":{"name":"Now Sports 英超5台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch625_170_122"},"626":{"name":"Now Sports 英超6台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch626_170_122"},"627":{"name":"Now Sports 英超7台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch627_170_122"},"630":{"name":"Now Sports 精选","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch630_170_122"},"631":{"name":"Now Sports 1","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch631_170_122"},"632":{"name":"Now Sports 2","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch632_170_122"},"633":{"name":"Now Sports 3","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch633_170_122"},"634":{"name":"Now Sports 4","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch634_170_122"},"635":{"name":"Now Sports 5","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch635_170_122"},"636":{"name":"Now Sports 6","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch636_170_122"},"637":{"name":"Now Sports 7","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch637_170_122"},"638":{"name":"beIN SPORTS 1","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch638_170_122"},"639":{"name":"beIN SPORTS 2","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch639_170_122"},"640":{"name":"曼联电视频道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch640_170_122"},"641":{"name":"Now Sports 641","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch641_170_122"},"642":{"name":"NBA TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch642_170_122"},"643":{"name":"beIN SPORTS 3","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch643_170_122"},"644":{"name":"beIN SPORTS 4","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch644_170_122"},"645":{"name":"beIN SPORTS 5","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch645_170_122"},"646":{"name":"beIN SPORTS 6","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch646_170_122"},"647":{"name":"Now Sports 647","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch647_170_122"},"650":{"name":"Now Sports 650","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch650_170_122"},"651":{"name":"Now Sports 651","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch651_170_122"},"652":{"name":"Now Sports 652","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch652_170_122"},"668":{"name":"Now668","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch668_170_122"},"674":{"name":"Cricbuzz","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch674_170_122"},"679":{"name":"Premier Sports","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch679_170_122"},"680":{"name":"Now Sports Plus","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch680_170_122"},"683":{"name":"Now Golf 2","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch683_170_122"},"684":{"name":"Now Golf 3","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch684_170_122"},"711":{"name":"NHK World Premium","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch711_170_122"},"713":{"name":"TV5MONDE Style","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch713_170_122"},"714":{"name":"TV5MONDE ASIE","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch714_170_122"},"715":{"name":"France 24 (French)","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch715_170_122"},"720":{"name":"GMA Pinoy TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch720_170_122"},"721":{"name":"GMA Life TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch721_170_122"},"722":{"name":"GMA News TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch722_170_122"},"725":{"name":"TFC","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch725_170_122"},"771":{"name":"Sony TV (India)","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch771_170_122"},"772":{"name":"Sony MAX","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch772_170_122"},"774":{"name":"Sony SAB","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch774_170_122"},"779":{"name":"MTV India","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch779_170_122"},"780":{"name":"COLORS","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch780_170_122"},"781":{"name":"Zee Cinema International","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch781_170_122"},"782":{"name":"Zee TV","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch782_170_122"},"785":{"name":"Zee News","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch785_170_122"},"793":{"name":"Star Gold","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch793_170_122"},"794":{"name":"STAR PLUS","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch794_170_122"},"797":{"name":"Star Bharat","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch797_170_122"},"900":{"name":"成人節目資訊","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch900_170_122"},"901":{"name":"冰火頻道","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch901_170_122"},"903":{"name":"成人極品台","logo":"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch903_170_122"}}'

# hami/config.json：Hami Video 频道表。
EMBEDDED_HAMI_CONFIG_JSON = '{"channels":[{"id":"OTT_LIVE_0000002089","name":"Hami體育1台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@2089_20241226093429.png"},{"id":"OTT_LIVE_0000001745","name":"愛爾達體育3台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1745.png"},{"id":"OTT_LIVE_0000001743","name":"愛爾達體育2台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1743.png"},{"id":"OTT_LIVE_0000001879","name":"2026屏東三大日音樂節","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@1879_20260323085339.png"},{"id":"OTT_LIVE_0000001510","name":"BBC Earth","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@145@1510.png"},{"id":"OTT_LIVE_0000001970","name":"Lifetime","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1970_20211222120324.jpg"},{"id":"OTT_LIVE_0000001859","name":"History","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1859_20220621233610.jpg"},{"id":"OTT_LIVE_0000001884","name":"tvN","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@144@1884_20201021112150.png"},{"id":"OTT_LIVE_0000001546","name":"amc電影台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1546_20240103152142.jpg"},{"id":"OTT_LIVE_0000001836","name":"DreamWorks","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@144@1836.png"},{"id":"OTT_LIVE_0000001518","name":"愛爾達娛樂台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1518.png"},{"id":"OTT_LIVE_0000001816","name":"Hami體育1台(免費)","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@2089_20241226093429.png"},{"id":"OTT_LIVE_0000001845","name":"博斯運動一台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001845.png"},{"id":"OTT_LIVE_0000001856","name":"愛爾達體育MAX4台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001856.png"},{"id":"OTT_LIVE_0000001744","name":"愛爾達體育1台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001744.png"},{"id":"OTT_LIVE_0000001853","name":"愛爾達體育MAX1台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001853.png"},{"id":"OTT_LIVE_0000002055","name":"愛爾達體育4台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000002055.png"},{"id":"OTT_LIVE_0000001854","name":"愛爾達體育MAX2台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001854.png"},{"id":"OTT_LIVE_0000001855","name":"愛爾達體育MAX3台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001855.png"},{"id":"OTT_LIVE_0000001903","name":"博斯網球台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001903.png"},{"id":"OTT_LIVE_0000001972","name":"博斯魅力網","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=OTT_LIVE_0000001972.png"},{"id":"OTT_LIVE_0000002117","name":"OTT_LIVE_0000002117","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@1987_20251126062839.jpg"},{"id":"OTT_LIVE_0000002062","name":"OTT_LIVE_0000002062","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@2062_20251112070842.jpg"},{"id":"OTT_LIVE_0000002063","name":"OTT_LIVE_0000002063","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@2063_20251112070927.jpg"},{"id":"OTT_LIVE_0000001974","name":"OTT_LIVE_0000001974","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch0306.jpg"},{"id":"OTT_LIVE_0000001736","name":"OTT_LIVE_0000001736","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch0230.jpg"},{"id":"OTT_LIVE_0000001735","name":"OTT_LIVE_0000001735","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch0231.jpg"},{"id":"OTT_LIVE_0000001508","name":"OTT_LIVE_0000001508","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@7@1508.png"},{"id":"OTT_LIVE_0000001611","name":"OTT_LIVE_0000001611","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch8.jpg"},{"id":"OTT_LIVE_0000001868","name":"OTT_LIVE_0000001868","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch10.jpg"},{"id":"OTT_LIVE_0000001877","name":"OTT_LIVE_0000001877","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch0275.jpg"},{"id":"OTT_LIVE_0000002109","name":"OTT_LIVE_0000002109","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@screen@ch0305.jpg"},{"id":"OTT_LIVE_0000002039","name":"Asian Food Network亞洲美食頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@210@2039_20230727152217.jpg"},{"id":"OTT_LIVE_0000001513","name":"BBC Lifestyle","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@145@1513.png"},{"id":"OTT_LIVE_0000001527","name":"BBC NEWS","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@145@1527_20230410115755.png"},{"id":"OTT_LIVE_0000002015","name":"Bloomberg TV","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@272@2015_20230323160151.jpg"},{"id":"OTT_LIVE_0000001889","name":"CARTOONITO","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@144@1889_20230728074928.jpg"},{"id":"OTT_LIVE_0000001710","name":"CATCHPLAY電影台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@60@1710.png"},{"id":"OTT_LIVE_0000001711","name":"CBeebies","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@145@1711.png"},{"id":"OTT_LIVE_0000002097","name":"CinemaWorld頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@2097_20250327091250.jpg"},{"id":"OTT_LIVE_0000001752","name":"CNEX紀實頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@7@1752.png"},{"id":"OTT_LIVE_0000002003","name":"CNN國際新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@231@2003_20221221090721.png"},{"id":"OTT_LIVE_0000002004","name":"CNN頭條新聞台(HLN)","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@231@2004_20221221091031.png"},{"id":"OTT_LIVE_0000002002","name":"CN卡通頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@231@2002_20221221085824.png"},{"id":"OTT_LIVE_0000001924","name":"Discovery Asia","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@210@1924_20210427134516.jpg"},{"id":"OTT_LIVE_0000001514","name":"Discovery DMAX","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1514.png"},{"id":"OTT_LIVE_0000001511","name":"Discovery EVE","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1511.png"},{"id":"OTT_LIVE_0000001512","name":"Discovery科學台(Science)","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@210@1512_20210901102149.jpg"},{"id":"OTT_LIVE_0000001771","name":"EUROSPORT","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@210@1771_20220221140518.jpg"},{"id":"OTT_LIVE_0000001850","name":"Ginx TV電競頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1850.png"},{"id":"OTT_LIVE_0000001971","name":"Global Trekker","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1971_20211222120307.jpg"},{"id":"OTT_LIVE_0000001908","name":"Hami WBC1台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@1908_20260113024702.png"},{"id":"OTT_LIVE_0000001909","name":"Hami WBC2台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@1909_20260113024708.png"},{"id":"OTT_LIVE_0000002091","name":"Hami體育2台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@2090_20241224152040.png"},{"id":"OTT_LIVE_0000002090","name":"Hami體育3台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@1987_20251126062901.png"},{"id":"OTT_LIVE_0000001981","name":"HBLTV1台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1981_20241114155316.png"},{"id":"OTT_LIVE_0000001982","name":"HBLTV2台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1982_20241114155332.png"},{"id":"OTT_LIVE_0000001901","name":"HGTV居家樂活頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@144@1901_20210129153058.jpg"},{"id":"OTT_LIVE_0000002056","name":"INULTRA","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@2056_20230920161522.jpg"},{"id":"OTT_LIVE_0000001914","name":"LiveABC互動英語頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@51@1914_20210323084621.jpg"},{"id":"OTT_LIVE_0000001867","name":"LOVE NATURE","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1867_20250205140137.jpg"},{"id":"OTT_LIVE_0000001757","name":"Mezzo Live","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1757_20241204101929.png"},{"id":"OTT_LIVE_0000002094","name":"momo綜合台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@343@2094_20250327091237.jpg"},{"id":"OTT_LIVE_0000001865","name":"momo親子台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1865.png"},{"id":"OTT_LIVE_0000001939","name":"Nick Jr.","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1939_20240920115541.jpg"},{"id":"OTT_LIVE_0000001876","name":"PET CLUB TV","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1876_20200716144014.jpg"},{"id":"OTT_LIVE_0000002005","name":"ROCK Action","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@2005_20230117102027.jpg"},{"id":"OTT_LIVE_0000001860","name":"ROCK Entertainment","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1860_20210901084710.jpg"},{"id":"OTT_LIVE_0000001861","name":"ROCK X Stream","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1861_20260206035914.jpg"},{"id":"OTT_LIVE_0000001759","name":"TRACE Sport Stars","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1759.png"},{"id":"OTT_LIVE_0000001758","name":"Trace Urban","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1758.png"},{"id":"OTT_LIVE_0000001756","name":"Travel Channel","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@210@1756_20210901102231.jpg"},{"id":"OTT_LIVE_0000001561","name":"TVBS","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1561_20230303074155.png"},{"id":"OTT_LIVE_0000001560","name":"TVBS新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1560_20230303074207.png"},{"id":"OTT_LIVE_0000001559","name":"TVBS歡樂台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1559_20230303074225.png"},{"id":"OTT_LIVE_0000001789","name":"TVBS精采台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1789_20201230161437.jpg"},{"id":"OTT_LIVE_0000001888","name":"Warner TV","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@144@1888_20201110165836.png"},{"id":"OTT_LIVE_0000001690","name":"三立iNEWS","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@32@1690.png"},{"id":"OTT_LIVE_0000001516","name":"三立新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@32@1516.png"},{"id":"OTT_LIVE_0000001562","name":"中天新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@30@1562.png"},{"id":"OTT_LIVE_0000001568","name":"中視主頻","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@30@1568.png"},{"id":"OTT_LIVE_0000001567","name":"中視新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@30@1567.png"},{"id":"OTT_LIVE_0000001566","name":"中視經典台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@1566.png"},{"id":"OTT_LIVE_0000001541","name":"中視菁采台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@30@1541_20230223080032.jpg"},{"id":"OTT_LIVE_0000002070","name":"亞洲旅遊台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1969_20211223113449.png"},{"id":"OTT_LIVE_0000001793","name":"人間衛視","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1793.png"},{"id":"OTT_LIVE_0000001528","name":"八大精彩台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1528_20230223082342.png"},{"id":"OTT_LIVE_0000001549","name":"八大綜藝台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1549.png"},{"id":"OTT_LIVE_0000001555","name":"公視戲劇","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1555.jpg"},{"id":"OTT_LIVE_0000001973","name":"博斯無限二台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1973_20211223113550.png"},{"id":"OTT_LIVE_0000001882","name":"博斯無限台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1882_20200918092245.png"},{"id":"OTT_LIVE_0000002110","name":"博斯運動二台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@2110_20251029042743.png"},{"id":"OTT_LIVE_0000001846","name":"博斯高球一台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1846.png"},{"id":"OTT_LIVE_0000001539","name":"古典音樂台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1539.jpg"},{"id":"OTT_LIVE_0000001519","name":"台視主頻","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@140@1519.png"},{"id":"OTT_LIVE_0000001565","name":"台視新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@140@1565.png"},{"id":"OTT_LIVE_0000001790","name":"台視綜合台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@140@1790.png"},{"id":"OTT_LIVE_0000001791","name":"台視財經台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@140@1791.png"},{"id":"OTT_LIVE_0000001538","name":"大愛電視","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1538.png"},{"id":"OTT_LIVE_0000002069","name":"寰宇新聞","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1841.png"},{"id":"OTT_LIVE_0000001851","name":"尼克兒童頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@1851_20240920115519.jpg"},{"id":"OTT_LIVE_0000002113","name":"影迷數位紀實台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@2113_20251124091211.jpg"},{"id":"OTT_LIVE_0000001689","name":"影迷數位電影台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1689.jpg"},{"id":"OTT_LIVE_0000001517","name":"愛爾達影劇台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1517.png"},{"id":"OTT_LIVE_0000002079","name":"愛爾達日韓台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@2079_20240314140751.jpg"},{"id":"OTT_LIVE_0000001573","name":"愛爾達綜合台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@61@1573.png"},{"id":"OTT_LIVE_0000001564","name":"東森新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1564_20230303074926.png"},{"id":"OTT_LIVE_0000001563","name":"東森財經台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1563_20230303074948.png"},{"id":"OTT_LIVE_0000001579","name":"民視台灣台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1579.png"},{"id":"OTT_LIVE_0000001551","name":"民視影劇台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1551.jpg"},{"id":"OTT_LIVE_0000001533","name":"民視新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1533_20230303074753.png"},{"id":"OTT_LIVE_0000001534","name":"民視無線台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1534_20230303091510.png"},{"id":"OTT_LIVE_0000001535","name":"民視第一台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1535.png"},{"id":"OTT_LIVE_0000001550","name":"民視綜藝台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1550_20230303074828.png"},{"id":"OTT_LIVE_0000001553","name":"空中英語教室","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1553.png"},{"id":"OTT_LIVE_0000002019","name":"罪案偵緝頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@156@2019_20230524101157.jpg"},{"id":"OTT_LIVE_0000001825","name":"華藝中文台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1825_20241126082218.jpg"},{"id":"OTT_LIVE_0000001526","name":"華視主頻","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@1526.png"},{"id":"OTT_LIVE_0000001569","name":"華視新聞資訊台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@27@1569.png"},{"id":"OTT_LIVE_0000001548","name":"豬哥亮歌廳秀","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1548_20230303074905.png"},{"id":"OTT_LIVE_0000001547","name":"達文西頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1547.jpg"},{"id":"OTT_LIVE_0000001635","name":"采昌影劇台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@33@1635.jpg"},{"id":"OTT_LIVE_0000001915","name":"金光布袋戲","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1915_20210323145706.jpg"},{"id":"OTT_LIVE_0000002006","name":"鏡電視新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@239@2006_20230119100452.png"},{"id":"OTT_LIVE_0000001938","name":"電影原聲台CMusic","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@88@1938_20210624083457.jpg"},{"id":"OTT_LIVE_0000002114","name":"靖天中文台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@21@2113_20251124091157.jpg"},{"id":"OTT_LIVE_0000001652","name":"靖天日本台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@80@1652.png"},{"id":"OTT_LIVE_0000002095","name":"靖天育樂台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@80@2095_20250327091224.jpg"},{"id":"OTT_LIVE_0000002096","name":"靖洋戲劇台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@80@2096_20250327091213.jpg"},{"id":"OTT_LIVE_0000001649","name":"非凡商業台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@32@1649.png"},{"id":"OTT_LIVE_0000001650","name":"非凡新聞台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@32@1650.png"},{"id":"OTT_LIVE_0000002115","name":"麥卡貝綜藝","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@32@2115_20251124091219.jpg"},{"id":"OTT_LIVE_0000002093","name":"麥哲倫頻道","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@342@2093_20250327091203.jpg"},{"id":"OTT_LIVE_0000001904","name":"龍華偶像","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1904_20210127105232.png"},{"id":"OTT_LIVE_0000001542","name":"龍華影劇","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1542_20230223081229.png"},{"id":"OTT_LIVE_0000001839","name":"龍華戲劇","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1839.png"},{"id":"OTT_LIVE_0000002098","name":"龍華洋片台","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@2098_20250327091147.png"},{"id":"OTT_LIVE_0000001840","name":"龍華電影","icon":"https://apl-hamivideo.cdn.hinet.net/HamiVideo/getHamiVideoImg.php?imageId=sourceImages@Live@149@1840.png"}]}'

# bein/mapping.json：beIN slider -> 频道名映射（70 条）。
EMBEDDED_BEIN_MAPPING_JSON = '{"sports_slider_1":{"name":"beIN SPORTS 1","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS1_DIGITAL_Mono.png","group":"sports"},"sports_slider_2":{"name":"beIN SPORTS 2","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS2_DIGITAL_Mono.png","group":"sports"},"sports_slider_3":{"name":"beIN SPORTS 3","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS3_DIGITAL_Mono.png","group":"sports"},"sports_slider_4":{"name":"beIN SPORTS 4","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS4_DIGITAL_Mono.png","group":"sports"},"sports_slider_5":{"name":"beIN SPORTS 5","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS5_DIGITAL_Mono.png","group":"sports"},"sports_slider_6":{"name":"beIN SPORTS 6","logo":"https://assets.bein.com/mena/sites/3/2021/02/beIN_SPORTS66_DIGITAL_Mono-01.png","group":"sports"},"sports_slider_7":{"name":"beIN SPORTS 7","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS7_DIGITAL_Mono.png","group":"sports"},"sports_slider_8":{"name":"beIN SPORTS 8","logo":"https://assets.bein.com/mena/sites/3/2023/09/logos-_beINSPORTS8.png","group":"sports"},"sports_slider_9":{"name":"beIN SPORTS 9","logo":"https://assets.bein.com/mena/sites/3/2023/09/logos-_beINSPORTS9.png","group":"sports"},"sports_slider_10":{"name":"beIN SPORT SNEWS","logo":"https://assets.bein.com/mena/sites/3/2015/06/NEWS_DIGITAL_Mono.png","group":"sports"},"sports_slider_11":{"name":"bein SPORTS FTA","logo":"https://assets.bein.com/mena/sites/3/2015/06/bein_SPORTS_FTA_DIGITAL_Mono.png","group":"sports"},"sports_slider_12":{"name":"beIN SPORTS 1 ENGLISH","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS1_ENGLISH_Digital_Mono.png","group":"sports"},"sports_slider_13":{"name":"beIN SPORTS 2 ENGLISH","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS2_ENGLISH_Digital_Mono.png","group":"sports"},"sports_slider_14":{"name":"beIN SPORTS 1 FRENCH","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS1_FRENCH_Digital_Mono.png","group":"sports"},"sports_slider_15":{"name":"beIN SPORTS 2 FRENCH","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS2_FRENCH_Digital_Mono.png","group":"sports"},"sports_slider_16":{"name":"beIN SPORTS 4K","logo":"https://assets.bein.com/mena/sites/3/2015/06/4k_DIGITAL_Mono.png","group":"sports"},"sports_slider_17":{"name":"beIN SPORTS XTRA 1","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS_XTRA1_Digital_Mono.png","group":"sports"},"sports_slider_18":{"name":"beIN SPORTS XTRA 2","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS_XTRA2_Digital_Mono.png","group":"sports"},"sports_slider_19":{"name":"beIN SPORTS XTRA 3","logo":"https://assets.bein.com/mena/sites/3/2023/09/logos-_beINSPORTSXTRA3.png","group":"sports"},"sports_slider_20":{"name":"beIN SPORTS XTRA 4","logo":"https://assets.bein.com/mena/sites/3/2025/01/beIN_SPORTS_XTRA_04_bein.com_.png","group":"sports"},"sports_slider_21":{"name":"beIN SPORTS XTRA 5","logo":"https://assets.bein.com/mena/sites/3/2025/01/beIN_SPORTS_XTRA_05_bein.com_.png","group":"sports"},"sports_slider_22":{"name":"beIN SPORTS XTRA 6","logo":"https://assets.bein.com/mena/sites/3/2025/01/beIN_SPORTS_XTRA_06_bein.com_.png","group":"sports"},"sports_slider_23":{"name":"beIN SPORTS XTRA 7","logo":"https://assets.bein.com/mena/sites/3/2025/01/beIN_SPORTS_XTRA_07_bein.com_.png","group":"sports"},"sports_slider_24":{"name":"beIN SPORTS XTRA 8","logo":"https://assets.bein.com/mena/sites/3/2025/01/beIN_SPORTS_XTRA_08_Bein.com_.png","group":"sports"},"sports_slider_25":{"name":"beIN SPORTS XTRA 9","logo":"https://assets.bein.com/mena/sites/3/2025/01/beIN_SPORTS_XTRA_Digital_09_Bein.com_.png","group":"sports"},"sports_slider_26":{"name":"beIN SPORTS MAX 1","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS_MAX1_DIGITAL_Mono.png","group":"sports"},"sports_slider_27":{"name":"beIN SPORTS MAX 2","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS_MAX2_DIGITAL_Mono.png","group":"sports"},"sports_slider_28":{"name":"beIN SPORTS MAX 3","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS_MAX3_DIGITAL_Mono.png","group":"sports"},"sports_slider_29":{"name":"beIN SPORTS MAX 4","logo":"https://assets.bein.com/mena/sites/3/2015/06/beIN_SPORTS_MAX4_DIGITAL_Mono.png","group":"sports"},"sports_slider_30":{"name":"Alkass 1","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_1.png","group":"sports"},"sports_slider_31":{"name":"Alkass 2","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_2.png","group":"sports"},"sports_slider_32":{"name":"Alkass 3","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_3.png","group":"sports"},"sports_slider_33":{"name":"Alkass 4","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_4.png","group":"sports"},"sports_slider_34":{"name":"Alkass 5","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_5.png","group":"sports"},"sports_slider_35":{"name":"Alkass 6","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_6.png","group":"sports"},"sports_slider_36":{"name":"Alkass 7","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_7.png","group":"sports"},"sports_slider_37":{"name":"Alkass 8","logo":"https://assets.bein.com/mena/sites/3/2020/06/2023_Alkass_8.png","group":"sports"},"entertainment_slider_1":{"name":"beIN MOVIES 1 PREMIERE","logo":"https://assets.bein.com/mena/sites/3/2015/06/MOVIES1_PREMIERE_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_2":{"name":"beIN MOVIES 2 ACTION","logo":"https://assets.bein.com/mena/sites/3/2015/06/MOVIES2_ACTION_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_3":{"name":"beIN MOVIES 3 DRAMA","logo":"https://assets.bein.com/mena/sites/3/2015/06/MOVIES3_DRAMA_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_4":{"name":"beIN MOVIES 4 FAMILY","logo":"https://assets.bein.com/mena/sites/3/2015/06/MOVIES4_FAMILY_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_5":{"name":"Box Office 1","logo":"https://assets.bein.com/mena/sites/3/2015/06/BoxOffice1_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_6":{"name":"Box Office 2","logo":"https://assets.bein.com/mena/sites/3/2015/06/BoxOffice2_DIGITAL_Mono.png.png","group":"entertainment"},"entertainment_slider_7":{"name":"Star Action","logo":"https://assets.bein.com/mena/sites/3/2015/06/Star_Action_2024-200x200-1.png","group":"entertainment"},"entertainment_slider_8":{"name":"Star Movies","logo":"https://assets.bein.com/mena/sites/3/2015/06/Star_Movies_2024-200x200-1.png","group":"entertainment"},"entertainment_slider_9":{"name":"beIN SERIES 1","logo":"https://assets.bein.com/mena/sites/3/2015/06/SERIES1_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_10":{"name":"beIN SERIES 2","logo":"https://assets.bein.com/mena/sites/3/2015/06/SERIES2_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_11":{"name":"beIN Drama Stacked","logo":"https://assets.bein.com/mena/sites/3/2021/02/Drama_Stacked_nov_2024_200x200.png","group":"entertainment"},"entertainment_slider_12":{"name":"GOURMET","logo":"https://assets.bein.com/mena/sites/3/2015/06/GOURMET_DIGITAL_Mono.png","group":"entertainment"},"entertainment_slider_13":{"name":"travel channel","logo":"https://assets.bein.com/mena/sites/3/2015/06/0003_TCI_colour-Blue_UK2016.png","group":"entertainment"},"entertainment_slider_14":{"name":"Food Network","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/food-network-logo-2018-1.png","group":"entertainment"},"entertainment_slider_15":{"name":"HGTV","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/HGTV.png","group":"entertainment"},"entertainment_slider_16":{"name":"Star World","logo":"https://assets.bein.com/mena/sites/3/2015/06/Star_World_2024-200x200-1.png","group":"entertainment"},"entertainment_slider_17":{"name":"FATAFEAT","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/FATAFEAT-1.png","group":"entertainment"},"entertainment_slider_18":{"name":"Bloomberg","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/Bloomberg-1.png","group":"entertainment"},"entertainment_slider_19":{"name":"Nat geo","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/Nat_geo_hd.png","group":"entertainment"},"entertainment_slider_20":{"name":"PDATEz NGW","logo":"https://assets.bein.com/mena/sites/3/2020/06/UPDATEz-NGW_Full_Logo_20182.png","group":"entertainment"},"entertainment_slider_21":{"name":"BBCEarth","logo":"https://assets.bein.com/mena/sites/3/2020/06/BBCEarth-new_200x200.png","group":"entertainment"},"entertainment_slider_22":{"name":"CNN International","logo":"https://assets.bein.com/mena/sites/3/2015/06/0004_CNN_International_logo.svg.png","group":"entertainment"},"entertainment_slider_23":{"name":"Euro News","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/Euro-News.png","group":"entertainment"},"entertainment_slider_24":{"name":"Discovery","logo":"https://assets.bein.com/mena/sites/3/2021/03/Discovery-logo-1.png","group":"entertainment"},"entertainment_slider_25":{"name":"be JUNIOR","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/beJUNIOR-1.png","group":"entertainment"},"entertainment_slider_26":{"name":"entertainment_slider_26","logo":"https://assets.bein.com/mena/sites/3/2020/06/Jeem-HD-1-01.png","group":"entertainment"},"entertainment_slider_27":{"name":"Bara3em","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/Bara3em-1.png","group":"entertainment"},"entertainment_slider_28":{"name":"AWG CN Brand","logo":"https://assets.bein.com/mena/sites/3/2015/06/0001_AWG_CN_Brand_Logo_1color_Black_4C.png","group":"entertainment"},"entertainment_slider_29":{"name":"Cartoon Network Arabic","logo":"https://www.bein.com/en/wp-content/uploads/sites/3/2020/06/Cartoon-Network-Arabic-1.png","group":"entertainment"},"entertainment_slider_30":{"name":"Disney Blue","logo":"https://assets.bein.com/mena/sites/3/2025/02/DC-Blue-new.png","group":"entertainment"},"entertainment_slider_31":{"name":"Disney jr.","logo":"https://assets.bein.com/mena/sites/3/2025/02/DJ.png","group":"entertainment"},"entertainment_slider_32":{"name":"Baby TV","logo":"https://assets.bein.com/mena/sites/3/2021/11/Baby-TV-2.png","group":"entertainment"},"entertainment_slider_33":{"name":"cartoonito","logo":"https://assets.bein.com/mena/sites/3/2020/06/cartoonito.png","group":"entertainment"}}'

# beinsports/config.json：beIN SPORTS 频道表（61 个频道）。
EMBEDDED_BEINSPORTS_CONFIG_JSON = '{"generated_at":"2026-04-08T20:00:00.000Z","total_channels":61,"channels":[{"id":"5ED9E1C4-BEF4-4AC2-8F90-28B3DCA26375","name":"BEINC","icon":"https://prod-media.beinsports.com/image/498f84ba449942229231dcfbbee14ea1.png"},{"id":"520D107A-71D4-4CB7-B141-1C6B089A7FFC","name":"BEINS8C","icon":"https://prod-media.beinsports.com/image/498f84ba449942229231dcfbbee14ea1.png"},{"id":"5824C394-7211-4004-AC46-35BD58B9D1EE","name":"BEINSSC","icon":"https://prod-media.beinsports.com/image/498f84ba449942229231dcfbbee14ea1.png"},{"id":"C244C48D-3B54-406A-94C9-D63B16318267","name":"beIN SPORTS","icon":"https://www.beinsports.com/_next/static/media/bein_logo.af017869.webp"},{"id":"A31EDF08-2A80-4D1F-9016-43A9C6138255","name":"beIN SPORTS","icon":"https://www.beinsports.com/_next/static/media/bein_logo.af017869.webp"},{"id":"B76EB90E-0657-4D61-9265-68ADA92E34CD","name":"beIN SPORTS","icon":"https://www.beinsports.com/_next/static/media/bein_logo.af017869.webp"},{"id":"66265DD1-D2AA-4A89-9412-2455186E90ED","name":"beIN SPORTS 1","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1290.png"},{"id":"164C0EDA-EBCE-4AA6-9DDA-D603E0948B9F","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"0FA3DEF0-234B-4E25-934F-392DF2C3162C","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"E227320A-8BA4-4ECB-A521-6DA53ADED441","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"A4320E60-381D-4435-8294-B4F3F33BF01C","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"02821165-CA6E-4A60-B781-C9C67B587185","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"6B04AAE6-C819-486A-BA59-827E03EA0DB5","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"FD38AB3D-2BF5-4855-B031-B83DE8335EF9","name":"beIN SPORTS 1 Premium","icon":null},{"id":"1E886E98-3104-4D8E-A97F-734B23108E8A","name":"beIN SPORTS 2","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1304.png"},{"id":"63A30D9C-609D-4A30-8730-2CA391DF287A","name":"beIN SPORTS 2","icon":"https://mediacms01.apac.beiniz.biz/channel_logo/1752576028271-bein_sports_2.png"},{"id":"DDFC8C16-6363-4A2C-AE66-2CE357DBC28E","name":"beIN SPORTS 2","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1304.png"},{"id":"5F323488-6662-42DC-BE1C-B954ECDD5CD7","name":"beIN SPORTS 2","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1304.png"},{"id":"ACE14EF6-9A0C-480D-A15B-80EF27D55E62","name":"beIN SPORTS 2","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1304.png"},{"id":"6DB008B8-66DA-423E-805D-616211D4B174","name":"beIN SPORTS 3","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1335.png"},{"id":"90E69FC7-AA8C-40F2-B35F-EBD174495F76","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"2E8A6222-8D90-4291-81E3-AF2F72557746","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"07C1C284-D784-4AD2-9F7E-022DCD196D04","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"F3CE2580-D815-4D33-84FC-6B95742BFB00","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"E41B16D3-5172-4D56-B2C7-E2BB41E38CAD","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"7C714598-E7ED-4B0F-8BD1-E8E80D473922","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"8352518C-86A6-4EED-8F47-EE619E8F0FDF","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"D0546ED7-9DB2-4924-9E32-C4F077E7BFC7","name":"beIN SPORTS 4","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-4.webp"},{"id":"A5A48DB1-C00B-4DB9-9FC6-5E5F25C18830","name":"beIN SPORTS 5","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-5.webp"},{"id":"E56B0905-F99F-4DB1-931E-E6002B530867","name":"beIN SPORTS 6","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-6.webp"},{"id":"831591C8-DA65-4528-B837-0E5A147887FB","name":"beIN SPORTS 7","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-7.webp"},{"id":"9A424246-EC89-43C3-9239-AA3A40540F94","name":"beIN SPORTS 8","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-8.webp"},{"id":"4083661E-F588-4700-943C-8FDF65F1B5EB","name":"beIN SPORTS Español","icon":"https://prod-media.beinsports.com/image/83ff5fb7036545401a7d3aa37880b8aa.png"},{"id":"6F425028-14F0-4E38-B776-71A9DB3F3493","name":"beIN SPORTS MAX 10","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1342.png"},{"id":"EF7E0369-CFD9-4607-9238-EC483E6D58A3","name":"beIN SPORTS MAX 4","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1336.png"},{"id":"A4425E44-B004-4137-ACB4-458BA4D710F8","name":"beIN SPORTS MAX 5","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1337.png"},{"id":"BBD26D71-897D-4799-87A1-A87120130D48","name":"beIN SPORTS MAX 6","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1338.png"},{"id":"9B4708C5-F5F0-452A-8157-4661C30FAE6A","name":"beIN SPORTS MAX 7","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1339.png"},{"id":"76D2430C-8BCB-41BD-A1DE-FBA0D58C5E8A","name":"beIN SPORTS MAX 8","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1340.png"},{"id":"FB208953-1C70-4D1E-B918-103AB02AB181","name":"beIN SPORTS MAX 9","icon":"https://photos.media-press.tv/logo/plurimedia/png_500x500_clair/1341.png"},{"id":"1ACADCF1-DFAC-480B-872C-53D51FE1B45D","name":"beIN SPORTS XTRA","icon":"https://prod-media.beinsports.com/image/f51ff5e82c61a9c21b95a001fdc6c241.png"},{"id":"C938A2D0-375A-4876-B05D-08EE29D33B07","name":"beIN SPORTS XTRA","icon":"https://prod-media.beinsports.com/image/f51ff5e82c61a9c21b95a001fdc6c241.png"},{"id":"5223E3D7-FB4E-4155-885A-9165996B22FA","name":"beIN SPORTS XTRA ñ","icon":"https://prod-media.beinsports.com/image/83ff5fb7036545401a7d3aa37880b8aa.png"},{"id":"67DD49E9-E3A2-4B3A-94B8-88C620A4DFB1","name":"beIN 4K","icon":"https://prod-media.beinsports.com/image/8f53295a73878494e9bc8dd6c3c7104f.png"},{"id":"008F0EA9-FCD9-4E8C-849A-913979E7450A","name":"beIN SPORTS MAX 2","icon":"https://prod-media.beinsports.com/image/a38b16173474ba8b1a95bcbc30d3b8a5.png"},{"id":"F7215920-CCF9-4DBB-8B9D-152E232FA549","name":"beIN SPORTS MAX 5","icon":"https://prod-media.beinsports.com/image/ca886eb9edb61a42256192745c72cd79.png"},{"id":"9B969275-E59C-4DD1-8FCA-8B01EAE04909","name":"beIN SPORTS FR 2","icon":"https://prod-media.beinsports.com/image/501e3f8a108d7ab9335ceecd363d113d.png"},{"id":"7783DC02-4527-4094-9EE3-CDA8E093E4EB","name":"beIN SPORTS MAX 3","icon":"https://prod-media.beinsports.com/image/dc1913d422398c25c5f0b81cab94cc87.png"},{"id":"84F7E0C1-D47A-4444-BBA8-C50BB8D2C5A8","name":"beIN SPORTS","icon":"https://www.beinsports.com/_next/static/media/bein_logo.af017869.webp"},{"id":"2FB43094-3598-43C1-A3BA-44BFB40092E0","name":"beIN SPORTS MAX 1","icon":"https://prod-media.beinsports.com/image/1113d7a76ffceca1bb350bfe145467c6.png"},{"id":"E24D9C11-A8B4-4C7F-AD3E-B3364FB6D5A2","name":"beIN SPORTS 6","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-6.webp"},{"id":"5B15611A-5F9D-4EF0-89A6-677C9CA2BD5D","name":"beIN SPORTS MAX 4","icon":"https://prod-media.beinsports.com/image/3496a425359f4287e9db34c2a01c0a8c.png"},{"id":"8AEA2426-D451-4BA5-BF48-114A1F04B1A8","name":"beIN SPORTS 3","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-3.webp"},{"id":"DC04009D-7E18-4D1C-BA7F-7269B8F8D065","name":"beIN SPORTS MAX 6","icon":"https://prod-media.beinsports.com/image/32f2a17a525f222650821cbc362a74ec.png"},{"id":"A892063B-A5D9-4199-95AC-6A214515FA6B","name":"beIN SPORTS 7","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-7.webp"},{"id":"846C79D6-18F8-4A4D-ACFA-2C18DCCB6398","name":"beIN SPORTS EN 2","icon":"https://prod-media.beinsports.com/image/4cd31d31e16ac873f5f421d5880e88d6.png"},{"id":"DB9361E8-B3EB-4D6F-9A82-75B5F09E2F92","name":"beIN SPORTS 4","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-4.webp"},{"id":"0F8D20A4-D46C-4B18-9242-8E7B3E978FF8","name":"beIN SPORTS 8","icon":"https://cdn.mango-soft.com/bein-sports/channel-logo-8.webp"},{"id":"7836FEA9-6B39-4A1A-8352-DC5FCB97A16C","name":"beIN SPORTS 1","icon":"https://prod-media.beinsports.com/image/85029984d97fda2dd293f3c7d6cf0c0d.png"},{"id":"5C08D9D3-C713-4F1F-947E-87C761428B9B","name":"beIN SPORTS 9","icon":"https://assets.bein.com/mena/sites/3/2023/09/logos-_beINSPORTS9.png"},{"id":"FD1DD7DD-1E7B-4AA2-8682-BFA17338E653","name":"beIN SPORTS 2","icon":"https://mediacms01.apac.beiniz.biz/channel_logo/1752576028271-bein_sports_2.png"},{"id":"93000494-0DF8-4107-AF0E-1C99D3DBB2EC","name":"beIN SPORTS FR 1","icon":"https://prod-media.beinsports.com/image/e2c2ff9c7fb57db574e461fbe467d84e.png"},{"id":"8C1EC4FC-35E6-4866-A75D-37FCFAE18839","name":"beIN SPORTS EN 1","icon":"https://prod-media.beinsports.com/image/6a971e08a01e6676d0f1a6e0dacbbd67.png"},{"id":"9ABD32F9-C6D9-4DD5-B936-2C7E6546292E","name":"beIN SPORTS XTRA 6","icon":"https://prod-media.beinsports.com/image/2c991594f9e4d315239a58e57cde71bb.256.png"},{"id":"7A8040D9-7BAF-477E-B9F7-8BAB88F677E8","name":"beIN SPORTS XTRA 4","icon":"https://prod-media.beinsports.com/image/17a2f75f991787fd923a77259dda115e.256.png"},{"id":"27E67022-B943-4913-9AF3-AFD3DAC9854B","name":"beIN SPORTS XTRA 2","icon":"https://prod-media.beinsports.com/image/051928341be67dcba03f0e04104d9047.png"},{"id":"7B558284-F996-4123-9584-1E5D01844270","name":"beIN SPORTS NEWS","icon":"https://prod-media.beinsports.com/image/61a10e6abb1149ad9d08f303267f9bc4.png"},{"id":"CDF1A4C8-26DD-4C33-A239-F729A3B09295","name":"beIN SPORTS XTRA 3","icon":"https://prod-media.beinsports.com/image/75c6b2d6319e12f37ff421834ad22fa8.png"},{"id":"E3B37FA0-E582-45B2-BB8E-516E1A714EF6","name":"beIN SPORTS XTRA 1","icon":"https://prod-media.beinsports.com/image/a501bebf79d570651ff601788ea9d16d.png"},{"id":"522050CE-EBD3-43EA-B636-42B034FDC05C","name":"beIN SPORTS XTRA 9","icon":"https://prod-media.beinsports.com/image/9519f7e2743c36f2ff3f03f1c0264bd5.256.png"},{"id":"51D28C47-7B79-4007-81A3-BFDF9BC65A3B","name":"beIN SPORTS XTRA 5","icon":"https://prod-media.beinsports.com/image/de6c3cf1b5d00bf10aaf95cb5ba69d75.256.png"},{"id":"964E6246-CA95-410B-82C4-EA75DD979435","name":"beIN SPORTS 5","icon":"https://prod-media.beinsports.com/image/0d2131d903a4485c80374b7f91549323.png"},{"id":"CD634732-20D1-4137-94E7-939DE93D056D","name":"beIN SPORTS XTRA 8","icon":"https://prod-media.beinsports.com/image/f8c077b1de82c818e0809f74c9409b85.256.png"},{"id":"1752F091-A114-4629-BED4-46E0BB488A24","name":"beIN SPORTS XTRA 7","icon":"https://prod-media.beinsports.com/image/0e7d4005bb386e7b30b56b171ef0f3f3.256.png"}]}'


# =====================================================================
# 平台 1：myTV SUPER（香港）
# 来源：EPGCollector/mytvsuper/mytvsuper.py（245 行）
# 逻辑原样保留：content-api 接口、双语台名、stop=下一档开始（末档+30分钟）、
# 不做 html.escape、RETRY_COUNT 真重试、ThreadPoolExecutor 8 线程。
# 改动：不写文件，返回内存里的 XML 根节点。
# =====================================================================
MYTVSUPER_CHANNEL_API_URL = "https://content-api.mytvsuper.com/v1/channel/list"
MYTVSUPER_EPG_API_URL = "https://content-api.mytvsuper.com/v1/epg"
MYTVSUPER_PLATFORM = "web"
MYTVSUPER_COUNTRY_CODE = "ZP"
MYTVSUPER_PROFILE_CLASS = "general"
MYTVSUPER_MAX_WORKERS = 8
MYTVSUPER_TIMEOUT = 30
MYTVSUPER_RETRY_COUNT = 2
MYTVSUPER_REQUEST_DELAY = 0.3
MYTVSUPER_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.mytvsuper.com/",
}


def mytvsuper_clean(text):
    """只去空白, 不做 html.escape, 转义交给 ElementTree"""
    if not text:
        return ""
    return str(text).strip()


def mytvsuper_parse_dt(dt_str):
    if not dt_str:
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"]:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def mytvsuper_to_xml_time(dt):
    if not dt:
        return ""
    return dt.strftime("%Y%m%d%H%M%S") + " +0800"


def mytvsuper_clean_name(name):
    if not name:
        return ""
    return re.sub(r"\s*\(Free\)|\s*\(免費\)|\s*Free|\s*免費", "", name, flags=re.I).strip()


def mytvsuper_get_channels():
    params = {"platform": MYTVSUPER_PLATFORM, "country_code": MYTVSUPER_COUNTRY_CODE,
              "profile_class": MYTVSUPER_PROFILE_CLASS}
    r = requests.get(MYTVSUPER_CHANNEL_API_URL, params=params,
                     headers=MYTVSUPER_HEADERS, timeout=MYTVSUPER_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    result = {}
    for c in data.get("channels", []):
        code = c.get("network_code")
        if not code:
            continue
        name_tc = mytvsuper_clean_name(c.get("name_tc", ""))
        name_en = mytvsuper_clean_name(c.get("name_en", ""))
        result[code] = {
            "name_tc": name_tc or code,
            "name_en": name_en or name_tc or code,
            "icon": c.get("landscape_poster") or "",
        }
    log(f"[mytvsuper] 频道数量: {len(result)}")
    return result


def mytvsuper_fetch_epg(code, s, e):
    params = {"platform": MYTVSUPER_PLATFORM, "country_code": MYTVSUPER_COUNTRY_CODE,
              "network_code": code, "from": s, "to": e}
    for attempt in range(MYTVSUPER_RETRY_COUNT + 1):
        try:
            time.sleep(MYTVSUPER_REQUEST_DELAY)
            r = requests.get(MYTVSUPER_EPG_API_URL, params=params,
                             headers=MYTVSUPER_HEADERS, timeout=MYTVSUPER_TIMEOUT)
            r.raise_for_status()
            return code, r.json()
        except Exception as ex:
            if attempt == MYTVSUPER_RETRY_COUNT:
                log(f"[mytvsuper] [WARN] {code} 失败: {ex}")
                return code, None
            time.sleep(1)


def mytvsuper_fetch_all(channels, s, e):
    epg = {}
    with ThreadPoolExecutor(max_workers=MYTVSUPER_MAX_WORKERS) as ex:
        futures = [ex.submit(mytvsuper_fetch_epg, c, s, e) for c in channels]
        for i, f in enumerate(as_completed(futures), 1):
            code, data = f.result()
            epg[code] = data
            if i % 20 == 0:
                log(f"[mytvsuper] [INFO] {i}/{len(channels)}")
    return epg


def mytvsuper_extract(data):
    if isinstance(data, list):
        out = []
        for i in data:
            out.extend(mytvsuper_extract(i))
        return out
    if isinstance(data, dict):
        if "start_datetime" in data:
            return [data]
        for k in ["epg", "programmes", "items", "list"]:
            if k in data and isinstance(data[k], list):
                out = []
                for i in data[k]:
                    out.extend(mytvsuper_extract(i))
                return out
        out = []
        for v in data.values():
            out.extend(mytvsuper_extract(v))
        return out
    return []


def mytvsuper_build_xml(channels, epg_data):
    tv = ET.Element("tv")
    tv.set("generator-info-name", "myTV SUPER EPG")
    for code, c in channels.items():
        ch = ET.SubElement(tv, "channel", id=code)
        ET.SubElement(ch, "display-name", lang="zh").text = mytvsuper_clean(c["name_tc"])
        ET.SubElement(ch, "display-name", lang="en").text = mytvsuper_clean(c["name_en"])
        if c["icon"]:
            ET.SubElement(ch, "icon", src=c["icon"])
    total = 0
    for code, data in epg_data.items():
        if not data:
            continue
        items = [p for p in mytvsuper_extract(data) if mytvsuper_parse_dt(p.get("start_datetime"))]
        items.sort(key=lambda p: mytvsuper_parse_dt(p["start_datetime"]))
        for idx, p in enumerate(items):
            start_dt = mytvsuper_parse_dt(p.get("start_datetime"))
            if idx + 1 < len(items):
                stop_dt = mytvsuper_parse_dt(items[idx + 1].get("start_datetime"))
            else:
                stop_dt = start_dt + timedelta(minutes=30)
            if not stop_dt or stop_dt <= start_dt:
                stop_dt = start_dt + timedelta(minutes=30)
            tc = mytvsuper_clean(p.get("programme_title_tc", ""))
            en = mytvsuper_clean(p.get("programme_title_en", ""))
            if not tc and not en:
                continue
            prog = ET.SubElement(tv, "programme", {
                "channel": code,
                "start": mytvsuper_to_xml_time(start_dt),
                "stop": mytvsuper_to_xml_time(stop_dt),
            })
            if tc:
                ET.SubElement(prog, "title", lang="zh").text = tc[:200]
            if en:
                ET.SubElement(prog, "title", lang="en").text = en[:200]
            desc = mytvsuper_clean(p.get("episode_synopsis_tc", ""))
            if desc:
                ET.SubElement(prog, "desc", lang="zh").text = desc[:300]
            desc_en = mytvsuper_clean(p.get("episode_synopsis_en", ""))
            if desc_en:
                ET.SubElement(prog, "desc", lang="en").text = desc_en[:300]
            total += 1
    log(f"[mytvsuper] 节目数: {total}")
    return tv, total


def grab_mytvsuper(days):
    """返回 (ET根节点, 频道数, 节目数)。失败抛异常，由 main() 捕获跳过。"""
    s = datetime.now().strftime("%Y%m%d")
    e = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
    channels = mytvsuper_get_channels()
    epg = mytvsuper_fetch_all(channels.keys(), s, e)
    tv, total = mytvsuper_build_xml(channels, epg)
    return tv, len(channels), total


# =====================================================================
# 平台 2：Now TV（香港）
# 来源：EPGCollector/nowtv/nowtv_epg.py（551 行）
# 逻辑原样保留：nowplayer.now.com/tvguide/epglist 接口、channelIdList[] 批量、
# locale=zh_HK、8 个一批、0.8 秒间隔、中文标题优先级、毫秒时间戳转 +0800。
# 改动：config.json 改为内嵌数据；不写文件，返回内存里的 XML 根节点；
# 环境检查（配置文件存在性/网络探针）去掉——内嵌配置不需要，网络失败
# 本来就会在抓取时报错。
# =====================================================================
NOWTV_EPG_URL = "https://nowplayer.now.com/tvguide/epglist"
NOWTV_BATCH_SIZE = 8
NOWTV_SLEEP_TIME = 0.8
NOWTV_REQUEST_TIMEOUT = 30


def nowtv_load_config():
    config = json.loads(EMBEDDED_NOWTV_CONFIG_JSON)
    log(f"[nowtv] 从内嵌配置加载了 {len(config)} 个频道配置")
    return config


def nowtv_format_time(ts):
    """将毫秒时间戳转换为中国时区（UTC+8）的XMLTV格式"""
    try:
        dt_utc = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        dt_cn = dt_utc + timedelta(hours=8)
        return dt_cn.strftime("%Y%m%d%H%M%S +0800")
    except Exception as e:
        log(f"[nowtv] 时间转换错误: {e}")
        dt = datetime.now(tz=timezone.utc) + timedelta(hours=8)
        return dt.strftime("%Y%m%d%H%M%S +0800")


def nowtv_get_chinese_title(program):
    """从节目信息中提取中文标题，并返回标题和语言代码"""
    if not isinstance(program, dict):
        return "Unknown", "en"
    title_sources = [
        ("nameZh", "titleZh", "name_zh", "title_zh", "name_cn", "title_cn"),
        ("localizedTitle", "localizedName"),
        ("name", "title", "programmeName", "displayName", "displayTitle"),
    ]
    for field_group in title_sources:
        for field in field_group:
            if field in program and program[field]:
                value = program[field]
                if isinstance(value, dict):
                    for lang in ["zh", "zh-CN", "zh_HK", "zh_TW", "cn"]:
                        if lang in value and value[lang]:
                            title = str(value[lang]).strip()
                            if title:
                                return title, "zh"
                elif isinstance(value, str) and value.strip():
                    title = value.strip()
                    if title:
                        if any("\u4e00" <= char <= "\u9fff" for char in title):
                            return title, "zh"
                        else:
                            return title, "en"
    return "Unknown", "en"


def nowtv_fetch_epg_batch(batch_channels, day):
    """批量获取频道EPG数据"""
    params = []
    for channel_id in batch_channels:
        params.append(("channelIdList[]", channel_id))
    params.append(("day", str(day)))
    params.append(("locale", "zh_HK"))
    params.append(("lang", "zh"))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        "Referer": "https://nowplayer.now.com/tv-guide",
    }
    try:
        response = requests.get(
            NOWTV_EPG_URL, params=params, headers=headers, timeout=NOWTV_REQUEST_TIMEOUT
        )
        if response.status_code != 200:
            log(f"[nowtv] HTTP {response.status_code}: 请求失败")
            return None
        data = response.json()
        if not isinstance(data, list):
            log(f"[nowtv] 返回数据格式异常: {type(data)}")
            return None
        return data
    except requests.exceptions.Timeout:
        log("[nowtv] 请求超时")
        return None
    except Exception as e:
        log(f"[nowtv] 请求异常: {e}")
        return None


def grab_nowtv(days):
    """返回 (ET根节点, 频道数, 节目数)。"""
    config = nowtv_load_config()
    channel_ids = list(config.keys())
    log(f"[nowtv] 从config获取到 {len(channel_ids)} 个频道ID，抓取 {days} 天")
    now = datetime.now(timezone.utc)
    all_epg_data = {}
    for day in range(days):
        day_epg_count = 0
        day_channel_count = 0
        for i in range(0, len(channel_ids), NOWTV_BATCH_SIZE):
            batch = channel_ids[i:i + NOWTV_BATCH_SIZE]
            epg_data = nowtv_fetch_epg_batch(batch, day)
            if epg_data and isinstance(epg_data, list):
                for idx, channel_programs in enumerate(epg_data):
                    if idx >= len(batch):
                        break
                    channel_id = batch[idx]
                    if not channel_programs or not isinstance(channel_programs, list):
                        continue
                    if channel_id not in all_epg_data:
                        all_epg_data[channel_id] = []
                    all_epg_data[channel_id].extend(channel_programs)
                    day_epg_count += len(channel_programs)
                    day_channel_count += 1
            time.sleep(NOWTV_SLEEP_TIME)
        log(f"[nowtv] Day {day} 完成: {day_channel_count}个频道, {day_epg_count}个节目")
    if not all_epg_data:
        raise RuntimeError("没有抓取到任何EPG数据")

    tv_element = ET.Element("tv", {
        "source-info-name": "NOWTV Hong Kong",
        "source-info-url": "https://nowplayer.now.com",
        "generator-info-name": "NOWTV-EPG-Collector",
        "generator-info-url": "https://github.com/your-repo",
    })
    channel_count = 0
    for channel_id, channel_info in config.items():
        channel_name = channel_info.get("name", f"频道{channel_id}")
        if channel_id in all_epg_data:
            channel_element = ET.SubElement(tv_element, "channel", {"id": channel_name})
            display_name = ET.SubElement(channel_element, "display-name", {"lang": "zh"})
            display_name.text = channel_name
            logo = channel_info.get("logo", "")
            if logo:
                ET.SubElement(channel_element, "icon", {"src": logo})
            channel_count += 1
    program_count = 0
    for channel_id, programs in all_epg_data.items():
        if not programs:
            continue
        channel_info = config.get(channel_id, {})
        channel_name = channel_info.get("name", channel_id)
        for program in programs:
            try:
                start_time = program.get("start", 0)
                end_time = program.get("end", 0)
                if not start_time or not end_time:
                    continue
                title, lang_code = nowtv_get_chinese_title(program)
                programme_element = ET.SubElement(tv_element, "programme", {
                    "channel": channel_name,
                    "start": nowtv_format_time(start_time),
                    "stop": nowtv_format_time(end_time),
                })
                title_element = ET.SubElement(programme_element, "title", {"lang": lang_code})
                title_element.text = title
                if "description" in program and program["description"]:
                    desc_element = ET.SubElement(programme_element, "desc", {"lang": lang_code})
                    desc_element.text = str(program["description"])
                if "category" in program and program["category"]:
                    category_element = ET.SubElement(programme_element, "category", {"lang": lang_code})
                    category_element.text = str(program["category"])
                program_count += 1
            except Exception:
                continue
    if program_count == 0:
        raise RuntimeError("没有有效的节目信息")
    return tv_element, channel_count, program_count


# =====================================================================
# 平台 3：Hami Video（台湾）
# 来源：EPGCollector/hami/hami_epg.py（189 行）
# 逻辑原样保留：hamivideo.hinet.net POST、X-Requested-With 头、
# startTime/endTime 毫秒戳、DAYS 天。
# 改动：config.json 改为内嵌数据；不写文件，返回内存里的 XML 根节点。
# =====================================================================
HAMI_EPG_URL = "https://hamivideo.hinet.net/hamivideo/channel/epg.do"


def hami_load_channels():
    return json.loads(EMBEDDED_HAMI_CONFIG_JSON).get("channels", [])


def hami_fetch_epg(contentPk, date):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://hamivideo.hinet.net/hamivideo/channel/{contentPk}.do",
    }
    data = {"contentPk": contentPk, "date": date}
    r = requests.post(HAMI_EPG_URL, data=data, headers=headers, timeout=20)
    try:
        return r.json()
    except Exception:
        log(f"[hami] {contentPk} {date} 非JSON（可能被拦截或无数据）")
        return []


def hami_parse_epg(data, channel_name):
    if not isinstance(data, list):
        return []
    result = []
    for p in data:
        if not isinstance(p, dict):
            continue
        result.append({
            "channel": channel_name,
            "title": p.get("programName", ""),
            "start": int(p.get("startTime", 0)),
            "end": int(p.get("endTime", 0)),
        })
    return result


def hami_fmt(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S +0800")
    except Exception:
        return datetime.now().strftime("%Y%m%d%H%M%S +0800")


def grab_hami(days):
    """返回 (ET根节点, 频道数, 节目数)。"""
    channels = hami_load_channels()
    log(f"[hami] 频道数量: {len(channels)}")
    if not channels:
        raise RuntimeError("config 为空")
    today = datetime.now()
    all_programs = []
    for ch in channels:
        cid = ch.get("id")
        name = ch.get("name")
        if not cid:
            continue
        for d in range(days):
            date_str = (today + timedelta(days=d)).strftime("%Y-%m-%d")
            data = hami_fetch_epg(cid, date_str)
            parsed = hami_parse_epg(data, name)
            all_programs.extend(parsed)
    if not all_programs:
        raise RuntimeError("EPG为空（API限制 / 参数错误 / IP被挡）")
    tv = ET.Element("tv")
    for ch in channels:
        cid = ch["id"]
        node = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(node, "display-name").text = ch.get("name", cid)
    for p in all_programs:
        pr = ET.SubElement(tv, "programme", {
            "channel": p["channel"],
            "start": hami_fmt(p["start"]),
            "stop": hami_fmt(p["end"]),
        })
        ET.SubElement(pr, "title", lang="zh").text = p["title"]
    return tv, len(channels), len(all_programs)


# =====================================================================
# 平台 4：HOY TV（香港）
# 来源：EPGCollector/hoytv/hoytv.py（169 行）
# 逻辑原样保留：api2.hoy.tv 频道接口、各频道 epg 链接、EpgItem 解析、
# EpisodeInfo/ComScore/ProgramInfo 三级标题、删昨天以前的节目。
# 改动：不写文件，返回内存里的 XML 根节点。
# =====================================================================
HOYTV_CHANNEL_API = "https://api2.hoy.tv/api/v3/a/channel?orientation=landscape"
HOYTV_HEADERS = {"User-Agent": "Mozilla/5.0"}


def hoytv_to_xmltv_time(t):
    dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y%m%d%H%M%S") + " +0800"


def hoytv_get_channels():
    r = requests.get(HOYTV_CHANNEL_API, headers=HOYTV_HEADERS, timeout=10)
    return r.json()["data"]


def hoytv_fetch_epg(url):
    r = requests.get(url, headers=HOYTV_HEADERS, timeout=15)
    r.encoding = "utf-8"
    return ET.fromstring(r.text)


def hoytv_clean(x):
    return x.strip() if x and x.strip() else None


def hoytv_get_title(item):
    epi = item.find("EpisodeInfo")
    if epi is not None:
        title = hoytv_clean(epi.findtext("EpisodeShortDescription"))
        if title:
            idx = epi.findtext("EpisodeIndex")
            if idx and idx != "0":
                title = f"{title} 第{idx}集"
            return title
    cs = item.find("ComScore")
    if cs is not None:
        title = hoytv_clean(cs.findtext("ns_st_pr"))
        if title:
            return title
    info = item.find("ProgramInfo")
    if info is not None:
        title = hoytv_clean(info.findtext("ProgramTitle"))
        if title:
            return title
    return None


def grab_hoytv(days):
    """返回 (ET根节点, 频道数, 节目数)。days 参数本平台用不上（epg 链接自带范围）。"""
    channels = hoytv_get_channels()
    tv = ET.Element("tv")
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for ch in channels:
        ch_id = str(ch["videos"]["id"])
        ch_name = ch["name"]["zh_hk"]
        c = ET.SubElement(tv, "channel")
        c.set("id", ch_id)
        d = ET.SubElement(c, "display-name")
        d.text = ch_name
        if ch.get("image"):
            icon = ET.SubElement(c, "icon")
            icon.set("src", ch["image"])
    for ch in channels:
        ch_id = str(ch["videos"]["id"])
        epg_url = ch.get("epg")
        if not epg_url:
            continue
        log(f"[hoytv] [处理] {ch['name']['zh_hk']}")
        try:
            root = hoytv_fetch_epg(epg_url)
        except Exception:
            continue
        for item in root.findall(".//EpgItem"):
            start = item.findtext("EpgStartDateTime")
            end = item.findtext("EpgEndDateTime")
            try:
                end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if end_dt < yesterday:
                continue
            title = hoytv_get_title(item)
            if not title:
                continue
            p = ET.SubElement(tv, "programme")
            p.set("channel", ch_id)
            p.set("start", hoytv_to_xmltv_time(start))
            p.set("stop", hoytv_to_xmltv_time(end))
            t = ET.SubElement(p, "title")
            t.text = title
            total += 1
    return tv, len(channels), total


# =====================================================================
# 平台 5：meWATCH（新加坡）
# 来源：EPGCollector/mewatch/mewatch_epg.py（400 行）
# 逻辑原样保留：cdn.mewatch.sg /api/lists/239614 分页取频道、
# /api/schedules 按频道+日期取节目、0.2/0.3 秒间隔、UTC 转 +0800。
# 改动：不写文件，返回内存里的 XML 根节点；修了一个原脚本的 bug——
# icon 的 URL 里有未转义的 &，写进 XML 属性后整个文件解析失败
# （line 6 col 107），这里对 icon src 和 display-name 做转义，
# 其余逻辑不变。
# =====================================================================
MEWATCH_BASE_URL = "https://cdn.mewatch.sg"
MEWATCH_PAGE_SIZE = 24
MEWATCH_CHANNEL_LIST_ID = "239614"
MEWATCH_TOTAL_CHANNELS = 121
MEWATCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def mewatch_to_xmltv_time(utc_str):
    """将UTC时间转换为XMLTV格式（新加坡时区 UTC+8）"""
    try:
        if utc_str.endswith("Z"):
            dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        dt = dt + timedelta(hours=8)
        return dt.strftime("%Y%m%d%H%M%S +0800")
    except Exception:
        return "20000101000000 +0800"


def mewatch_fetch_all_channels():
    """获取所有频道信息"""
    log("[mewatch] 步骤1: 获取频道列表 (共121个频道)")
    all_channels = {}
    page = 1
    while True:
        log(f"[mewatch]   正在获取第 {page} 页...")
        url = f"{MEWATCH_BASE_URL}/api/lists/{MEWATCH_CHANNEL_LIST_ID}"
        params = {
            "ff": "idp,ldp,rpt,cd",
            "lang": "en",
            "page": page,
            "page_size": MEWATCH_PAGE_SIZE,
            "segments": "all",
        }
        try:
            response = requests.get(url, params=params, headers=MEWATCH_HEADERS, timeout=30)
            data = response.json()
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                channel_id = str(item.get("id", ""))
                if not channel_id:
                    continue
                channel_info = {
                    "id": channel_id,
                    "name": item.get("title", f"Channel_{channel_id}"),
                    "icon": "",
                    "description": item.get("description", ""),
                    "number": item.get("logicalChannelNumber"),
                    "genres": item.get("genres", []),
                    "isPlayable": item.get("isPlayable", False),
                    "videoFormat": item.get("videoFormat", ""),
                }
                images = item.get("images", {})
                if isinstance(images, dict):
                    for img_key in ["tile", "default", "wallpaper", "poster"]:
                        if img_key in images and images[img_key]:
                            channel_info["icon"] = images[img_key]
                            break
                all_channels[channel_id] = channel_info
            log(f"[mewatch]     第 {page} 页: 获取到 {len(items)} 个频道，累计 {len(all_channels)} 个")
            if len(all_channels) >= MEWATCH_TOTAL_CHANNELS:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            log(f"[mewatch]   获取失败: {e}")
            break
    log(f"[mewatch] 成功获取 {len(all_channels)} 个频道")
    return all_channels


def mewatch_fetch_single_channel_epg(channel_id, channel_name, days):
    """获取单个频道多天的EPG数据"""
    programmes = []
    start_date = datetime.utcnow()
    for day in range(days):
        current_date = (start_date + timedelta(days=day)).strftime("%Y-%m-%d")
        url = f"{MEWATCH_BASE_URL}/api/schedules"
        params = {
            "channels": channel_id,
            "date": current_date,
            "duration": 24,
            "hour": 0,
            "intersect": "true",
            "lang": "en",
            "segments": "all",
            "ff": "idp,ldp,rpt,cd",
        }
        try:
            response = requests.get(url, params=params, headers=MEWATCH_HEADERS, timeout=30)
            data = response.json()
            schedules = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "schedules" in item:
                        schedules.extend(item.get("schedules", []))
            elif isinstance(data, dict) and "schedules" in data:
                schedules = data.get("schedules", [])
            for schedule in schedules:
                if not isinstance(schedule, dict) or schedule.get("isGap"):
                    continue
                item_data = schedule.get("item", {})
                programme = {
                    "channel": channel_id,
                    "start": schedule.get("startDate"),
                    "stop": schedule.get("endDate"),
                    "title": item_data.get("title", "No Title"),
                    "description": item_data.get("description", ""),
                    "episode_title": item_data.get("episodeTitle", ""),
                    "season": item_data.get("seasonNumber"),
                    "episode": item_data.get("episodeNumber"),
                    "classification": item_data.get("classification", {}),
                    "images": item_data.get("images", {}),
                    "duration": schedule.get("duration"),
                }
                programmes.append(programme)
            time.sleep(0.2)
        except Exception as e:
            log(f"[mewatch]     获取 {current_date} 失败: {e}")
            continue
    return programmes


def mewatch_fetch_all_epg_data(channels, days):
    """获取所有频道的EPG数据"""
    log(f"[mewatch] 步骤2: 获取全部 {len(channels)} 个频道 {days} 天的EPG数据")
    all_programmes = []
    channel_ids = list(channels.keys())
    for i, channel_id in enumerate(channel_ids, 1):
        channel_name = channels[channel_id]["name"]
        log(f"[mewatch] [{i}/{len(channel_ids)}] 处理频道: {channel_name} ({channel_id})")
        channel_programs = mewatch_fetch_single_channel_epg(channel_id, channel_name, days)
        all_programmes.extend(channel_programs)
        log(f"[mewatch]     完成: {len(channel_programs)} 个节目")
    log(f"[mewatch] EPG数据获取完成，总节目数: {len(all_programmes)}")
    return all_programmes


def mewatch_generate_xmltv(channels, programmes):
    """生成XMLTV格式文件（返回 ET 根节点）。
    注意：原脚本用字符串拼接写 XML，icon 的 URL 里未转义的 & 会导致
    解析失败（line 6 col 107）。这里对 icon src 和 display-name、
    desc 做转义，其余逻辑不变。"""
    xml_lines = []
    xml_lines.append('<?xml version="1.0" encoding="UTF-8" ?>')
    xml_lines.append('<tv generator-info-name="meWatch EPG" source-info-url="https://www.mewatch.sg">')
    for channel_id, channel_info in channels.items():
        xml_lines.append(f'  <channel id="{xml_escape(channel_id, {"\"": "&quot;"})}">')
        xml_lines.append(f'    <display-name>{xml_escape(channel_info["name"])}</display-name>')
        if channel_info["description"]:
            desc = xml_escape(channel_info["description"])
            xml_lines.append(f'    <desc>{desc}</desc>')
        if channel_info["icon"]:
            xml_lines.append(f'    <icon src="{xml_escape(channel_info["icon"], {"\"": "&quot;"})}" />')
        xml_lines.append('  </channel>')
    programme_count = 0
    for prog in programmes:
        start_time = prog.get("start")
        stop_time = prog.get("stop")
        if not start_time or not stop_time:
            continue
        start_xml = mewatch_to_xmltv_time(start_time)
        stop_xml = mewatch_to_xmltv_time(stop_time)
        xml_lines.append(f'  <programme start="{start_xml}" stop="{stop_xml}" channel="{xml_escape(prog["channel"], {"\"": "&quot;"})}">')
        title = xml_escape(prog.get("title", ""))
        xml_lines.append(f'    <title lang="en">{title}</title>')
        description = prog.get("description", "")
        if description:
            description = xml_escape(description)
            xml_lines.append(f'    <desc lang="en">{description}</desc>')
        classification = prog.get("classification", {})
        if isinstance(classification, dict) and classification.get("name"):
            category = xml_escape(classification["name"])
            xml_lines.append(f'    <category lang="en">{category}</category>')
        xml_lines.append('  </programme>')
        programme_count += 1
    xml_lines.append('</tv>')
    log(f"[mewatch] 添加了 {programme_count} 个节目")
    return ET.fromstring("\n".join(xml_lines)), programme_count


def grab_mewatch(days):
    """返回 (ET根节点, 频道数, 节目数)。"""
    channels = mewatch_fetch_all_channels()
    if not channels:
        raise RuntimeError("未能获取到任何频道")
    programmes = mewatch_fetch_all_epg_data(channels, days)
    if not programmes:
        raise RuntimeError("未能获取到任何节目数据")
    tv, count = mewatch_generate_xmltv(channels, programmes)
    return tv, len(channels), count


# =====================================================================
# 平台 6：beIN
# 来源：EPGCollector/bein/bein_epg.py（348 行）
# 逻辑原样保留：bein.com epg-ajax-template、sports/entertainment 两类、
# slider 截取 + <li> 解析、mapping.json 映射、原始时间不转时区（+0000）。
# 改动：mapping.json 改为内嵌数据；不写文件，返回内存里的 XML 根节点。
# =====================================================================
BEIN_BASE_URL = "https://www.bein.com/en/epg-ajax-template/"
BEIN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bein.com/en/tv-guide/",
    "X-Requested-With": "XMLHttpRequest",
}


def bein_load_channel_mapping():
    """加载内嵌 mapping.json 并构建映射"""
    try:
        mapping_data = json.loads(EMBEDDED_BEIN_MAPPING_JSON)
        channel_map = {}
        for slider_key, channel_info in mapping_data.items():
            if "name" in channel_info:
                channel_name = channel_info["name"]
                channel_id = re.sub(r"[^a-z0-9]+", "_", channel_name.lower()).strip("_")
                original_id = channel_id
                suffix = 1
                while channel_id in channel_map:
                    channel_id = f"{original_id}_{suffix}"
                    suffix += 1
                channel_map[slider_key] = {
                    "id": channel_id,
                    "name": channel_name,
                    "logo": channel_info.get("logo", ""),
                    "original_key": slider_key,
                }
        log(f"[bein] 已加载 {len(channel_map)} 个频道映射")
        return channel_map
    except Exception as e:
        log(f"[bein] 加载mapping失败: {e}")
        return {}


def bein_fetch_html(date_str, category):
    """获取HTML源代码 - 使用原始脚本的参数"""
    params = {
        "action": "epg_fetch",
        "offset": "0",
        "category": category,
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": date_str,
        "language": "EN",
        "postid": "25356",
        "loadindex": "0",
    }
    try:
        r = requests.get(BEIN_BASE_URL, params=params, headers=BEIN_HEADERS, timeout=20)
        r.raise_for_status()
        log(f"[bein] 获取{category}数据: {len(r.text):,} 字节")
        return r.text
    except Exception as e:
        log(f"[bein] 获取{category}失败: {e}")
        return ""


def bein_parse_html_original(html, category):
    """使用原始脚本的解析逻辑"""
    all_programs = []
    if not html:
        return all_programs
    slider_ids = re.findall(r'id=["\'](slider_\d+)["\']', html)
    slider_ids = list(dict.fromkeys(slider_ids))
    log(f"[bein]   [{category}] 找到 {len(slider_ids)} 个slider")
    for sid in slider_ids:
        cid = f"{category}_{sid}"
        block_match = re.search(
            rf'<div[^>]+id=["\']{sid}["\'][\s\S]*?<ul[\s\S]*?</ul>',
            html,
        )
        if not block_match:
            continue
        slider_html = block_match.group(0)
        items = re.findall(r"<li[^>]*>(.*?)</li>", slider_html, re.S)
        for it in items:
            title_match = re.search(r"class=title[^>]*>(.*?)<", it, re.S)
            time_match = re.search(r"(\d{2}:\d{2}).*?(\d{2}:\d{2})", it)
            if not title_match or not time_match:
                continue
            title = title_match.group(1).strip()
            start, end = time_match.groups()
            all_programs.append({
                "channel_temp_id": cid,
                "title": title,
                "start": start,
                "end": end,
            })
    return all_programs


def bein_process_all_days(channel_map, days):
    """处理所有天数的数据"""
    all_programs = []
    for d in range(days):
        date = (datetime.now().date() + timedelta(days=d)).strftime("%Y-%m-%d")
        log(f"[bein] 处理日期: {date} (第{d+1}天)")
        for category in ["sports", "entertainment"]:
            html = bein_fetch_html(date, category)
            if html:
                programs = bein_parse_html_original(html, category)
                for prog in programs:
                    prog["date"] = date
                    prog["category"] = category
                all_programs.extend(programs)
                log(f"[bein]   找到 {len(programs)} 个{category}节目")
    return all_programs


def bein_convert_programs_with_mapping(all_programs, channel_map):
    """将节目从临时ID转换为最终频道ID"""
    converted_programs = []
    channel_info_map = {}
    for prog in all_programs:
        temp_id = prog["channel_temp_id"]
        if temp_id in channel_map:
            channel_info = channel_map[temp_id]
            final_channel_id = channel_info["id"]
            final_channel_name = channel_info["name"]
            if final_channel_id not in channel_info_map:
                channel_info_map[final_channel_id] = {
                    "name": final_channel_name,
                    "logo": channel_info.get("logo", ""),
                }
            converted_prog = prog.copy()
            converted_prog["channel_id"] = final_channel_id
            converted_prog["channel_name"] = final_channel_name
            converted_programs.append(converted_prog)
        else:
            parts = temp_id.split("_")
            if len(parts) >= 3:
                try:
                    slider_num = int(parts[-1])
                    clean_temp_id = f"{parts[0]}_{parts[1]}_{slider_num}"
                    if clean_temp_id in channel_map:
                        channel_info = channel_map[clean_temp_id]
                        final_channel_id = channel_info["id"]
                        final_channel_name = channel_info["name"]
                        if final_channel_id not in channel_info_map:
                            channel_info_map[final_channel_id] = {
                                "name": final_channel_name,
                                "logo": channel_info.get("logo", ""),
                            }
                        converted_prog = prog.copy()
                        converted_prog["channel_id"] = final_channel_id
                        converted_prog["channel_name"] = final_channel_name
                        converted_programs.append(converted_prog)
                except ValueError:
                    continue
    return converted_programs, channel_info_map


def bein_generate_xml(channel_info_map, all_programs):
    """生成XML（直接使用抓取的原始时间，不进行时区转换），返回 ET 根节点。"""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', "<tv>"]
    for channel_id, channel_info in channel_info_map.items():
        xml.append(f'  <channel id="{channel_id}">')
        xml.append(f'    <display-name>{channel_info["name"]}</display-name>')
        if channel_info["logo"]:
            xml.append(f'    <icon src="{channel_info["logo"]}"/>')
        xml.append("  </channel>")
    timezone_offset = "+0000"
    for prog in all_programs:
        try:
            date_obj = datetime.strptime(prog["date"], "%Y-%m-%d")
            sh, sm = map(int, prog["start"].split(":"))
            eh, em = map(int, prog["end"].split(":"))
            start_dt = date_obj.replace(hour=sh, minute=sm, second=0)
            end_dt = date_obj.replace(hour=eh, minute=em, second=0)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            start_str = start_dt.strftime("%Y%m%d%H%M%S") + timezone_offset
            end_str = end_dt.strftime("%Y%m%d%H%M%S") + timezone_offset
            title = prog["title"]
            for old, new in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")]:
                title = title.replace(old, new)
            xml.append(f'  <programme start="{start_str}" stop="{end_str}" channel="{prog["channel_id"]}">')
            xml.append(f"    <title>{title}</title>")
            xml.append(f'    <desc>{prog["start"]} - {prog["end"]}</desc>')
            xml.append("  </programme>")
        except Exception:
            continue
    xml.append("</tv>")
    return ET.fromstring("\n".join(xml))


def grab_bein(days):
    """返回 (ET根节点, 频道数, 节目数)。"""
    channel_map = bein_load_channel_mapping()
    if not channel_map:
        raise RuntimeError("频道映射为空")
    raw_programs = bein_process_all_days(channel_map, days)
    if not raw_programs:
        raise RuntimeError("没有找到任何节目")
    all_programs, channel_info_map = bein_convert_programs_with_mapping(raw_programs, channel_map)
    if not all_programs:
        raise RuntimeError("频道映射转换失败")
    log(f"[bein] 最终统计: {len(channel_info_map)} 个频道, {len(all_programs)} 个节目")
    tv = bein_generate_xml(channel_info_map, all_programs)
    return tv, len(channel_info_map), len(all_programs)


# =====================================================================
# 平台 7：beIN SPORTS
# 来源：EPGCollector/beinsports/beinsports_epg.py（761 行）
# 逻辑原样保留：beinsports.com/api/opta tv-event、channelIds 批量 8 个、
# 3 次重试、原始时区不转换、generate_stable_xml_id。
# 改动：config.json 改为内嵌数据；不写文件，返回内存里的 XML 根节点。
# =====================================================================
BEINSPORTS_BASE_API = "https://www.beinsports.com/api/opta"
BEINSPORTS_REQUEST_TIMEOUT = 30
BEINSPORTS_MAX_RETRIES = 3
BEINSPORTS_REQUEST_DELAY = 0.5
BEINSPORTS_DAYS_BACK = 1
BEINSPORTS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,es;q=0.7,ar;q=0.6",
    "Referer": "https://www.beinsports.com/",
    "Origin": "https://www.beinsports.com",
}


def beinsports_log(msg, error=False):
    log(f"[beinsports] {msg}")


def beinsports_iso_format(dt):
    """转换为ISO 8601格式（API要求）"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def beinsports_xmltv_time(dt):
    """转换为XMLTV时间格式（使用原始时区，不进行转换）"""
    if dt.tzinfo is not None:
        offset = dt.utcoffset()
        if offset is not None:
            offset_seconds = offset.total_seconds()
            offset_hours = int(offset_seconds // 3600)
            offset_minutes = int((offset_seconds % 3600) // 60)
            offset_str = f"{offset_hours:+03d}{offset_minutes:02d}"
        else:
            offset_str = "+0000"
    else:
        offset_str = "+0000"
    time_str = dt.strftime("%Y%m%d%H%M%S")
    return f"{time_str} {offset_str}"


def beinsports_parse_iso_time(iso_str):
    """解析ISO时间字符串，保留时区信息"""
    try:
        if iso_str.endswith("Z"):
            dt = datetime.fromisoformat(iso_str[:-1] + "+00:00")
        elif "T" in iso_str and "+" in iso_str:
            dt = datetime.fromisoformat(iso_str)
        elif "T" in iso_str and "-" in iso_str[-6:]:
            dt = datetime.fromisoformat(iso_str)
        else:
            dt = datetime.fromisoformat(iso_str)
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as e:
        beinsports_log(f"时间解析失败: {iso_str} - {e}")
        return None


def beinsports_normalize_string(text):
    """规范化字符串，移除控制字符"""
    if not text:
        return ""
    return "".join(char for char in text if ord(char) >= 32 or char in "\n\r\t")


def beinsports_generate_stable_xml_id(channel_data):
    """生成稳定、唯一的XML频道ID"""
    channel_id = channel_data.get("id", "")
    name = channel_data.get("name", "").lower()
    if not channel_id or not name:
        return "unknown"
    id_prefix = channel_id[:6] if len(channel_id) >= 6 else channel_id
    id_prefix = id_prefix.upper()
    if "4k" in name:
        base_id = "bein4k"
    elif "español" in name or "esp" in name:
        base_id = "beinespanol"
    elif "français" in name or "fr " in name:
        base_id = "beinfr"
    elif "english" in name or "en " in name:
        base_id = "beinen"
    elif "xtra" in name and "ñ" in name:
        base_id = "beinextra_es"
    elif "xtra" in name:
        base_id = "beinextra"
    elif "max" in name:
        max_match = re.search(r"max\s*(\d+)", name)
        if max_match:
            base_id = f"beinmax{max_match.group(1)}"
        else:
            base_id = "beinmax"
    elif "bein sports" in name:
        num_match = re.search(r"bein sports\s*(\d+)", name)
        if num_match:
            base_id = f"beinsports{num_match.group(1)}"
        else:
            base_id = "beinsports"
    else:
        clean_name = re.sub(r"[^a-z0-9]", "_", name)
        base_id = clean_name[:15].strip("_")
        if not base_id:
            base_id = "bein"
    return f"{base_id}_{id_prefix}"


def beinsports_load_config():
    """加载内嵌配置文件"""
    config = json.loads(EMBEDDED_BEINSPORTS_CONFIG_JSON)
    if isinstance(config, list):
        config = {
            "generated_at": datetime.now().isoformat(),
            "total_channels": len(config),
            "channels": config,
        }
    elif isinstance(config, dict) and "channels" in config:
        pass
    elif isinstance(config, dict):
        if any(key in config for key in ["id", "name", "icon"]):
            config = {
                "generated_at": datetime.now().isoformat(),
                "total_channels": 1,
                "channels": [config],
            }
        elif "channels" not in config:
            config["channels"] = []
            config["total_channels"] = 0
    else:
        raise RuntimeError(f"未知的配置文件格式: {type(config)}")
    if "channels" not in config:
        config["channels"] = []
    actual_channels = len(config.get("channels", []))
    config["total_channels"] = actual_channels
    log(f"[beinsports] 配置文件加载成功，频道总数: {actual_channels}")
    return config


def beinsports_extract_channel_info(channel_data):
    """从原始频道数据中提取规范化信息"""
    channel_id = channel_data.get("id", "")
    name = channel_data.get("name", "")
    if not channel_id or not name:
        return None
    icon = channel_data.get("icon") or channel_data.get("logo")
    display_name = channel_data.get("display_name") or name
    name_lower = name.lower()
    if "español" in name_lower or "ñ" in name_lower:
        language = "es"
    elif "français" in name_lower or "france" in str(channel_data.get("country", "")).lower():
        language = "fr"
    elif "english" in name_lower or "en" in str(channel_data.get("region", "")).lower():
        language = "en"
    elif "arabic" in name_lower or "ar" in str(channel_data.get("region", "")).lower():
        language = "ar"
    else:
        language = "en"
    xml_id = beinsports_generate_stable_xml_id(channel_data)
    return {
        "id": channel_id,
        "xml_id": xml_id,
        "name": name,
        "display_name": display_name,
        "icon": icon,
        "language": language,
        "country": channel_data.get("country"),
        "region": channel_data.get("region", "unknown"),
        "provider": channel_data.get("provider", "unknown"),
        "external_id": channel_data.get("external_id"),
    }


def beinsports_process_channels(config):
    """处理所有频道数据，返回频道列表和映射表"""
    if not config or "channels" not in config:
        return [], {}, {}
    raw_channels = config["channels"]
    processed_channels = []
    id_to_channel = {}
    xml_id_to_channel = {}
    for raw_channel in raw_channels:
        if not isinstance(raw_channel, dict):
            continue
        channel_info = beinsports_extract_channel_info(raw_channel)
        if not channel_info:
            continue
        processed_channels.append(channel_info)
        id_to_channel[channel_info["id"]] = channel_info
        xml_id_to_channel[channel_info["xml_id"]] = channel_info
    processed_channels.sort(key=lambda x: x["display_name"].lower())
    log(f"[beinsports] 处理完成: {len(processed_channels)} 个频道")
    return processed_channels, id_to_channel, xml_id_to_channel


def beinsports_make_api_request(url, params):
    """发送API请求，带重试机制"""
    for attempt in range(BEINSPORTS_MAX_RETRIES):
        try:
            response = requests.get(
                url, headers=BEINSPORTS_HEADERS, params=params, timeout=BEINSPORTS_REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                return response.json()
            else:
                beinsports_log(f"HTTP {response.status_code}: {url}")
        except requests.exceptions.Timeout:
            beinsports_log(f"请求超时 (尝试 {attempt + 1}/{BEINSPORTS_MAX_RETRIES}): {url}")
        except Exception as e:
            beinsports_log(f"请求异常: {e}")
        if attempt < BEINSPORTS_MAX_RETRIES - 1:
            time.sleep(BEINSPORTS_REQUEST_DELAY * (attempt + 1))
    return None


def beinsports_fetch_epg_data(channel_ids, start_time, end_time):
    """获取EPG数据"""
    if not channel_ids:
        return []
    url = f"{BEINSPORTS_BASE_API}/tv-event"
    params = [
        ("searchKey", ""),
        ("startBefore", beinsports_iso_format(end_time)),
        ("endAfter", beinsports_iso_format(start_time)),
        ("limit", "5000"),
    ]
    for channel_id in channel_ids:
        params.append(("channelIds", channel_id))
    log(f"[beinsports] 获取EPG数据: {len(channel_ids)} 个频道")
    data = beinsports_make_api_request(url, params)
    if not data:
        log("[beinsports] 获取EPG数据失败")
        return []
    programs = []
    if isinstance(data, list):
        programs = data
    elif isinstance(data, dict):
        for key in ["rows", "data", "items", "events"]:
            if key in data and isinstance(data[key], list):
                programs = data[key]
                break
    log(f"[beinsports] 获取到 {len(programs)} 个节目")
    return programs


def beinsports_parse_program_data(program, id_to_channel):
    """解析单个节目数据"""
    try:
        channel_id = program.get("channelId")
        if not channel_id or channel_id not in id_to_channel:
            return None
        channel_info = id_to_channel[channel_id]
        start_time = program.get("startDate")
        end_time = program.get("endDate")
        if not start_time or not end_time:
            return None
        start_dt = beinsports_parse_iso_time(start_time)
        end_dt = beinsports_parse_iso_time(end_time)
        if not start_dt or not end_dt:
            return None
        title = (
            program.get("title")
            or program.get("competitionName")
            or program.get("sport")
            or program.get("name")
            or "Sports Event"
        )
        description = program.get("description") or ""
        home_team = program.get("homeTeamName") or program.get("homeTeam")
        away_team = program.get("awayTeamName") or program.get("awayTeam")
        sport = program.get("sport") or ""
        return {
            "xml_channel_id": channel_info["xml_id"],
            "channel_language": channel_info["language"],
            "channel_name": channel_info["display_name"],
            "title": beinsports_normalize_string(title),
            "description": beinsports_normalize_string(description),
            "start_time": start_dt,
            "end_time": end_dt,
            "home_team": beinsports_normalize_string(home_team) if home_team else None,
            "away_team": beinsports_normalize_string(away_team) if away_team else None,
            "sport": beinsports_normalize_string(sport),
            "competition": beinsports_normalize_string(program.get("competitionName") or ""),
            "season": program.get("season"),
            "round": program.get("round"),
            "episode_num": program.get("episodeNumber"),
            "source_id": channel_id,
            "program_id": program.get("id"),
        }
    except Exception as e:
        beinsports_log(f"解析节目数据失败: {e}")
        return None


def grab_beinsports(days):
    """返回 (ET根节点, 频道数, 节目数)。"""
    config = beinsports_load_config()
    channels, id_to_channel, _xml_id_to_channel = beinsports_process_channels(config)
    if not channels:
        raise RuntimeError("没有可用的频道数据")
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=BEINSPORTS_DAYS_BACK)
    end_time = now + timedelta(days=days)
    log(f"[beinsports] EPG时间范围: {start_time:%Y-%m-%d} 到 {end_time:%Y-%m-%d}")
    all_programs = []
    channel_ids = [ch["id"] for ch in channels]
    batch_size = 8
    for i in range(0, len(channel_ids), batch_size):
        batch = channel_ids[i:i + batch_size]
        programs = beinsports_fetch_epg_data(batch, start_time, end_time)
        if programs:
            all_programs.extend(programs)
        if i + batch_size < len(channel_ids):
            time.sleep(BEINSPORTS_REQUEST_DELAY)
    tv_root = ET.Element("tv")
    tv_root.set("source-info-name", "beIN SPORTS")
    tv_root.set("source-info-url", "https://www.beinsports.com")
    tv_root.set("generator-info-name", "beIN-SPORTS-EPG-Generator-Pro")
    tv_root.set("generator-info-url", "")
    tv_root.set("date", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000"))
    for channel in channels:
        channel_elem = ET.SubElement(tv_root, "channel", id=channel["xml_id"])
        display_name = ET.SubElement(channel_elem, "display-name", lang=channel["language"])
        display_name.text = channel["display_name"]
        if channel["name"] != channel["display_name"]:
            alt_name = ET.SubElement(channel_elem, "display-name", lang=channel["language"])
            alt_name.text = channel["name"]
        if channel.get("icon"):
            ET.SubElement(channel_elem, "icon", src=channel["icon"])
        url_elem = ET.SubElement(channel_elem, "url")
        url_elem.text = f"beinsports:{channel['id']}"
        if channel.get("region"):
            ET.SubElement(channel_elem, "region").text = channel["region"]
        if channel.get("provider"):
            ET.SubElement(channel_elem, "provider").text = channel["provider"]
        if channel.get("country"):
            ET.SubElement(channel_elem, "country").text = channel["country"]
        if channel.get("external_id"):
            ET.SubElement(channel_elem, "external-id").text = str(channel["external_id"])
    added_count = 0
    seen_programs = set()
    for program in all_programs:
        program_info = beinsports_parse_program_data(program, id_to_channel)
        if not program_info:
            continue
        program_key = (
            program_info["xml_channel_id"],
            program_info["start_time"].isoformat(),
            program_info["end_time"].isoformat(),
            program_info["title"],
        )
        if program_key in seen_programs:
            continue
        seen_programs.add(program_key)
        prog_elem = ET.SubElement(tv_root, "programme", {
            "channel": program_info["xml_channel_id"],
            "start": beinsports_xmltv_time(program_info["start_time"]),
            "stop": beinsports_xmltv_time(program_info["end_time"]),
        })
        prog_elem.set("source-id", program_info["source_id"])
        if program_info.get("program_id"):
            prog_elem.set("program-id", program_info["program_id"])
        title_elem = ET.SubElement(prog_elem, "title", lang=program_info["channel_language"])
        title_elem.text = program_info["title"]
        if program_info["home_team"] and program_info["away_team"]:
            sub_title = f"{program_info['home_team']} vs {program_info['away_team']}"
            sub_elem = ET.SubElement(prog_elem, "sub-title", lang=program_info["channel_language"])
            sub_elem.text = sub_title
        if program_info["description"]:
            desc_elem = ET.SubElement(prog_elem, "desc", lang=program_info["channel_language"])
            desc_elem.text = program_info["description"]
        category_elem = ET.SubElement(prog_elem, "category", lang=program_info["channel_language"])
        category_elem.text = "Sports"
        if program_info["sport"]:
            sport_elem = ET.SubElement(prog_elem, "category", lang=program_info["channel_language"])
            sport_elem.text = program_info["sport"]
        if program_info["competition"]:
            competition_elem = ET.SubElement(prog_elem, "category", lang=program_info["channel_language"])
            competition_elem.text = program_info["competition"]
        if program_info.get("episode_num"):
            episode_elem = ET.SubElement(prog_elem, "episode-num", system="onscreen")
            episode_elem.text = str(program_info["episode_num"])
        added_count += 1
    log(f"[beinsports] 添加了 {added_count} 个节目（去重后）")
    return tv_root, len(channels), added_count


# =====================================================================
# 平台 8：MNC Vision（印尼）
# 来源：EPGCollector/mncvision_epg/mncvision_epg.py（162 行）
# 逻辑原样保留：mncvision.id/schedule/table、<option> 频道列表、
# POST 查节目、<td class="text-center"> 时间 + title="..." 节目名、
# 今天+明天 2 天、0.8 秒间隔、+0700。
# 改动：不写文件，返回内存里的 XML 根节点。
# =====================================================================
MNCVISION_BASE_URL = "https://www.mncvision.id/schedule/table"
MNCVISION_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": MNCVISION_BASE_URL,
    "Origin": "https://www.mncvision.id",
}


def mncvision_fetch_channels():
    log("[mncvision] 获取频道列表...")
    res = requests.get(MNCVISION_BASE_URL, headers=MNCVISION_HEADERS, timeout=20)
    log(f"[mncvision] 状态码: {res.status_code}")
    res.raise_for_status()
    matches = re.findall(r'<option value="([^"]+)">([^<]+)</option>', res.text)
    channels = []
    for v, n in matches:
        n = re.sub(r"\s*-\s*\[Channel\s*\d+\]\s*$", "", n.strip())
        channels.append({"value": v, "name": n})
    log(f"[mncvision] 频道数量: {len(channels)}")
    return channels


def mncvision_fetch_schedule(channel_value, date):
    data = {
        "search_model": "channel",
        "af0rmelement": "aformelement",
        "fdate": date,
        "fchannel": channel_value,
        "submit": "Cari",
    }
    res = requests.post(MNCVISION_BASE_URL, headers=MNCVISION_HEADERS, data=data, timeout=20)
    log(f"[mncvision] {channel_value} -> {res.status_code}")
    res.raise_for_status()
    times = re.findall(r'<td class="text-center">(.*?)</td>', res.text)
    programs = re.findall(r'title="(.*?)" rel', res.text)
    return times, programs


def mncvision_build_xml(date_prefix, times, programs, channel_name):
    xml = ""
    count = min(len(programs), len(times) // 2 - 1)
    for i in range(count):
        start = date_prefix + times[i * 2].replace(":", "") + "00 +0700"
        stop = date_prefix + times[(i + 1) * 2].replace(":", "") + "00 +0700"
        # 原脚本只转义 &，这里顺带转义 < >，否则 ET 解析整段 XML 会失败
        title = xml_escape(programs[i])
        xml += f'<programme start="{start}" stop="{stop}" channel="{xml_escape(channel_name, {"\"": "&quot;"})}">\n'
        xml += f"<title>{title}</title>\n"
        xml += "</programme>\n"
    return xml


def grab_mncvision(days):
    """返回 (ET根节点, 频道数, 节目数)。本平台固定今天+明天 2 天。"""
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    d1 = today.strftime("%Y-%m-%d")
    d2 = tomorrow.strftime("%Y-%m-%d")
    d1_xml = today.strftime("%Y%m%d")
    d2_xml = tomorrow.strftime("%Y%m%d")
    channels = mncvision_fetch_channels()
    total = len(channels)
    xml = '<?xml version="1.0" encoding="UTF-8"?><tv>\n'
    for ch in channels:
        xml += f'  <channel id="{xml_escape(ch["name"], {"\"": "&quot;"})}">\n'
        xml += f'    <display-name>{xml_escape(ch["name"])}</display-name>\n'
        xml += "  </channel>\n"
    prog_total = 0
    for i, ch in enumerate(channels, 1):
        log(f"[mncvision] [{i}/{total}] {ch['name']}")
        try:
            t1, p1 = mncvision_fetch_schedule(ch["value"], d1)
            xml += mncvision_build_xml(d1_xml, t1, p1, ch["name"])
            prog_total += min(len(p1), len(t1) // 2 - 1)
            t2, p2 = mncvision_fetch_schedule(ch["value"], d2)
            xml += mncvision_build_xml(d2_xml, t2, p2, ch["name"])
            prog_total += min(len(p2), len(t2) // 2 - 1)
            time.sleep(0.8)
        except Exception as e:
            log(f"[mncvision] 失败: {ch['name']}: {e}")
    xml += "</tv>"
    tv = ET.fromstring(xml)
    return tv, len(channels), max(prog_total, 0)


# =====================================================================
# 平台 9：StarHub（新加坡）
# 来源：EPGCollector/starhub/starhub_epg.py（514 行）
# 逻辑原样保留：waf-starhub-metadata-api、分页取频道（platform_id 去重）、
# schedules 批量 20 个、未来 2 天、0.2 秒间隔、device=1 失败换 device=2。
# 改动：不写文件、不生成 m3u，返回内存里的 XML 根节点。
# =====================================================================
STARHUB_BASE_URL = "https://waf-starhub-metadata-api-p001.ifs.vubiquity.com/v3.1"
STARHUB_CHANNEL_API = f"{STARHUB_BASE_URL}/epg/channels"
STARHUB_SCHEDULE_API = f"{STARHUB_BASE_URL}/epg/schedules"
STARHUB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
STARHUB_SGT = timezone(timedelta(hours=8))
STARHUB_BATCH_SIZE = 20
STARHUB_EPG_DAYS_AHEAD = 2
STARHUB_REQUEST_DELAY = 0.2
STARHUB_REQUEST_TIMEOUT = 30


def starhub_format_xmltv_time(timestamp):
    """将 Unix 时间戳转换为 XMLTV 时间格式"""
    try:
        dt = datetime.fromtimestamp(timestamp, STARHUB_SGT)
        return dt.strftime("%Y%m%d%H%M%S") + " +0800"
    except Exception as e:
        log(f"[starhub] 时间格式转换失败: {e}")
        return ""


def starhub_fix_image_url(url):
    """修复不完整的图片 URL"""
    if not url or not isinstance(url, str):
        return ""
    if url.startswith("https:///"):
        return url.replace("https:///", "https://")
    return url


def starhub_get_all_channels():
    """获取所有频道信息（自动分页），返回去重后的频道列表。"""
    log("[starhub] 正在获取频道列表...")
    all_channels = []
    page = 1
    while True:
        try:
            params = {
                "locale": "zh",
                "locale_default": "en_US",
                "device": 1,
                "limit": 50,
                "page": page,
            }
            response = requests.get(STARHUB_CHANNEL_API, params=params,
                                    headers=STARHUB_HEADERS, timeout=STARHUB_REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            channels_batch = data.get("resources", [])
            if not channels_batch:
                log(f"[starhub] 第 {page} 页无频道数据")
                break
            all_channels.extend(channels_batch)
            page_info = data.get("page", {})
            current_page = page_info.get("current", page)
            total_pages = page_info.get("total", 1)
            log(f"[starhub] 第 {current_page}/{total_pages} 页: 获取到 "
                f"{page_info.get('items_count', 0)} 个频道 (总计: {page_info.get('total_items_count', 0)})")
            if current_page >= total_pages:
                break
            page = current_page + 1
            time.sleep(STARHUB_REQUEST_DELAY)
        except requests.exceptions.RequestException as e:
            log(f"[starhub] 获取第 {page} 页频道失败: {e}")
            break
        except Exception as e:
            log(f"[starhub] 处理第 {page} 页频道时出错: {e}")
            break
    if not all_channels:
        log("[starhub] 未获取到任何频道数据")
        return []
    unique_channels = {}
    for channel in all_channels:
        platform_id = channel.get("platform_id")
        if platform_id:
            unique_channels[platform_id] = channel
    log(f"[starhub] 获取完成: 共 {len(all_channels)} 个频道，去重后 {len(unique_channels)} 个")
    return list(unique_channels.values())


def starhub_get_epg_schedules(channels, device_type=1):
    """获取多个频道的节目时间表，返回 {platform_id: [节目]}。"""
    log(f"[starhub] 正在获取节目表 (设备类型: {device_type})...")
    channel_ids = []
    id_to_platform = {}
    for channel in channels:
        internal_id = channel.get("id")
        platform_id = channel.get("platform_id")
        if internal_id and platform_id:
            channel_ids.append(internal_id)
            id_to_platform[internal_id] = platform_id
    if not channel_ids:
        log("[starhub] 无有效的频道ID，跳过节目获取")
        return {}
    start_time = int(time.time())
    end_time = start_time + (STARHUB_EPG_DAYS_AHEAD * 24 * 3600)
    from collections import defaultdict
    schedule_dict = defaultdict(list)
    successful_batches = 0
    for i in range(0, len(channel_ids), STARHUB_BATCH_SIZE):
        batch_num = (i // STARHUB_BATCH_SIZE) + 1
        batch_ids = channel_ids[i:i + STARHUB_BATCH_SIZE]
        try:
            params = {
                "locale": "zh",
                "locale_default": "en_US",
                "device": device_type,
                "in_channel_id": ",".join(batch_ids),
                "lte_start": end_time,
                "gt_end": start_time,
                "limit": 500,
            }
            response = requests.get(STARHUB_SCHEDULE_API, params=params,
                                    headers=STARHUB_HEADERS, timeout=STARHUB_REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            programmes = data.get("resources", [])
            for prog in programmes:
                channel_platform_id = prog.get("channel_platform_id")
                if channel_platform_id:
                    if channel_platform_id not in id_to_platform.values():
                        channel_id = prog.get("channel_id")
                        if channel_id and channel_id in id_to_platform:
                            channel_platform_id = id_to_platform[channel_id]
                    schedule_dict[channel_platform_id].append(prog)
            successful_batches += 1
            log(f"[starhub] 批次 {batch_num}: 获取到 {len(programmes)} 个节目")
            time.sleep(STARHUB_REQUEST_DELAY)
        except requests.exceptions.RequestException as e:
            log(f"[starhub] 批次 {batch_num} 获取失败: {e}")
        except Exception as e:
            log(f"[starhub] 处理批次 {batch_num} 时出错: {e}")
    for channel_id, prog_list in schedule_dict.items():
        schedule_dict[channel_id] = sorted(prog_list, key=lambda x: x.get("start", 0))
    total_programmes = sum(len(progs) for progs in schedule_dict.values())
    log(f"[starhub] 节目获取完成: 成功 {successful_batches} 个批次，共 {total_programmes} 个节目，"
        f"分布在 {len(schedule_dict)} 个频道")
    return schedule_dict


def grab_starhub(days):
    """返回 (ET根节点, 频道数, 节目数)。本平台固定未来 2 天。"""
    channels = starhub_get_all_channels()
    if not channels:
        raise RuntimeError("无法获取频道信息")
    schedules = starhub_get_epg_schedules(channels, device_type=1)
    if not schedules and len(channels) > 0:
        log("[starhub] 设备类型 1 未获取到节目，尝试设备类型 2...")
        schedules = starhub_get_epg_schedules(channels, device_type=2)
    tv = ET.Element("tv")
    tv.set("generator-info-name", "StarHub EPG Grabber")
    tv.set("generator-info-url", "")
    tv.set("source-info-name", "StarHub")
    tv.set("source-info-url", "https://www.starhub.com")
    channels_added = 0
    for channel in channels:
        platform_id = channel.get("platform_id")
        if not platform_id:
            continue
        ch_elem = ET.SubElement(tv, "channel", id=platform_id)
        channel_name = channel.get("title", "")
        if channel_name:
            display_elem = ET.SubElement(ch_elem, "display-name")
            display_elem.text = channel_name
        pictures = channel.get("pictures", [])
        for pic in pictures:
            pic_url = pic.get("url")
            if pic_url:
                fixed_url = starhub_fix_image_url(pic_url)
                if fixed_url:
                    ET.SubElement(ch_elem, "icon", src=fixed_url)
                    break
        channels_added += 1
    programmes_added = 0
    for channel_id, prog_list in schedules.items():
        for prog in prog_list:
            try:
                start_time = prog.get("start")
                end_time = prog.get("end")
                if not start_time or not end_time:
                    continue
                xml_start = starhub_format_xmltv_time(start_time)
                xml_end = starhub_format_xmltv_time(end_time)
                if not xml_start or not xml_end:
                    continue
                prog_elem = ET.SubElement(tv, "programme")
                prog_elem.set("channel", channel_id)
                prog_elem.set("start", xml_start)
                prog_elem.set("stop", xml_end)
                title = prog.get("title", "")
                if title:
                    title_elem = ET.SubElement(prog_elem, "title")
                    title_elem.text = title
                description = prog.get("description", "")
                if description:
                    desc_elem = ET.SubElement(prog_elem, "desc")
                    desc_elem.text = description
                genres = prog.get("genres", [])
                if isinstance(genres, str):
                    genres = [genres]
                for genre in genres:
                    if genre:
                        cat_elem = ET.SubElement(prog_elem, "category")
                        cat_elem.text = genre
                prog_pics = prog.get("pictures", [])
                for pic in prog_pics:
                    pic_url = pic.get("url")
                    if pic_url:
                        fixed_url = starhub_fix_image_url(pic_url)
                        if fixed_url and "nosuchthumbnail" not in fixed_url:
                            ET.SubElement(prog_elem, "icon", src=fixed_url)
                            break
                programmes_added += 1
            except Exception as e:
                log(f"[starhub] 处理节目时出错: {e}")
                continue
    return tv, channels_added, programmes_added


# =====================================================================
# 平台 10：新唐人 NTD TV
# 来源：EPGCollector/ntdtv/（6 个文件：config.py fetcher.py epg.py
#      xmltv.py stream.py live_iptv.py）
# 逻辑原样保留：直播页内嵌 data-schedules(JSON)、频道 code/name/timezone、
# 节目按开始时间拍平去重、结束时间=下一档开始（末档默认 30 分钟，
# 单个最长 6 小时）、频道 id = 频道名（和 m3u 的 tvg-id 一致）。
# 改动：
#   - 原来用 bs4 + lxml（非标准库），这里用标准库 html.parser / 正则
#     改写等价逻辑；stream.py 的 m3u8 提取（纯正则）保留备用。
#   - 浏览器模式（playwright）不包含，只用静态模式。
#   - 不写文件，返回内存里的 XML 根节点。
# =====================================================================
NTDTV_LIVE_URL = "https://www.ntdtv.com/b5/live-tv"
NTDTV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
NTDTV_TIMEOUT = 20
NTDTV_DEFAULT_MINUTES = 30
NTDTV_MAX_HOURS = 6.0
NTDTV_T2S = str.maketrans({"東": "东", "亞": "亚", "國": "国", "臺": "台"})


class ntdtv_Fetcher:
    """负责网络请求：取页面 HTML（config.py + fetcher.py 内联，静态模式）。"""

    def __init__(self, proxy=None):
        self.session = requests.Session()
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def get_html(self, url):
        r = self.session.get(url, headers=NTDTV_HEADERS, timeout=NTDTV_TIMEOUT,
                             proxies=self.proxies)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding
        return r.text

    def get_bytes(self, url, referer=""):
        headers = dict(NTDTV_HEADERS)
        if referer:
            headers["Referer"] = referer
        r = self.session.get(url, headers=headers, timeout=30, proxies=self.proxies)
        r.raise_for_status()
        return r.content


class _NTDTVChannelTagParser(HTMLParser):
    """收集带 data-channel + data-src 属性的标签（替代 bs4 的 select）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("data-channel") and d.get("data-src"):
            self.items.append(d)


def ntdtv_name_variants(name):
    """繁体名字同时尝试简体写法。"""
    return list(dict.fromkeys([name, name.translate(NTDTV_T2S)]))


def ntdtv_resolve_src(src):
    """
    复刻站点播放脚本(bottom.js)的拼接规则:
      data-src 含 "akamaized"  -> 就是完整地址(补上协议)
      其他                      -> "//" + data-src + "/playlist.m3u8"
    """
    src = src.strip()
    if re.match(r"^https?://", src):
        link = src if (".m3u8" in src or "akamaized" in src) else src.rstrip("/") + "/playlist.m3u8"
    elif "akamaized" in src:
        link = src
    else:
        link = "//" + src.strip("/") + "/playlist.m3u8"
    if link.startswith("//"):
        link = "https:" + link
    elif not re.match(r"^https?://", link):
        link = "https://" + link
    return link


def ntdtv_parse_channel_items(html):
    """解析形如 <li data-channel="美東台" data-src="...m3u8" data-code="..." timezone="..."> 的频道项。"""
    parser = _NTDTVChannelTagParser()
    parser.feed(html)
    items = []
    for el in parser.items:
        src = (el.get("data-src") or "").strip()
        if src:
            items.append({
                "name": el["data-channel"].strip(),
                "src": src,
                "url": ntdtv_resolve_src(src),
                "code": (el.get("data-code") or "").strip(),
                "timezone": (el.get("timezone") or "").strip(),
            })
    return items


# stream.py：从播放页源码里找 m3u8 地址（纯正则，标准库）
_NTDTV_M3U8_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.m3u8[^\s\"'<>\\]*", re.I)


def ntdtv_extract_m3u8(html, base_url=""):
    """返回页面源码中出现的所有 m3u8 地址(去重保序)。"""
    text = html_mod.unescape(html).replace("\\/", "/").replace("\\u0026", "&")
    return list(dict.fromkeys(_NTDTV_M3U8_RE.findall(text)))


def ntdtv_load_schedules(html):
    """从页面里找内嵌的 data-schedules(JSON)。返回 schedules dict。"""
    m = re.search(r'data-schedules\s*=\s*"([^"]*)"', html)
    if m is None:
        m = re.search(r"data-schedules\s*=\s*'([^']*)'", html)
    if m is None:
        raise RuntimeError("页面里没找到 data-schedules, 节目单可能换了位置。")
    return json.loads(html_mod.unescape(m.group(1)))


def ntdtv_channel_map(items):
    """code -> {name, timezone}。"""
    return {i["code"]: {"name": i["name"], "timezone": i.get("timezone", "")}
            for i in items if i.get("code")}


def ntdtv_flatten(channel_data):
    """把一个频道所有日期的节目拍平, 按开始时间排序并去重(同一时间只留第一条)。"""
    lists = channel_data if isinstance(channel_data, list) else list(channel_data.values())
    seen = {}
    for lst in lists:
        for it in lst:
            try:
                ts = int(float(it["time_start"]))
            except (KeyError, TypeError, ValueError):
                continue
            title = (it.get("title") or "").strip()
            if title:
                seen.setdefault(ts, title)
    return sorted(seen.items())


def ntdtv_to_programmes(cid, items, default_minutes, max_hours):
    out = []
    for i, (ts, title) in enumerate(items):
        end_ts = items[i + 1][0] if i + 1 < len(items) else ts + default_minutes * 60
        end_ts = min(end_ts, ts + int(max_hours * 3600))
        if end_ts <= ts:
            continue
        out.append({
            "channel": cid,
            "start": datetime.fromtimestamp(ts, timezone.utc),
            "stop": datetime.fromtimestamp(end_ts, timezone.utc),
            "title": title,
        })
    return out


def ntdtv_fmt_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def grab_ntdtv(days):
    """返回 (ET根节点, 频道数, 节目数)。days 参数本平台用不上（直播页给多少是多少）。"""
    html = ntdtv_Fetcher().get_html(NTDTV_LIVE_URL)
    schedules = ntdtv_load_schedules(html)
    items = ntdtv_parse_channel_items(html)
    cmap = ntdtv_channel_map(items)
    tv = ET.Element("tv", {"generator-info-name": "emby_scraper"})
    channels, programmes = {}, []
    for code, cdata in schedules.items():
        info = cmap.get(code, {})
        name = info.get("name", code)
        items_flat = ntdtv_flatten(cdata)
        if not items_flat:
            log(f"[ntdtv] [!] {name}: 没有节目数据")
            continue
        channels[name] = name
        programmes += ntdtv_to_programmes(name, items_flat,
                                          NTDTV_DEFAULT_MINUTES, NTDTV_MAX_HOURS)
    for cid, name in channels.items():
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = name
    for p in programmes:
        pr = ET.SubElement(tv, "programme", {
            "channel": p["channel"],
            "start": ntdtv_fmt_utc(p["start"]),
            "stop": ntdtv_fmt_utc(p["stop"]),
        })
        ET.SubElement(pr, "title").text = p["title"]
        if p.get("desc"):
            ET.SubElement(pr, "desc").text = p["desc"]
    return tv, len(channels), len(programmes)


# =====================================================================
# 平台 11：open-epg.com 转换
# 来源：EPGCollector/epg_script/epg_script.py（108 行）
# 逻辑原样保留：取 open-epg.com 生成的 XML，解析 programme，时间转 UTC+3，
# 只留今天及以后的节目；若当天没有，退回最后有节目那一天。
# 改动：本地 Windows 路径不存在，直接走 URL；不写 epg-pro.json，
# 返回内存里的 XML 根节点。
# =====================================================================
EPGSCRIPT_URL = "https://www.open-epg.com/generate/xtckHrCmAy.xml"
EPGSCRIPT_TZ = timezone(timedelta(hours=3))


def epgscript_parse_time_to_utc3(t_str):
    """解析时间字符串并转换为 UTC+3 的 datetime。"""
    try:
        t_utc = datetime.strptime(t_str[:14], "%Y%m%d%H%M%S")
        t_utc = t_utc.replace(tzinfo=timezone.utc)
        return t_utc.astimezone(EPGSCRIPT_TZ)
    except (ValueError, TypeError) as e:
        log(f"[epg_script] 解析时间 {t_str} 失败: {e}")
        return None


def epgscript_fmt(dt):
    iso_format_with_colon = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return iso_format_with_colon[:-2] + ":" + iso_format_with_colon[-2:]


def grab_epg_script(days):
    """返回 (ET根节点, 频道数, 节目数)。days 参数本平台用不上。"""
    response = requests.get(EPGSCRIPT_URL, timeout=20)
    response.raise_for_status()
    xml_content = response.content.decode("utf-8")
    root = ET.fromstring(xml_content)
    all_programs = []
    for programme in root.findall("programme"):
        start_str = programme.attrib.get("start")
        stop_str = programme.attrib.get("stop")
        if not start_str or not stop_str:
            continue
        start_dt = epgscript_parse_time_to_utc3(start_str)
        stop_dt = epgscript_parse_time_to_utc3(stop_str)
        if start_dt and stop_dt:
            all_programs.append({
                "channel": programme.attrib.get("channel", ""),
                "start": start_dt,
                "stop": stop_dt,
                "title": programme.findtext("title", default="").strip(),
                "description": programme.findtext("desc", default="").strip(),
            })
    today = datetime.now(EPGSCRIPT_TZ).date()
    epg_data = [prog for prog in all_programs if prog["stop"].date() >= today]
    if not epg_data and all_programs:
        log("[epg_script] 当天没有节目，退回最后有节目那一天。")
        latest_start_date = max(p["start"].date() for p in all_programs)
        log(f"[epg_script] 最后有节目的一天: {latest_start_date}")
        for prog in all_programs:
            if prog["start"].date() == latest_start_date:
                epg_data.append(prog)
    if not epg_data:
        raise RuntimeError("没有有效的节目数据")
    tv = ET.Element("tv", {"generator-info-name": "open-epg"})
    channels = {}
    for prog in epg_data:
        channels.setdefault(prog["channel"], prog["channel"])
    for cid, name in channels.items():
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = name
    for prog in epg_data:
        pr = ET.SubElement(tv, "programme", {
            "channel": prog["channel"],
            "start": prog["start"].strftime("%Y%m%d%H%M%S +0300"),
            "stop": prog["stop"].strftime("%Y%m%d%H%M%S +0300"),
        })
        ET.SubElement(pr, "title").text = prog["title"]
        if prog.get("description"):
            ET.SubElement(pr, "desc").text = prog["description"]
    return tv, len(channels), len(epg_data)


# =====================================================================
# 主流程
# =====================================================================
PLATFORMS = [
    ("mytvsuper", "myTV SUPER（香港）", grab_mytvsuper),
    ("nowtv", "Now TV（香港）", grab_nowtv),
    ("hami", "Hami Video（台湾）", grab_hami),
    ("hoytv", "HOY TV（香港）", grab_hoytv),
    ("mewatch", "meWATCH（新加坡）", grab_mewatch),
    ("bein", "beIN", grab_bein),
    ("beinsports", "beIN SPORTS", grab_beinsports),
    ("mncvision", "MNC Vision（印尼）", grab_mncvision),
    ("starhub", "StarHub（新加坡）", grab_starhub),
    ("ntdtv", "新唐人 NTD TV", grab_ntdtv),
    ("epg_script", "open-epg.com 转换", grab_epg_script),
]


def main():
    global OUTPUT_XML_GZ, OUTPUT_CHANNELS_JSON
    ap = argparse.ArgumentParser(description="全部 EPG 抓取 + 合并（一个文件）")
    ap.add_argument("--days", type=int, default=DAYS, help="抓取天数（默认 3）")
    ap.add_argument("--only", default="", help="只跑这些平台，逗号分隔，如 mytvsuper,nowtv")
    ap.add_argument("--skip", default="", help="跳过这些平台，逗号分隔，如 mewatch")
    ap.add_argument("--out-gz", default=OUTPUT_XML_GZ, help="合并输出 epg.xml.gz")
    ap.add_argument("--out-json", default=OUTPUT_CHANNELS_JSON, help="合并输出 channels.json")
    args = ap.parse_args()

    OUTPUT_XML_GZ = args.out_gz
    OUTPUT_CHANNELS_JSON = args.out_json

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}

    log("=" * 60)
    log(f"EPG 抓取开始：{len(PLATFORMS)} 个平台，抓取天数 {args.days}")
    log("=" * 60)

    exact, reverse = load_alias_map()

    roots = []
    results = []
    t0 = time.time()
    for key, label, func in PLATFORMS:
        if not ENABLE.get(key, True):
            continue
        if only and key not in only:
            continue
        if key in skip:
            continue
        log(f"\n---- [{key}] {label} ----")
        try:
            tv, n_ch, n_prog = func(args.days)
            roots.append((tv, PLATFORM_REGION.get(key, key.upper())))
            results.append((key, label, "OK", n_ch, n_prog, ""))
            log(f"[{key}] OK：{n_ch} 个频道，{n_prog} 条节目")
        except Exception as e:
            results.append((key, label, "FAIL", 0, 0, f"{type(e).__name__}: {e}"))
            log(f"[{key}] FAIL：{type(e).__name__}: {e}（跳过，不中断）")

    log("\n" + "=" * 60)
    log("各平台抓取结果")
    log("=" * 60)
    for key, label, status, n_ch, n_prog, err in results:
        if status == "OK":
            log(f"  {key:12s} {label:20s} OK   频道 {n_ch:4d}  节目 {n_prog:6d}")
        else:
            log(f"  {key:12s} {label:20s} FAIL {err}")

    if not roots:
        log("\n[FAIL] 所有平台都失败了，没有可合并的数据。")
        return 1

    log("\n---- 合并 ----")
    tv, channel_map, n_prog = merge_roots(roots, exact, reverse)
    write_outputs(tv, channel_map, exact)

    # 孤儿节目检查：programme 的 channel 不在频道表里
    cids = set(channel_map.keys())
    orphan = sum(1 for p in tv.findall("programme") if p.get("channel") not in cids)
    # 重名检查
    from collections import Counter
    names = Counter((ch.findtext("display-name") or "") for ch in tv.findall("channel"))
    dups = {k: v for k, v in names.items() if v > 1}

    log("\n" + "=" * 60)
    log(f"完成：总频道 {len(channel_map)} 个，总节目 {n_prog} 条，"
        f"孤儿节目 {orphan} 条，重名 {len(dups)} 组，用时 {time.time() - t0:.0f} 秒")
    for target in ["民視", "華視", "中視", "翡翠台"]:
        hits = [ch.get("id") for ch in tv.findall("channel")
                if (ch.findtext("display-name") or "") == target]
        log(f"  {target}: {hits}")
    if dups:
        log(f"  重名示例: {list(dups.items())[:10]}")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
