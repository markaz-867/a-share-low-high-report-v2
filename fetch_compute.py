#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取三指数日K线（动态截止到今天），计算逐年低点/高点主区间（±5% 容忍带）+ 当前位置实时读数。"""
import urllib.request, json, os, datetime

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
ALPHA = 0.05  # ±5% 容忍带
TODAY = datetime.date.today()
BEG, END = "20160101", TODAY.strftime("%Y%m%d")

INDICES = [
    ("上证指数", "000001", "1.000001"),
    ("创业板指", "399006", "0.399006"),
    ("科创50",   "000688", "1.000688"),
]

def fetch_daily(secid):
    base = ("https://{host}/api/qt/stock/kline/get?secid={secid}"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3"
            "&fields2=f51,f52,f53,f54,f55&klt=101&fqt=1&beg={beg}&end={end}")
    # 多个 eastmoney 节点：GitHub 云端 IP 可能被主域风控，跨节点可绕开限流
    HOSTS = [
        "82.push2his.eastmoney.com",
        "push2his.eastmoney.com",
        "1.push2his.eastmoney.com",
        "push2.eastmoney.com",
        "28.push2his.eastmoney.com",
        "7.push2his.eastmoney.com",
        "48.push2his.eastmoney.com",
        "91.push2his.eastmoney.com",
    ]
    import subprocess, time
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    last = None
    for host in HOSTS:
        url = base.format(host=host, secid=secid, beg=BEG, end=END)
        for attempt in range(3):
            try:
                out = subprocess.run(
                    ["curl", "-s", "-m", "30", "-A", UA,
                     "-e", "https://quote.eastmoney.com/", url],
                    capture_output=True, text=True, check=True)
                if not out.stdout.strip():
                    raise RuntimeError("空响应")
                j = json.loads(out.stdout)
                d = j.get("data")
                if not d or not d.get("klines"):
                    raise RuntimeError(f"{secid} 无数据: {j.get('msg')}")
                recs = []
                for line in d.get("klines", []):
                    p = line.split(",")
                    recs.append({
                        "date": p[0],
                        "open": float(p[1]),
                        "close": float(p[2]),
                        "high": float(p[3]),
                        "low": float(p[4]),
                    })
                return recs
            except Exception as e:
                last = e
                print(f"  retry {host} {secid} attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(3)
    raise RuntimeError(f"{secid} 抓取失败(所有源): {last}")

def primary_window(recs, anchor_key, extreme_val, side):
    """side='low' -> 围绕最低点向外扩，直到 close 突破 extreme_val*(1+ALPHA)
       side='high'-> 围绕最高点向外扩，直到 close 跌破 extreme_val*(1-ALPHA)"""
    n = len(recs)
    # 锚点：极值出现的第一个交易日
    anchor = next(i for i, r in enumerate(recs) if r[anchor_key] == extreme_val)
    if side == "low":
        thr = extreme_val * (1 + ALPHA)
        ok = lambda c: c <= thr
    else:
        thr = extreme_val * (1 - ALPHA)
        ok = lambda c: c >= thr
    L = anchor
    while L - 1 >= 0 and ok(recs[L - 1]["close"]):
        L -= 1
    R = anchor
    while R + 1 < n and ok(recs[R + 1]["close"]):
        R += 1
    window = recs[L:R + 1]
    band_low = min(r["low"] for r in window)
    band_high = max(r["high"] for r in window)
    return window[0]["date"], window[-1]["date"], band_low, band_high

def analyze_year(recs):
    annual_low = min(r["low"] for r in recs)
    annual_high = max(r["high"] for r in recs)
    annual_low_date = next(r["date"] for r in recs if r["low"] == annual_low)
    annual_high_date = next(r["date"] for r in recs if r["high"] == annual_high)
    lw_s, lw_e, lb_lo, lb_hi = primary_window(recs, "low", annual_low, "low")
    hw_s, hw_e, hb_lo, hb_hi = primary_window(recs, "high", annual_high, "high")
    return {
        "trading_days": len(recs),
        "annual_low": round(annual_low, 2), "annual_low_date": annual_low_date,
        "annual_high": round(annual_high, 2), "annual_high_date": annual_high_date,
        "low_window_start": lw_s, "low_window_end": lw_e,
        "low_band_low": round(lb_lo, 2), "low_band_high": round(lb_hi, 2),
        "high_window_start": hw_s, "high_window_end": hw_e,
        "high_band_low": round(hb_lo, 2), "high_band_high": round(hb_hi, 2),
    }

def live_position(recs):
    """当前位置实时读数：以「年初至今最低收盘」为低点带锚、「年初至今最高收盘」为高点带锚，
    判断今日收盘落在哪一侧、距带多少%。"""
    if not recs:
        return {}
    yr = TODAY.year
    ytd = [r for r in recs if r["date"] >= f"{yr}-01-01"]
    if not ytd:
        ytd = recs
    closes = [r["close"] for r in ytd]
    low_close = min(closes)
    high_close = max(closes)
    low_date = next(r["date"] for r in ytd if r["close"] == low_close)
    high_date = next(r["date"] for r in ytd if r["close"] == high_close)
    last = ytd[-1]
    today_close = last["close"]
    dist_low = (today_close / low_close - 1) * 100       # ≤5% 视为低点带内
    dist_high = (today_close / high_close - 1) * 100      # ≥-5% 视为高点带内
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

def main():
    result = {"meta": {"alpha": ALPHA, "beg": BEG, "end": END,
                       "gen_date": TODAY.strftime("%Y-%m-%d")}, "indices": {}, "live": {}}
    raw = {}
    for name, code, secid in INDICES:
        recs = fetch_daily(secid)
        by_year = {}
        for y in range(2016, TODAY.year + 1):
            ys, ye = f"{y}-01-01", f"{y}-12-31"
            yr = [r for r in recs if ys <= r["date"] <= ye]
            if yr:
                by_year[str(y)] = analyze_year(yr)
        result["indices"][name] = {"code": code, "secid": secid, "years": by_year}
        result["live"][name] = live_position(recs)
        raw[name] = {
            "code": code, "secid": secid,
            "series": [{"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]} for r in recs]
        }
        print(f"{name}({code}): 抓取 {len(recs)} 条, 覆盖年份 {len(by_year)} 个, 当前位置={result['live'][name]['zone']}")
    out = os.path.join(OUT_DIR, "results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    raw_out = os.path.join(OUT_DIR, "raw_data.json")
    with open(raw_out, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print("saved ->", out)
    print("saved ->", raw_out)

if __name__ == "__main__":
    main()
