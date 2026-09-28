#!/usr/bin/env python3
"""大师版渲染：由导出目录的 messages.json + report.json（+ 可选 master.json 策展文件）
生成杂志编辑风 report-master.html 与同风格 report-master.png（超长自动分页）。

用法（用项目虚拟环境运行，依赖 Pillow）：
  '<项目>/.venv/bin/python' -m wechat_local.master '<导出目录>'
  '<项目>/.venv/bin/python' -m wechat_local.master '<导出目录>' --only html
  '<项目>/.venv/bin/python' -m wechat_local.master '<导出目录>' --only png

master.json 各字段均可选，缺省时从 report.json/messages.json 自动推导；
契约详见 references/master-schema.md。
"""
import argparse
import datetime as dt
import html as html_lib
import json
import sys
from pathlib import Path

# ---------- 视觉常量（与 HTML/CSS 同源） ----------
PAPER = '#f6f2ea'; PAPER2 = '#efe9dc'; INK = '#191713'; INK_SOFT = '#4a463d'
INK_FAINT = '#8a8478'; LINE = '#d9d2c2'; RED = '#b3261e'; RED_DEEP = '#8e1a14'
GOLD = '#a97e2f'; GREEN = '#2e6b4f'; WHITE = '#ffffff'

SONGTI = '/System/Library/Fonts/Songti.ttc'
HIRAGINO = '/System/Library/Fonts/Hiragino Sans GB.ttc'

WEEKDAYS = '星期一 星期二 星期三 星期四 星期五 星期六 星期日'.split()
CN_NUM = '壹贰叁肆伍陆柒捌玖拾'


def load_json(path):
    return json.loads(Path(path).read_text())


def fmt_date(iso):
    d = dt.date.fromisoformat(iso[:10])
    return f'{d.year} 年 {d.month} 月 {d.day} 日', WEEKDAYS[d.weekday()], d


def fmt_hm(iso):
    return dt.datetime.fromisoformat(iso).strftime('%H:%M')


# ---------- master.json 归一化：补齐缺省 ----------
def build_master(data, report, master=None):
    from .core import validate_report
    validate_report(data, report)
    if master is None: master = {}
    if not isinstance(master, dict): raise ValueError('master.json 必须是对象')
    allowed = {'kicker', 'title_main', 'title_accent', 'subtitle', 'timeline'}
    unknown = set(master) - allowed
    if unknown: raise ValueError('master.json 不允许覆盖事实字段：' + ', '.join(sorted(unknown)))
    md = data['metadata']
    date_cn, weekday, _ = fmt_date(md['start'])
    m = dict(master)
    for key, default in [('kicker', '群聊纪要 · 来源可核对'), ('title_main', md['group_name']),
                         ('title_accent', ''), ('subtitle', '讨论纪要')]:
        if key in master and (not isinstance(master[key], str) or len(master[key]) > 120):
            raise ValueError(key + ' 必须是不超过 120 字符的文本')
        m.setdefault(key, default)
    if md.get('fixture'): m['title_main'] = '【虚构测试】' + m['title_main']
    m.update(date_cn=date_cn, weekday=weekday,
             window=md['start'] + ' 至 ' + md['end'],
             partial=bool(md.get('partial_period')), scope=md['scope'], group=md['group_name'],
             group_id=md['group_id'], count=md['message_count'], speakers=md['speaker_count'])
    tc = md.get('type_counts', {})
    m['stats'] = [{'num': md['message_count'], 'label': '消息总数'},
                  {'num': md['speaker_count'], 'label': '已识别发言人'},
                  {'num': tc.get('文本', 0), 'label': '文本'},
                  {'num': tc.get('图片', 0), 'label': '图片'},
                  {'num': tc.get('卡片', 0), 'label': '卡片 / 文件'}]
    m['_data'], m['_report'] = data, report
    m['_sequence'] = {item['id']: i for i, item in enumerate(data['messages'], 1)}
    def cited(item):
        refs = '、'.join(str(m['_sequence'][r]) for r in item['sources'])
        return item['text'] + ('〔来源 ' + refs + '〕' if refs else '')
    m['lede'] = cited(report['overview'])
    m['todos'] = [{'cls': '', 'tag': '待办', 't': cited(t), 'meta': [
        ['负责人', t['owner'], ''], ['时限', t['deadline'], ''], ['状态', t['status'], '']
    ]} for t in report['todos']]
    m['topics'] = [{'chip': '议题', 'title': '议题 ' + str(i), 'body': cited(t)}
                   for i, t in enumerate(report['topics'], 1)]
    for key in ('confirmed', 'unresolved', 'other'): m[key] = [cited(t) for t in report[key]]
    ids = master.get('timeline', [])
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('timeline 必须为不重复的真实消息 ID 数组')
    byid = {item['id']: item for item in data['messages']}
    if any(mid not in byid for mid in ids): raise ValueError('时间线引用不存在的消息')
    m['timeline'] = [[byid[mid]['time'], byid[mid]['sender'],
                      byid[mid]['text'] + '〔来源 ' + str(m['_sequence'][mid]) + '〕', False]
                     for mid in sorted(ids, key=lambda x: m['_sequence'][x])]
    m['_timeline_ids'] = ids
    return m


# ==================== HTML 渲染 ====================
CSS = """
:root{--paper:#f6f2ea;--paper-2:#efe9dc;--ink:#191713;--ink-soft:#4a463d;--ink-faint:#8a8478;--line:#d9d2c2;--red:#b3261e;--red-deep:#8e1a14;--gold:#a97e2f;--green:#2e6b4f}
*{margin:0;padding:0;box-sizing:border-box}html{scroll-behavior:smooth}
body{background:var(--paper);color:var(--ink);font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased;line-height:1.75}
::selection{background:var(--red);color:#fff}
#progress{position:fixed;top:0;left:0;height:3px;width:0;background:linear-gradient(90deg,var(--red),var(--gold));z-index:99;transition:width .1s linear}
.masthead{max-width:1080px;margin:0 auto;padding:56px 48px 14px;display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid var(--ink)}
.masthead .brand{font-family:"Songti SC","STSong","Noto Serif SC",serif;font-weight:700;font-size:15px;letter-spacing:.35em}
.masthead .issue{font-size:12px;color:var(--ink-faint);letter-spacing:.2em}
.cover{max-width:1080px;margin:0 auto;padding:72px 48px 64px;position:relative}
.cover .kicker{display:flex;align-items:center;gap:14px;margin-bottom:28px}
.cover .kicker .dot{width:9px;height:9px;border-radius:50%;background:var(--red)}
.cover .kicker span{font-size:13px;letter-spacing:.45em;color:var(--red);font-weight:600}
h1{font-family:"Songti SC","STSong","Noto Serif SC",serif;font-size:clamp(44px,7.2vw,84px);font-weight:900;line-height:1.14;letter-spacing:.02em}
h1 em{font-style:normal;color:var(--red)}
.cover .date-big{font-family:"Songti SC","STSong",serif;font-size:clamp(19px,2.8vw,28px);color:var(--ink-soft);margin-top:22px;letter-spacing:.12em}
.cover .scope{margin-top:26px;display:inline-block;font-size:12.5px;color:var(--ink-faint);border:1px solid var(--line);padding:8px 16px;border-radius:999px;background:rgba(255,255,255,.5)}
.cover .seal{position:absolute;right:56px;top:80px;width:104px;height:104px;border:2.5px solid var(--red);border-radius:10px;color:var(--red);display:flex;align-items:center;justify-content:center;text-align:center;font-family:"Songti SC",serif;font-weight:900;font-size:24px;line-height:1.3;transform:rotate(8deg);opacity:.85;letter-spacing:.1em;box-shadow:0 0 0 4px rgba(179,38,30,.08) inset}
@media(max-width:720px){.cover .seal{display:none}}
.wrap{max-width:1080px;margin:0 auto;padding:0 48px}
section{padding:64px 0 8px}
.sec-head{display:flex;align-items:baseline;gap:18px;margin-bottom:36px;border-bottom:2px solid var(--ink);padding-bottom:12px}
.sec-head .no{font-family:"Songti SC",serif;font-size:15px;color:var(--red);font-weight:700;letter-spacing:.1em}
.sec-head h2{font-family:"Songti SC","STSong",serif;font-size:32px;font-weight:900;letter-spacing:.06em}
.sec-head .en{margin-left:auto;font-size:11px;letter-spacing:.3em;color:var(--ink-faint)}
.stats{display:grid;grid-template-columns:repeat(__NCOL__,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin-top:8px}
.stat{background:var(--paper);padding:30px 20px 26px;text-align:center;transition:background .4s}
.stat:hover{background:var(--paper-2)}
.stat .num{font-family:"Songti SC",serif;font-size:52px;font-weight:900;line-height:1;color:var(--ink);font-variant-numeric:tabular-nums}
.stat .num small{font-size:18px;font-weight:400;color:var(--ink-faint);margin-right:2px}
.stat .lbl{font-size:12.5px;color:var(--ink-soft);margin-top:12px;letter-spacing:.15em}
.stat.hot .num{color:var(--red)}
@media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}}
.lede{font-family:"Songti SC","STSong",serif;font-size:20px;line-height:2.05;color:var(--ink);text-align:justify}
.lede::first-letter{font-size:64px;font-weight:900;color:var(--red);float:left;line-height:.95;padding:6px 12px 0 0}
.timeline{position:relative;margin-top:12px;padding-left:118px}
.timeline::before{content:"";position:absolute;left:88px;top:6px;bottom:6px;width:1px;background:var(--line)}
.tl-item{position:relative;padding:14px 0 14px 8px;opacity:0;transform:translateY(16px);transition:opacity .7s ease,transform .7s ease}
.tl-item.show{opacity:1;transform:none}
.tl-item .t{position:absolute;left:-118px;top:16px;width:78px;text-align:right;font-family:"Songti SC",serif;font-size:15px;font-weight:700;color:var(--ink-soft);font-variant-numeric:tabular-nums}
.tl-item .node{position:absolute;left:-34px;top:24px;width:9px;height:9px;border-radius:50%;background:var(--paper);border:2px solid var(--ink-faint)}
.tl-item.key .node{border-color:var(--red);background:var(--red);box-shadow:0 0 0 4px rgba(179,38,30,.15)}
.tl-item.key .t{color:var(--red)}
.tl-item .who{font-size:12px;font-weight:700;color:var(--gold);letter-spacing:.1em;margin-bottom:2px}
.tl-item .what{font-size:14.5px;color:var(--ink);line-height:1.7}
.todos{display:grid;grid-template-columns:repeat(2,1fr);gap:22px;margin-top:8px}
@media(max-width:860px){.todos{grid-template-columns:1fr}}
.todo{background:#fff;border:1px solid var(--line);border-top:3px solid var(--ink);padding:24px 26px 20px;position:relative;transition:transform .35s ease,box-shadow .35s ease,border-color .35s;opacity:0;transform:translateY(18px)}
.todo.show{opacity:1;transform:none}
.todo:hover{transform:translateY(-4px);box-shadow:0 18px 40px -18px rgba(25,23,19,.25)}
.todo.urgent{border-top-color:var(--red)}
.todo.warn{border-top-color:var(--gold)}
.todo .tag{position:absolute;top:-11px;right:18px;font-size:11px;font-weight:700;letter-spacing:.12em;padding:3px 12px;border-radius:999px;color:#fff;background:var(--ink)}
.todo.urgent .tag{background:var(--red)}
.todo.warn .tag{background:var(--gold)}
.todo h3{font-family:"Songti SC",serif;font-size:18px;font-weight:900;line-height:1.5;margin-bottom:12px}
.todo .meta{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:13px}
.todo .meta dt{color:var(--ink-faint);letter-spacing:.1em;white-space:nowrap}
.todo .meta dd{color:var(--ink-soft)}
.todo .meta dd.dl{color:var(--red-deep);font-weight:600}
.todo .meta dd.st{color:var(--green)}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media(max-width:860px){.duo{grid-template-columns:1fr}}
.panel{padding:26px 28px;border:1px solid var(--line);background:#fff}
.panel.ok{border-left:4px solid var(--green)}
.panel.open{border-left:4px solid var(--red)}
.panel h3{font-family:"Songti SC",serif;font-size:20px;font-weight:900;margin-bottom:16px;display:flex;align-items:center;gap:10px}
.panel.ok h3::before{content:"✓";color:var(--green);font-size:22px}
.panel.open h3::before{content:"?";color:var(--red);font-size:20px;border:1.5px solid var(--red);width:24px;height:24px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:15px}
.panel li{list-style:none;font-size:14px;color:var(--ink-soft);padding:9px 0 9px 18px;position:relative;border-bottom:1px dashed var(--line);line-height:1.7}
.panel li:last-child{border-bottom:none}
.panel li::before{content:"";position:absolute;left:0;top:18px;width:6px;height:6px;background:var(--gold);transform:rotate(45deg)}
.topic{border-top:1px solid var(--line);padding:26px 0;display:grid;grid-template-columns:220px 1fr;gap:28px;opacity:0;transform:translateY(14px);transition:opacity .6s ease,transform .6s ease}
.topic.show{opacity:1;transform:none}
@media(max-width:720px){.topic{grid-template-columns:1fr;gap:10px}}
.topic .side .chip{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.15em;color:#fff;background:var(--ink);padding:3px 12px;border-radius:3px;margin-bottom:10px}
.topic .side h3{font-family:"Songti SC",serif;font-size:21px;font-weight:900;line-height:1.45}
.topic .body{font-size:14.5px;color:var(--ink-soft);text-align:justify}
.topic .body b{color:var(--red-deep)}
footer{margin-top:80px;background:var(--ink);color:#cfc9ba;padding:44px 48px 52px}
footer .inner{max-width:1080px;margin:0 auto;display:flex;gap:40px;flex-wrap:wrap;justify-content:space-between;align-items:flex-start}
footer .colophon{font-family:"Songti SC",serif;font-size:18px;font-weight:900;color:#f2eee3;letter-spacing:.15em}
footer p{font-size:12px;line-height:1.9;max-width:640px;color:#9b9587}
footer .stamp{font-size:11px;letter-spacing:.25em;color:#7d7768}
.reveal{opacity:0;transform:translateY(20px);transition:opacity .7s ease,transform .7s ease}
.reveal.show{opacity:1;transform:none}
"""

# Source content is rendered as escaped static HTML; JavaScript never receives chat text.
CSS += """
.reveal,.tl-item,.todo,.topic{opacity:1;transform:none}
body{overflow-wrap:anywhere}.scope{max-width:100%;border-radius:12px!important}
.cover .seal{display:none}.masthead{gap:20px;flex-wrap:wrap}
.todo .tag{position:static;display:inline-block;margin-bottom:12px}
.topic>*{min-width:0}.stat .num{font-size:clamp(24px,5vw,52px)}
.stat{min-width:0;padding-left:10px;padding-right:10px}
.source{margin-top:12px;font:13px/1.8 sans-serif;color:var(--ink-soft)}
.source summary{cursor:pointer;color:var(--red)}.source article{padding:12px 0;border-top:1px solid var(--line)}
.source pre{white-space:pre-wrap;font:inherit;overflow-wrap:anywhere}.source code{overflow-wrap:anywhere}
.timeline{padding-left:0}.timeline::before{display:none}.tl-item .t{position:static;width:auto;text-align:left}
@media(max-width:600px){.masthead{padding:26px 20px 14px}.cover{padding:36px 20px}.wrap{padding:0 20px}
section{padding-top:36px}.sec-head{gap:12px}.sec-head h2{font-size:25px}.sec-head .en{display:none}
footer{padding:30px 20px}.todo,.panel{padding:20px}.cover .kicker span{letter-spacing:.15em}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
"""


def esc(s):
    return html_lib.escape(str(s), quote=True)


def render_html(folder, m):
    report, data = m['_report'], m['_data']
    byid = {item['id']: item for item in data['messages']}
    def refs(ids):
        if not ids: return ''
        articles = []
        for mid in ids:
            msg = byid[mid]
            text = msg['text']
            if msg['details']: text += '\n' + json.dumps(msg['details'], ensure_ascii=False)
            articles.append('<article><strong>来源 ' + str(m['_sequence'][mid]) + '</strong> · <code>' + esc(mid) +
                            '</code><p>' + esc(msg['time']) + ' · ' + esc(msg['sender']) + '</p><pre>' + esc(text) + '</pre></article>')
        return '<details class="source"><summary>核对来源（' + str(len(ids)) + ' 条）</summary>' + ''.join(articles) + '</details>'
    def section(n, title, body):
        return '<section class="wrap"><div class="sec-head"><span class="no">' + str(n) + '</span><h2>' + title + '</h2></div>' + body + '</section>'
    stats = ''.join('<div class="stat"><div class="num">' + str(x['num']) + '</div><div class="lbl">' + esc(x['label']) + '</div></div>' for x in m['stats'])
    parts = ['<div class="wrap"><div class="stats">' + stats + '</div></div>']
    parts.append(section(1, '讨论速览', '<p class="lede">' + esc(report['overview']['text']) + '</p>' + refs(report['overview']['sources'])))
    n = 2
    if m['timeline']:
        timeline = ''.join('<div class="tl-item"><div class="t">' + esc(row[0]) + '</div><div class="who">' + esc(row[1]) + '</div><div class="what">' + esc(row[2]) + '</div>' + refs([mid]) + '</div>' for row, mid in zip(m['timeline'], sorted(m['_timeline_ids'], key=lambda x:m['_sequence'][x])))
        parts.append(section(n, '消息时间线', '<div class="timeline">' + timeline + '</div>')); n += 1
    todos = ''.join('<div class="todo"><span class="tag">待办</span><h3>' + esc(t['text']) + '</h3><dl class="meta">' +
                    ''.join('<dt>' + k + '</dt><dd>' + esc(t[field]) + '</dd>' for k, field in [('负责人','owner'),('时限','deadline'),('状态','status')]) +
                    '</dl>' + refs(t['sources']) + '</div>' for t in report['todos'])
    parts.append(section(n, '重点任务清单', '<div class="todos">' + (todos or '<p>未提取到有明确依据的事项。</p>') + '</div>')); n += 1
    panels = []
    for key, title in [('confirmed','群内已确认事项'), ('unresolved','尚未解决的问题')]:
        rows = ''.join('<li>' + esc(t['text']) + refs(t['sources']) + '</li>' for t in report[key])
        panels.append('<div class="panel"><h3>' + title + '</h3><ul>' + (rows or '<li>未提取到有明确依据的事项。</li>') + '</ul></div>')
    parts.append(section(n, '确认事项与未解决问题', '<div class="duo">' + ''.join(panels) + '</div>')); n += 1
    for key, title in [('topics','议题详解'), ('other','其他信息')]:
        rows = ''.join('<div class="topic"><div class="side"><span class="chip">' + str(i) + '</span><h3>' + title + '</h3></div><div class="body">' + esc(t['text']) + refs(t['sources']) + '</div></div>' for i,t in enumerate(report[key],1))
        parts.append(section(n,title,rows or '<p>未提取到有明确依据的事项。</p>')); n += 1
    scope = m['window'] + '（Asia/Shanghai，包含起点、不包含终点）'
    note = m['scope'] + ' 群内陈述未作外部核实；未解析媒体仅标记类型。'
    if m['partial']: note += ' 请求范围尚未结束，本次不是完整时段。'
    title = esc(m['title_main']) + '<em>' + esc(m['title_accent']) + '</em>'
    doc = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'">'
           '<title>' + esc(m['group']) + ' · 聊天纪要</title><style>' + CSS.replace('__NCOL__','5') + '</style></head><body>'
           '<div class="masthead"><div class="brand">' + esc(m['group']) + '</div><div class="issue">聊天纪要 · 带来源可核对</div></div>'
           '<header class="cover"><div class="kicker"><span class="dot"></span><span>' + esc(m['kicker']) + '</span></div><h1>' + title + '<br>' + esc(m['subtitle']) + '</h1>'
           '<div class="date-big">' + esc(m['date_cn']) + '</div><p class="scope">' + esc(scope) + '</p><p>' + esc(note) + '</p></header>' + ''.join(parts) +
           '<footer><div class="inner"><div class="colophon">' + esc(m['group']) + ' · 聊天纪要</div><p>' + esc(scope + '；群 ID：' + m['group_id'] + '；' + note) + '</p></div></footer></body></html>')
    out = Path(folder) / 'report-master.html'; out.write_text(doc, encoding='utf-8')
    return out


# ==================== PNG 渲染 ====================
class Png:
    WIDTH = 1240
    MARGIN = 76
    PAGE_MAX = 12000

    def __init__(self):
        from PIL import Image, ImageDraw, ImageFont
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.ImageFont = ImageFont

        def F(path, idx, size):
            candidates=[path,HIRAGINO,'/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/STHeiti Medium.ttc']
            for candidate in dict.fromkeys(candidates):
                if not Path(candidate).is_file():continue
                for index in dict.fromkeys([idx,0]):
                    try:return ImageFont.truetype(candidate,size,index=index)
                    except OSError:pass
            raise RuntimeError('没有可用中文字体；未生成 PNG')

        self.f_title = F(SONGTI, 0, 88)       # Songti SC Black
        self.f_sub = F(SONGTI, 0, 44)
        self.f_h2 = F(SONGTI, 1, 42)          # Songti SC Bold
        self.f_h3 = F(SONGTI, 1, 30)
        self.f_num = F(SONGTI, 0, 62)
        self.f_kick = F(HIRAGINO, 2, 22)      # Hiragino W6
        self.f_body = F(HIRAGINO, 0, 25)      # Hiragino W3
        self.f_bodyb = F(HIRAGINO, 2, 25)
        self.f_small = F(HIRAGINO, 0, 21)
        self.f_smallb = F(HIRAGINO, 2, 21)
        self._scratch = ImageDraw.Draw(Image.new('RGB', (8, 8)))
        self.blocks = []   # (height, draw_fn)

    # ---- 文字测量与换行 ----
    def tw(self, text, font):
        return self._scratch.textlength(text, font=font)

    def wrap(self, text, font, width):
        lines, cur = [], ''
        for ch in text:
            if ch == '\n':
                lines.append(cur); cur = ''; continue
            if cur and self.tw(cur + ch, font) > width:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines or ['']

    def spaced_width(self, text, font, spacing):
        return sum(self.tw(c, font) + spacing for c in text) - spacing

    # ---- 积木：每个 block 返回 (height, draw(d, y)) ----
    def b_spacer(self, h):
        return h, lambda d, y: y + h

    def b_masthead(self, group):
        lines = self.wrap(group + ' · 聊天纪要', self.f_h3, self.WIDTH - 2*self.MARGIN)
        h = 55 + len(lines)*44 + 25
        def draw(d,y):
            d.rectangle((0,y,self.WIDTH,y+12),fill=RED); y+=55
            for line in lines: d.text((self.MARGIN,y),line,font=self.f_h3,fill=INK); y+=44
            d.line((self.MARGIN,y+10,self.WIDTH-self.MARGIN,y+10),fill=INK,width=2)
            return y+25
        return h,draw


    def b_cover(self,m):
        width=self.WIDTH-2*self.MARGIN
        titles=self.wrap(m['title_main']+m['title_accent'],self.f_title,width)
        subtitles=self.wrap(m['subtitle'],self.f_sub,width)
        kicks=self.wrap(m['kicker'],self.f_kick,width)
        note=m['window']+'（Asia/Shanghai，包含起点、不包含终点）\n'+m['scope']
        if m['partial']:note+=' 请求范围尚未结束，本次不是完整时段。'
        scope=self.wrap(note,self.f_small,width)
        h=50+len(kicks)*34+24+len(titles)*112+len(subtitles)*60+30+len(scope)*34+35
        def draw(d,y):
            y+=50
            for line in kicks:d.text((self.MARGIN,y),line,font=self.f_kick,fill=RED);y+=34
            y+=24
            for line in titles:d.text((self.MARGIN,y),line,font=self.f_title,fill=INK);y+=112
            for line in subtitles:d.text((self.MARGIN,y),line,font=self.f_sub,fill=RED_DEEP);y+=60
            y+=30
            for line in scope:d.text((self.MARGIN,y),line,font=self.f_small,fill=INK_SOFT);y+=34
            return y+35
        return h,draw


    def b_stats(self, stats):
        cw = self.WIDTH - 2 * self.MARGIN
        n = len(stats)
        h = 30 + 150 + 30

        def draw(d, y):
            y += 30
            bw = cw / n
            d.rectangle((self.MARGIN, y, self.MARGIN + cw, y + 150), fill=WHITE, outline=LINE, width=1)
            for i, s in enumerate(stats):
                cx = self.MARGIN + bw * i + bw / 2
                num = str(s['num']) + s.get('suffix', '')
                col = RED if s.get('hot') else INK
                d.text((cx - self.tw(num, self.f_num) / 2, y + 24), num, font=self.f_num, fill=col)
                d.text((cx - self.tw(s['label'], self.f_small) / 2, y + 100), s['label'], font=self.f_small, fill=INK_SOFT)
                if i:
                    d.line((self.MARGIN + bw * i, y + 20, self.MARGIN + bw * i, y + 130), fill=LINE, width=1)
            return y + 150 + 30
        return h, draw

    def b_sechead(self, idx, title, en):
        no = CN_NUM[idx] if idx < len(CN_NUM) else str(idx + 1)
        h = 60 + 58

        def draw(d, y):
            y += 60
            d.text((self.MARGIN, y), no, font=self.f_h3, fill=RED)
            d.text((self.MARGIN + 52, y - 8), title, font=self.f_h2, fill=INK)
            w = self.tw(en, self.f_small)
            d.text((self.WIDTH - self.MARGIN - w, y + 14), en, font=self.f_small, fill=INK_FAINT)
            y += 50
            d.line((self.MARGIN, y, self.WIDTH - self.MARGIN, y), fill=INK, width=3)
            return y + 8
        return h, draw

    def b_lede(self,text):
        lines=self.wrap(text,self.f_body,self.WIDTH-2*self.MARGIN)
        def draw(d,y):
            y+=16
            for line in lines:d.text((self.MARGIN,y),line,font=self.f_body,fill=INK);y+=44
            return y+16
        return 32+len(lines)*44,draw


    def b_timeline(self, items):
        cw = self.WIDTH - 2 * self.MARGIN - 130
        parts, h = [], 20
        for it in items:
            t, who, what = it[0], it[1], it[2]
            key = len(it) > 3 and it[3]
            lines = self.wrap(what, self.f_body, cw)
            bh = max(40, len(lines) * 40 + 34)
            parts.append((t, who, lines, key, bh))
            h += bh
        h += 20

        def draw(d, y):
            y += 20
            x_time = self.MARGIN
            x_line = self.MARGIN + 96
            y0 = y
            for t, who, lines, key, bh in parts:
                col = RED if key else INK_SOFT
                d.text((x_time + 66 - self.tw(t, self.f_smallb), y + 2), t, font=self.f_smallb, fill=col)
                d.ellipse((x_line - 6, y + 10, x_line + 6, y + 22), fill=(RED if key else PAPER), outline=(RED if key else INK_FAINT), width=3)
                d.text((x_line + 30, y), who, font=self.f_smallb, fill=GOLD)
                yy = y + 32
                for ln in lines:
                    d.text((x_line + 30, yy), ln, font=self.f_body, fill=INK)
                    yy += 40
                y += bh
            d.line((x_line, y0 + 14, x_line, y - 14), fill=LINE, width=2)
            return y + 20
        return h, draw

    def b_todo(self, todo):
        cw = self.WIDTH - 2 * self.MARGIN
        inner = cw - 2 * 34
        title_lines = self.wrap(todo['t'], self.f_h3, inner - 130)
        meta_rows = []
        for k, v, c in todo['meta']:
            vlines = self.wrap(v, self.f_small, inner - 110)
            meta_rows.append((k, vlines, c))
        body_h = 30 + len(title_lines) * 46 + 12
        for _, vlines, _ in meta_rows:
            body_h += len(vlines) * 34 + 6
        body_h += 26
        h = 14 + body_h + 22
        accent = {'urgent': RED, 'warn': GOLD}.get(todo['cls'], INK)

        def draw(d, y):
            y += 14
            x0, x1 = self.MARGIN, self.WIDTH - self.MARGIN
            d.rectangle((x0, y + 8, x1, y + 8 + body_h), fill=WHITE, outline=LINE, width=1)
            d.rectangle((x0, y + 8, x1, y + 13), fill=accent)
            # 标签
            tag = todo['tag']
            tw_ = self.tw(tag, self.f_smallb) + 28
            d.rounded_rectangle((x1 - 24 - tw_, y - 6, x1 - 24, y + 34), radius=20, fill=accent)
            d.text((x1 - 24 - tw_ + 14, y + 5), tag, font=self.f_smallb, fill=WHITE)
            yy = y + 34
            for ln in title_lines:
                d.text((x0 + 34, yy), ln, font=self.f_h3, fill=INK)
                yy += 46
            yy += 12
            for k, vlines, c in meta_rows:
                d.text((x0 + 34, yy), k, font=self.f_small, fill=INK_FAINT)
                col = RED_DEEP if c == 'dl' else GREEN if c == 'st' else INK_SOFT
                fnt = self.f_smallb if c in ('dl', 'st') else self.f_small
                for vln in vlines:
                    d.text((x0 + 34 + 96, yy), vln, font=fnt, fill=col)
                    yy += 34
                yy += 6
            return y + 8 + body_h + 22
        return h, draw

    def b_panel(self, title, items, mode):
        cw = self.WIDTH - 2 * self.MARGIN
        inner = cw - 2 * 34 - 30
        rows = [self.wrap(x, self.f_small, inner) for x in items]
        body_h = 30 + 44 + 14 + sum(len(r) * 34 + 14 for r in rows) + 12
        h = body_h + 18
        accent = GREEN if mode == 'ok' else RED

        def draw(d, y):
            x0, x1 = self.MARGIN, self.WIDTH - self.MARGIN
            d.rectangle((x0, y, x1, y + body_h), fill=WHITE, outline=LINE, width=1)
            d.rectangle((x0, y, x0 + 8, y + body_h), fill=accent)
            mark = '✓' if mode == 'ok' else '?'
            if mode == 'ok':
                d.line([(x0+34,y+44),(x0+43,y+54),(x0+62,y+30)],fill=accent,width=3)
            else:d.text((x0+34,y+26),'?',font=self.f_h3,fill=accent)
            d.text((x0 + 34 + 44, y + 28), title, font=self.f_h3, fill=INK)
            yy = y + 30 + 44 + 14
            for r in rows:
                d.polygon([(x0 + 40, yy + 8), (x0 + 48, yy + 16), (x0 + 40, yy + 24), (x0 + 32, yy + 16)], fill=GOLD)
                for ln in r:
                    d.text((x0 + 64, yy), ln, font=self.f_small, fill=INK_SOFT)
                    yy += 34
                yy += 6
                d.line((x0 + 40, yy, x1 - 30, yy), fill=LINE, width=1)
                yy += 8
            return y + body_h + 18
        return h, draw

    def _rich_runs(self,text):
        return [(ch,self.f_small,INK_SOFT) for ch in text]


    def _rich_layout(self, chars, width):
        """按宽度把富文本字符序列折行为 [[(ch,font,color)]]。"""
        lines, cur, w = [], [], 0
        for ch, f, c in chars:
            if ch == '\n':
                lines.append(cur); cur, w = [], 0
                continue
            cw_ = self.tw(ch, f)
            if cur and w + cw_ > width:
                lines.append(cur); cur, w = [], 0
            cur.append((ch, f, c)); w += cw_
        if cur:
            lines.append(cur)
        return lines or [[]]

    def b_topic(self, topic):
        cw = self.WIDTH - 2 * self.MARGIN
        chip_w = self.tw(topic['chip'], self.f_smallb) + 30
        title_lines = self.wrap(topic['title'], self.f_h3, cw - chip_w - 30)
        body_lines = self._rich_layout(self._rich_runs(topic['body']), cw)
        h = 22 + 44 + 14 + len(title_lines) * 44 + 10 + len(body_lines) * 36 + 22

        def draw(d, y):
            y += 22
            d.line((self.MARGIN, y, self.WIDTH - self.MARGIN, y), fill=LINE, width=1)
            y += 20
            d.rounded_rectangle((self.MARGIN, y, self.MARGIN + chip_w, y + 36), radius=4, fill=INK)
            d.text((self.MARGIN + 15, y + 6), topic['chip'], font=self.f_smallb, fill=WHITE)
            yy = y + 44 + 6
            for ln in title_lines:
                d.text((self.MARGIN, yy), ln, font=self.f_h3, fill=INK)
                yy += 44
            yy += 10
            for line in body_lines:
                x = self.MARGIN
                for ch, f, c in line:
                    d.text((x, yy), ch, font=f, fill=c)
                    x += self.tw(ch, f)
                yy += 36
            return yy + 22
        return h, draw

    def b_footer(self,m):
        width=self.WIDTH-2*self.MARGIN
        title=self.wrap(m['group']+' · 聊天纪要',self.f_h3,width)
        note=m['window']+'（Asia/Shanghai，包含起点、不包含终点）；'+m['scope']+' 群内陈述未作外部核实。未解析媒体仅标记类型。来源编号可在网页展开核对。'
        rows=self.wrap(note,self.f_small,width)
        h=35+len(title)*44+22+len(rows)*34+35
        def draw(d,y):
            d.rectangle((0,y,self.WIDTH,y+h),fill=INK); yy=y+35
            for line in title:d.text((self.MARGIN,yy),line,font=self.f_h3,fill=PAPER);yy+=44
            yy+=22
            for line in rows:d.text((self.MARGIN,yy),line,font=self.f_small,fill='#cfc9ba');yy+=34
            return y+h
        return h,draw

    def build_blocks(self,m):
        self.blocks=[]
        def add(block,text):
            if block[0] <= self.PAGE_MAX-180:self.blocks.append(block)
            else:
                # Huge individual cards become short continuation blocks; never crop an item.
                for start in range(0,len(text),500):self.blocks.append(self.b_lede(text[start:start+500]))
        add(self.b_masthead(m['group']),m['group'])
        add(self.b_cover(m),'\n'.join([m['kicker'],m['title_main']+m['title_accent'],m['subtitle'],m['window'],m['scope']]))
        add(self.b_stats(m['stats']),' '.join(str(x['num'])+' '+x['label'] for x in m['stats']))
        sec=0
        def heading(title,en):
            nonlocal sec
            self.blocks.append(self.b_sechead(sec,title,en));sec+=1
        heading('讨论速览','OVERVIEW');add(self.b_lede(m['lede']),m['lede'])
        if m['timeline']:
            heading('消息时间线','TIMELINE')
            for row in m['timeline']:
                text=' · '.join(row[:3]);add(self.b_lede(text),text)
        heading('重点任务清单','TO-DO')
        for todo in m['todos']:
            text=todo['t']+'\n'+'\n'.join(k+'：'+v for k,v,_ in todo['meta'])
            add(self.b_todo(todo),text)
        if not m['todos']:add(self.b_lede('未提取到有明确依据的事项。'),'')
        heading('确认事项与未解决问题','STATUS')
        for key,title,mode in [('confirmed','群内已确认事项','ok'),('unresolved','尚未解决的问题','open')]:
            for text in m[key] or ['未提取到有明确依据的事项。']:add(self.b_panel(title,[text],mode),title+'\n'+text)
        heading('议题详解','TOPICS')
        for topic in m['topics']:add(self.b_topic(topic),topic['title']+'\n'+topic['body'])
        if not m['topics']:add(self.b_lede('未提取到有明确依据的事项。'),'')
        heading('其他信息','OTHER')
        for text in m['other'] or ['未提取到有明确依据的事项。']:add(self.b_lede(text),text)
        add(self.b_footer(m),m['window']+'\n'+m['scope'])
        return self.blocks


    def render(self,folder,m):
        self.build_blocks(m)
        pages=[];cur=[];height=0
        limit=self.PAGE_MAX-150
        # Measure callbacks individually; their returned height, not the estimate, controls packing.
        for estimate,draw in self.blocks:
            canvas=self.Image.new('RGB',(self.WIDTH,int(estimate)+512),PAPER)
            end=int(draw(self.ImageDraw.Draw(canvas),0))+12
            if end>canvas.height:raise ValueError('版式高度超出测量范围，未裁切输出')
            if end>limit:raise ValueError('单块内容超出分页上限，未裁切输出')
            block=canvas.crop((0,0,self.WIDTH,end))
            if cur and height+end>limit:pages.append((cur,height));cur=[];height=0
            cur.append(block);height+=end
        if cur:pages.append((cur,height))
        names=[]
        for i,(blocks,height) in enumerate(pages,1):
            top=90 if len(pages)>1 else 0
            image=self.Image.new('RGB',(self.WIDTH,height+top+30),PAPER)
            if top:self.ImageDraw.Draw(image).text((self.MARGIN,25),f'第 {i}/{len(pages)} 图 · 内容较长，按高度分图，完整内容顺序衔接',font=self.f_small,fill=RED_DEEP)
            y=top
            for block in blocks:image.paste(block,(0,y));y+=block.height
            name='report-master.png' if i==1 else f'report-master-{i:02d}.png'
            image.save(Path(folder)/name);names.append(Path(folder)/name)
        return names


def render(folder, master_path=None, only=None):
    from .core import dump
    folder=Path(folder).resolve()
    data=load_json(folder/'messages.json'); report=load_json(folder/'report.json')
    mpath=Path(master_path) if master_path else folder/'master.json'
    if master_path and not mpath.is_file(): raise ValueError('指定的 master.json 不存在')
    master=load_json(mpath) if mpath.is_file() else {}
    m=build_master(data,report,master)
    made=[]
    if only in (None,'html'):made.append(render_html(folder,m))
    if only in (None,'png'):made.extend(Png().render(folder,m))
    result={'ok':True,'files':[str(p) for p in made],'curated':bool(master),
            'reviewed_message_count':len(report['reviewed_message_ids']),
            'references_exist':True,'semantic_review':'由总结者核对；程序仅验证引用存在和数据一致性',
            'fixture':data['metadata']['fixture']}
    dump(folder/'master-validation.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder');parser.add_argument('--master');parser.add_argument('--only',choices=['html','png'])
    args=parser.parse_args()
    print(json.dumps(render(args.folder,args.master,args.only),ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False),file=sys.stderr);raise SystemExit(2)
