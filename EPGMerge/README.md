# EPGMerge

合并多个 xml / xml.gz EPG,输出 `output/merged.xml`、`merged.xml.gz`、`epg.json`、`channels.json`,并自动补全频道图标。

## 文件
- `merge_epg.py`  主脚本(只用标准库)
- `sources.txt`   源列表,一行一个
- `icons.json`    手动图标表 {频道ID或名称: 图标URL},优先级最高
- `aliases.json`  ID 别名表 {旧ID: 统一ID},用于把不同源的同一频道合并

## 本地运行
python EPGMerge/merge_epg.py --prune-empty --keep-days 1

## 图标补全顺序
1. icons.json(ID 或名称匹配,忽略大小写/空格/符号)
2. 其它源里同名频道自带的 icon
3. 仍缺的会列在 output/missing_icons.txt,补进 icons.json 即可
