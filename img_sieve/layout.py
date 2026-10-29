"""版面几何工具：把 OCR 观察按纵向位置聚合成行。

坐标为千分比（0~1000），左上角原点，与图片分辨率无关。
"""
from __future__ import annotations


def group_rows(obs, tol=40, anchor_first=False):
    """把 observation 按纵向位置聚合成行。

    anchor_first=True 时以行内**首个** observation 为基准判定归属，
    避免链式比较把纵向相隔很远的几行并成一行（否则相邻的金额 / 日期
    会被拼到订单号后面，产出脏单号）；默认沿用链式比较。
    """
    sorted_obs = sorted(obs, key=lambda o: o["box"]["y1"])
    rows = []
    cur = []
    for o in sorted_obs:
        if not cur:
            cur = [o]
            continue
        prev = cur[0] if anchor_first else cur[-1]
        if abs(o["box"]["y1"] - prev["box"]["y1"]) <= tol:
            cur.append(o)
        else:
            rows.append(cur)
            cur = [o]
    if cur:
        rows.append(cur)
    return rows
