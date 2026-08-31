#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取代表性 ETF 日K线（动态截止到今天），存 etf_data.json。"""
import subprocess, json, os, datetime

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today()
BEG, END = "20160101", TODAY.strftime("%Y%m%d")

# code -> (secid, 名称)
ETFS = {
    "510050": ("1.510050", "上证50ETF"),
    "510300": ("1.510300", "沪深300ETF"),
    "159915": ("0.159915", "创业板ETF"),
    "588000": ("1.588000", "科创50ETF"),
}

BEG_DASH = f"{BEG[:4]}-{BEG[4:6]}-{BEG[6:]}"   # 2016-01-01
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def sina_symbol(secid):
    """eastmoney secid -> 新浪 symbol：1.510050 -> sh510050, 0.159915 -> sz159915"""
    mkt, code = secid.split(".")
    return ("sh" if mkt == "1" else "sz") + code


def fetch_sina(secid):
    """备用源：新浪财经日K（东方财富在 GitHub Actions 云端 IP 上可能被整体封堵）。
    注意：新浪为不复权数据，eastmoney 为前复权；ETF 叠加图用量级对比，差异可接受。"""
    sym = sina_symbol(secid)
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=4000")
    out = subprocess.run(
        ["curl", "-s", "-m", "40", "-A", UA, "-e", "https://finance.sina.com.cn/", url],
        capture_output=True, text=True)
    txt = (out.stdout or "").strip()
    if not txt:
        raise RuntimeError("新浪空响应")
    arr = json.loads(txt)
    if not isinstance(arr, list) or not arr:
        raise RuntimeError("新浪无数据")
    recs = []
    for r in arr:
        d = str(r["day"])[:10]
        if d < BEG_DASH:
            continue
        recs.append({
            "date": d,
            "close": float(r["close"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
        })
    if not recs:
        raise RuntimeError("新浪返回数据为空(过滤后)")
    return recs


def fetch_daily(secid):
    """先走东方财富多节点，全部失败则自动切换新浪备用源。"""
    try:
        return _fetch_em(secid)
    except Exception as e:
        print(f"  ! 东方财富全部节点失败({secid}): {str(e)[:120]}")
        print("  -> 切换新浪备用源")
        return fetch_sina(secid)


def _fetch_em(secid):
    base = ("https://{host}/api/qt/stock/kline/get?secid={secid}"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3"
            "&fields2=f51,f52,f53,f54,f55&klt=101&fqt=1&beg={beg}&end={end}")
    # 每个节点只试 1 次、超时 15s：失败要快速让位给新浪备用源，避免 CI 空耗
    HOSTS = [
        "82.push2his.eastmoney.com",
        "push2his.eastmoney.com",
        "1.push2his.eastmoney.com",
        "28.push2his.eastmoney.com",
    ]
    import subprocess, time
    last = None
    for host in HOSTS:
        url = base.format(host=host, secid=secid, beg=BEG, end=END)
        try:
            out = subprocess.run(
                ["curl", "-s", "-m", "15", "-A", UA,
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
                    "close": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                })
            return recs
        except Exception as e:
            last = e
            print(f"  · {host} 失败: {str(e)[:90]}")
    raise RuntimeError(f"{secid} 东方财富全部节点失败: {str(last)[:90]}")

def main():
    result = {}
    index_map = {
        "上证指数": ["510050", "510300"],
        "创业板指": ["159915"],
        "科创50":   ["588000"],
    }
    failed = []
    for code, (secid, name) in ETFS.items():
        try:
            recs = fetch_daily(secid)
        except Exception as e:
            # ETF 数据仅用于叠加图，属可选数据：单只失败只跳过，不阻断整条流水线
            print(f"! {name}({code}) 抓取失败，已跳过: {e}")
            failed.append(code)
            continue
        result[code] = {
            "name": name, "secid": secid,
            "series": [{"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]} for r in recs]
        }
        print(f"{name}({code}): 抓取 {len(recs)} 条")
    # 只保留抓取成功的标的，避免生成器引用空数据
    index_map = {k: [c for c in v if c in result] for k, v in index_map.items()}
    index_map = {k: v for k, v in index_map.items() if v}
    out = {"etfs": result, "index_map": index_map}
    path = os.path.join(OUT_DIR, "etf_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"saved -> {path} (成功 {len(result)}/{len(ETFS)} 只"
          + (f", 跳过 {','.join(failed)}" if failed else "") + ")")

if __name__ == "__main__":
    main()
