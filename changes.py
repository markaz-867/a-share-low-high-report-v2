#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重点变化检测 · 本地 17:00 / 云端 17:30 / C2C 17:05 三路共用同一份口径。

反编造铁律：所有变化数值均由 raw_data.json 的日 K 序列经 _position_at()
重算得到。_position_at() 与 fetch_compute.live_position() 算法完全一致，
仅把年份由 TODAY.year 改为调用方传入（避免跨年回放错位）。

检测三类变化：
  1. 日涨跌幅 |Δ| >= 2%（阈值可调）
  2. 区间切换（zone 字段变化）
  3. 创年内新低 / 新高（ytd_low_date / ytd_high_date 被刷新）

任何一路检测失败都不影响主摘要生成，但调用方应在告警里点名说明。
"""
from __future__ import annotations

from typing import Any

ALPHA = 0.05
CHANGE_PCT = 2.0
NAMES = ("上证指数", "创业板指", "科创50", "IC主力连续", "IM主力连续")


def _position_at(recs: list[dict], year: str) -> dict[str, Any]:
    """与 fetch_compute.live_position() 同算法，年份由参数指定。"""
    if not recs:
        return {}
    ytd = [r for r in recs if r["date"] >= f"{year}-01-01"]
    if not ytd:
        ytd = recs
    closes = [r["close"] for r in ytd]
    low_close = min(closes)
    high_close = max(closes)
    low_date = next(r["date"] for r in ytd if r["close"] == low_close)
    high_date = next(r["date"] for r in ytd if r["close"] == high_close)
    last = ytd[-1]
    today_close = last["close"]
    dist_low = (today_close / low_close - 1) * 100
    dist_high = (today_close / high_close - 1) * 100
    if dist_low <= ALPHA * 100:
        zone = "低点带内"
    elif dist_high >= -ALPHA * 100:
        zone = "高点带内"
    else:
        zone = "中间区"
    return {
        "last_date": last["date"], "last_close": round(today_close, 2),
        "ytd_low_close": round(low_close, 2), "ytd_low_date": low_date,
        "ytd_high_close": round(high_close, 2), "ytd_high_date": high_date,
        "dist_low_pct": round(dist_low, 2), "dist_high_pct": round(dist_high, 2),
        "low_anchor": round(low_close * (1 + ALPHA), 2),
        "high_anchor": round(high_close * (1 - ALPHA), 2),
        "zone": zone,
    }


def detect_changes(data: dict, raw: dict, threshold: float = CHANGE_PCT) -> list[dict]:
    """比对当天 live 与上一交易日重算值，返回显著变化列表。"""
    out: list[dict] = []
    live = data.get("live", {})
    for name in NAMES:
        cur = live.get(name)
        if not cur:
            continue
        series = (raw.get(name) or {}).get("series") or []
        last_date = str(cur.get("last_date", ""))
        if not last_date or len(series) < 2:
            continue
        idx = next((i for i, r in enumerate(series) if r["date"] == last_date), -1)
        if idx < 0:
            idx = next((i for i in range(len(series) - 1, -1, -1)
                        if series[i]["date"] <= last_date), -1)
        if idx < 1:
            continue
        prev = _position_at(series[:idx], last_date[:4])
        if not prev:
            continue
        cur_close = float(cur.get("last_close", 0))
        prev_close = float(prev["last_close"])
        chg = (cur_close / prev_close - 1) * 100 if prev_close else 0.0

        if abs(chg) >= threshold:
            verb = "涨" if chg > 0 else "跌"
            out.append({
                "name": name, "kind": "日涨跌幅", "dir": verb,
                "text": f"{name} 日{verb} {abs(chg):.2f}%"
                        f"（收盘 {cur_close:,.2f}，前收 {prev_close:,.2f}）",
            })
        if prev.get("zone") != cur.get("zone"):
            out.append({
                "name": name, "kind": "区间切换",
                "text": f"{name} {prev.get('zone', '—')} → {cur.get('zone', '—')}",
            })
        if cur.get("ytd_low_date") != prev.get("ytd_low_date"):
            out.append({
                "name": name, "kind": "创年内新低",
                "text": f"{name} 创年内新低 {float(cur.get('ytd_low_close', 0)):,.2f}"
                        f"（前低 {float(prev.get('ytd_low_close', 0)):,.2f} / "
                        f"{prev.get('ytd_low_date', '—')}）",
            })
        if cur.get("ytd_high_date") != prev.get("ytd_high_date"):
            out.append({
                "name": name, "kind": "创年内新高",
                "text": f"{name} 创年内新高 {float(cur.get('ytd_high_close', 0)):,.2f}"
                        f"（前高 {float(prev.get('ytd_high_close', 0)):,.2f} / "
                        f"{prev.get('ytd_high_date', '—')}）",
            })
    return out


def format_changes(changes: list[dict], threshold: float = CHANGE_PCT) -> str:
    """渲染摘要顶部「重点变化」区块；无变化也明确说明，不留空白。"""
    if not changes:
        return (f"重点变化：今日五品种无显著变化"
                f"（日涨跌幅 < {threshold:.0f}%、区间未切换、未创年内极值）")
    return "\n".join([f"重点变化（{len(changes)} 项）："] +
                     [f"· {c['text']}" for c in changes])
