# -*- coding: utf-8 -*-
"""画出 common.py 与三个 EDA notebook 章节的依赖图。输出 eda/common_dependencies.png"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mp
from matplotlib.patches import FancyArrowPatch

mpl = matplotlib
mpl.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'DejaVu Sans']
mpl.rcParams['axes.unicode_minus'] = False

COL = {
    'load':  ('#2a78d6', '#eaf2fc'),   # 加载
    'feat':  ('#eb6834', '#fdf0e8'),   # 特征
    'traj':  ('#1baf7a', '#e8f7f0'),   # 轨迹/事件
}
NB_COL = {'00': '#2a78d6', '01': '#eb6834', '02': '#1baf7a'}

fig, ax = plt.subplots(figsize=(16.5, 9.8))
ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
ax.set_title('eda/common.py → 三个 EDA notebook 的依赖图（改自 2026-09-03 版，对数据库只读）',
             fontsize=14, pad=14)

# ---------------- 左：common.py ----------------
ax.add_patch(mp.FancyBboxPatch((0.015, 0.03), 0.30, 0.94,
                               boxstyle='round,pad=0.006', fc='#f4f5f2',
                               ec='#8e8d88', lw=1.4))
ax.text(0.03, 0.965, 'eda/common.py', fontsize=12, weight='bold', va='top')

def func_row(y, name, group, note='', colorize=True):
    c, bg = COL[group]
    ax.add_patch(mp.FancyBboxPatch((0.028, y - 0.021), 0.272, 0.042,
                                   boxstyle='round,pad=0.003', fc=bg, ec=c, lw=1.0))
    ax.text(0.035, y, name, fontsize=9, va='center', weight='bold' if colorize else 'normal')
    if note:
        ax.text(0.30, y, note, fontsize=7.5, va='center', ha='right', color='#66655f')
    return y

def group_header(y, text, c):
    ax.text(0.03, y, text, fontsize=9.5, weight='bold', color=c, va='center')

# A 加载
group_header(0.925, 'A · 只读加载（sqlite mode=ro）', COL['load'][0])
y_load = func_row(0.878, 'load_flight_prices',   'load')
y_alert = func_row(0.828, 'load_price_alerts',   'load')
y_log = func_row(0.778, 'load_monitor_log',      'load')

# B 特征
group_header(0.715, 'B · 特征工程', COL['feat'][0])
y_feat = func_row(0.668, 'add_features', 'feat', note='+lead/dep_slot/bj_airport/…')

# C 轨迹/事件
group_header(0.592, 'C · 轨迹与事件聚合', COL['traj'][0])
y_ts   = func_row(0.545, 'trajectory_summary', 'traj', note='含删失(censored)判定')
y_ev   = func_row(0.495, 'change_events',      'traj', note='涨跌/幅度/间隔')
y_grid = func_row(0.445, 'lead_grid',          'traj', note='按整数lead展开')
y_floor= func_row(0.395, 'floor_by_lead',      'traj', note='(航向,起飞日,lead桶)最低')
y_trans= func_row(0.345, 'lead_transitions',   'traj', note='同轨迹多lead对照')
y_bin  = func_row(0.295, 'lead_bin / LEAD_BIN_ORDER', 'traj')
y_ac   = func_row(0.245, 'ac_family',          'traj')
y_spare= func_row(0.195, 'prices_at_leads',    'traj', note='(当前未使用·备用)')

ax.text(0.03, 0.115, '所有章节的起点都是\nload_flight_prices + add_features 得到 df；\n下图只画"中间派生对象"的流向。',
        fontsize=8, color='#52514e', va='top')

# ---------------- 右：三个 notebook 列 ----------------
def notebook_col(x0, title, sections):
    """sections: [(短名, 描述, [函数key], y)]"""
    y_top = 0.94
    h = 0.052
    xw = 0.195
    c = NB_COL[title[:2]]
    ax.add_patch(mp.FancyBboxPatch((x0, y_top - 0.045), xw, 0.045,
                                   boxstyle='round,pad=0.003', fc=c, ec=c, lw=1.2))
    ax.text(x0 + xw / 2, y_top - 0.0225, title, fontsize=11, weight='bold',
            color='white', ha='center', va='center')
    ys = {}
    for i, (sid, desc, _) in enumerate(sections):
        y = y_top - 0.052 - i * 0.058
        ys[sid] = y
        box = mp.FancyBboxPatch((x0, y - h / 2), xw, h,
                                boxstyle='round,pad=0.003', fc='white', ec=c, lw=1.0)
        ax.add_patch(box)
        ax.text(x0 + 0.007, y + 0.010, sid, fontsize=8.2, weight='bold',
                va='center', color=c, clip_path=box, clip_on=True)
        ax.text(x0 + 0.007, y - 0.0115, desc, fontsize=7.2, va='center',
                color='#3c3b38', clip_path=box, clip_on=True)
    return ys

# 00 章节
s00 = [('§0 准备', 'df', []), ('§1 规模/时间', 'df', []), ('§2 航向/航班/航司', 'df', []),
       ('§3 断档审计', 'df', []), ('§4 字段完整性', 'df', []), ('§4b 异常价格审计', 'df', []),
       ('§5 采样深度', 'ts', ['ts']), ('§6 价格概览', 'df', []), ('§7 变价/告警/日志', 'ev+alerts+logs', ['ev', 'alert', 'log'])]
# 01 章节
s01 = [('§0 准备/网格', 'grid+floor+trans', []), ('§1 全网最低随lead', 'floor', ['floor']),
       ('§2 等待vs现在买', 'trans', ['trans']), ('§3 该不该买(分位)', 'floor+df+lead_bin', ['floor', 'bin']),
       ('§4 最低价出现lead', 'grid', ['grid']), ('§5 往返不对称', 'df+grid', ['grid'])]
# 02 章节
s02 = [('§0 准备', '全量加载', []), ('§1 日历效应', 'floor', ['floor']),
       ('§2 时刻/机场/机型', 'df+ac_family', ['ac']), ('§3 航司分层', 'df', []),
       ('§4 波动', 'ev+lead_bin', ['ev', 'bin']), ('§5/5b 告警噪音与告警表', 'ev+alerts', ['ev', 'alert']),
       ('§6 疑似售罄', 'ts', ['ts'])]

ys00 = notebook_col(0.355, '00 数据总览', s00)
ys01 = notebook_col(0.575, '01 购买时机', s01)
ys02 = notebook_col(0.795, '02 结构·波动·售罄', s02)

# ---------------- 箭头 ----------------
def arrow(y_from, x_to, sid, color, rad=0.12):
    ax.add_patch(FancyArrowPatch(
        (0.302, y_from), (x_to - 0.006, ys(sid) + 0.008),
        connectionstyle=f'arc3,rad={rad}', arrowstyle='-|>', mutation_scale=8,
        color=color, lw=1.0, alpha=0.85, shrinkA=2, shrinkB=2))

def ys(sid):
    for d in (ys00, ys01, ys02):
        if sid in d:
            return d[sid]
    raise KeyError(sid)

X00, X01, X02 = 0.355, 0.575, 0.795
arrow(y_load, X00, '§0 准备', COL['load'][0])
arrow(y_load, X01, '§0 准备/网格', COL['load'][0])
arrow(y_load, X02, '§0 准备', COL['load'][0])
arrow(y_feat, X00, '§0 准备', COL['feat'][0], rad=0.18)
arrow(y_feat, X01, '§0 准备/网格', COL['feat'][0], rad=0.18)
arrow(y_feat, X02, '§0 准备', COL['feat'][0], rad=0.18)
arrow(y_alert, X00, '§7 变价/告警/日志', COL['load'][0])
arrow(y_alert, X02, '§5/5b 告警噪音与告警表', COL['load'][0])
arrow(y_log, X00, '§7 变价/告警/日志', COL['load'][0], rad=0.10)
arrow(y_ts, X00, '§5 采样深度', COL['traj'][0])
arrow(y_ts, X02, '§6 疑似售罄', COL['traj'][0])
arrow(y_ev, X00, '§7 变价/告警/日志', COL['traj'][0])
arrow(y_ev, X02, '§4 波动', COL['traj'][0])
arrow(y_ev, X02, '§5/5b 告警噪音与告警表', COL['traj'][0], rad=-0.14)
arrow(y_grid, X01, '§1 全网最低随lead', COL['traj'][0], rad=-0.10)
arrow(y_grid, X01, '§4 最低价出现lead', COL['traj'][0])
arrow(y_grid, X01, '§5 往返不对称', COL['traj'][0], rad=0.10)
arrow(y_grid, X02, '§1 日历效应', COL['traj'][0], rad=-0.10)
arrow(y_floor, X01, '§1 全网最低随lead', COL['traj'][0], rad=0.14)
arrow(y_floor, X01, '§3 该不该买(分位)', COL['traj'][0])
arrow(y_floor, X02, '§1 日历效应', COL['traj'][0], rad=0.14)
arrow(y_trans, X01, '§2 等待vs现在买', COL['traj'][0])
arrow(y_bin, X01, '§3 该不该买(分位)', COL['traj'][0], rad=0.12)
arrow(y_bin, X02, '§4 波动', COL['traj'][0], rad=0.12)
arrow(y_ac, X02, '§2 时刻/机场/机型', COL['traj'][0])

ax.text(0.02, 0.005, '图例：蓝=加载 · 橙=特征 · 绿=轨迹/事件   |   灰色框内含"基于 df 或本节自算"的章节不再连箭头',
        fontsize=8, color='#52514e')

fig.savefig(r'D:\Flight-Monitor\eda\common_dependencies.png', dpi=150,
            bbox_inches='tight', facecolor='white')
print('saved -> eda/common_dependencies.png')
