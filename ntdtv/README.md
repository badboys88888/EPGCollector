# EPGCollector

在 GitHub Actions 上定时运行 Python 脚本, 采集直播页的频道地址和节目单,
生成频道列表 (m3u) 与节目单 (XMLTV), 通过 GitHub Pages 发布。

## 订阅地址 (启用 Pages 并成功运行一次之后)
- 频道列表: https://badboys88888.github.io/EPGCollector/ntdtv.m3u
- 节目单:   https://badboys88888.github.io/EPGCollector/ntdtv.xml

## 启用步骤
1. 把本仓库文件推到 `badboys88888/EPGCollector` (注意隐藏文件夹 `.github`)。
2. 仓库 Settings -> Pages -> Source 选 **GitHub Actions** (免费账号需要仓库为公开)。
3. Actions 页面 -> **Update playlist and EPG** -> Run workflow, 手动跑一次。
4. 之后每 6 小时自动更新 (UTC, 可在 `.github/workflows/epg.yml` 的 cron 里改)。

频道数或节目数异常(站点改版等)时任务会失败, 不会发布坏文件, 旧文件继续有效。

## 文件说明
| 文件 | 作用 |
|---|---|
| `live_iptv.py` | 读取直播页, 解析各台 data-src, 生成 m3u (+ 频道 json) |
| `epg.py` | 读取页面内嵌的节目单 JSON, 生成 XMLTV |
| `xmltv.py` | XMLTV 写入模块 |
| `fetcher.py` | 网络请求 (支持代理) |
| `stream.py` | m3u8 地址提取等辅助函数 |
| `config.py` | 请求头、超时等配置 |
| `.github/workflows/epg.yml` | 定时任务与 Pages 发布 |

## 说明
- m3u 里是电视台页面上公开的直播地址, 不经过任何转发, 播放时由你的设备直接去连。
- m3u 头部带了节目单地址, 支持的播放器会自动加载; 不支持的可手动填 xml 地址。
- 频道 id 是频道名(如 美東台), m3u 的 tvg-id 与 xml 的频道 id 一致。
- 节目没有结束时间, 用下一档节目的开始时间推算。

## 本地运行
```bash
pip install -r requirements.txt
python live_iptv.py "https://www.ntdtv.com/b5/live-tv" --out out/ntdtv.m3u
python epg.py "https://www.ntdtv.com/b5/live-tv" --channels-json out/ntdtv.json --out out/ntdtv.xml
```
