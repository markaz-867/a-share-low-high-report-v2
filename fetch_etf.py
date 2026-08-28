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

def fetch_daily(secid):
    base = ("https://{host}/api/qt/stock/kline/get?secid={secid}"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3"
            "&fields2=f51,f52,f53,f54,f55&klt=101&fqt=1&beg={beg}&end={end}")
    HOSTS = [
        "82.push2his.eastmoney.com",
        "push2his.eastmoney.com",
        "1.push2his.eastmoney.com",
        "push2.eastmoney.com",
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

def main():
    result = {}
    index_map = {
        "上证指数": ["510050", "510300"],
        "创业板指": ["159915"],
        "科创50":   ["588000"],
    }
    for code, (secid, name) in ETFS.items():
        recs = fetch_daily(secid)
        result[code] = {
            "name": name, "secid": secid,
            "series": [{"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]} for r in recs]
        }
        print(f"{name}({code}): 抓取 {len(recs)} 条")
    out = {"etfs": result, "index_map": index_map}
    path = os.path.join(OUT_DIR, "etf_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved ->", path)

if __name__ == "__main__":
    main()
