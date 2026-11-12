#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股高低点报表 · 统一推送模块（本地 17:00 与 GitHub Actions 17:30 共用同一份）

职责：
  1. 只从 results.json 的 live 取数，生成「唯一一份」摘要文本（同口径、同模板）
  2. 按状态文件判断是否有新交易日；无新数据则静默（输出 SILENT）
  3. 发到飞书或企业微信群机器人（按 Webhook URL 自动识别）
  4. 写出 latest_summary.txt：电脑端文字 / 手机消息正文 / 线上产物三方共用

Webhook 取值优先级：环境变量 REPORT_WEBHOOK > 项目根 .notify_webhook 文件。
未配置时不会阻断报表生成，只打印 WEBHOOK_NOT_SET，仍写出摘要与状态。
飞书若开启「签名校验」，用环境变量 FEISHU_SECRET 传入密钥。

用法：
  python notify.py                                   # dry-run，只打印摘要
  python notify.py --send --source "本地 17:00"      # 本地自动化
  python notify.py --send --source "云端 17:30" \
      --state-file .cloud_push_state.json            # GitHub Actions
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES_URL = "https://markaz-867.github.io/a-share-low-high-report-v2/"
EXPECTED = ("上证指数", "创业板指", "科创50", "IC主力连续", "IM主力连续")
REQUIRED_FIELDS = ("last_date", "last_close", "dist_low_pct", "dist_high_pct", "zone")
DISCLAIMER = "以上为报表口径与数据整理，不构成投资建议；市场有风险，决策需谨慎。"
CALIBER = (
    "口径：「较年内最低」= 今日收盘相对年初至今最低收盘的涨幅，"
    "5% 只是判定阈值；收盘价、YTD 口径，前瞻代理非事后底。"
)
TIMEOUT = 20


def load_results(results_path: Path) -> dict:
    with results_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    live = data.get("live")
    if not isinstance(live, dict):
        raise ValueError("results.json 缺少 live 字段")
    missing = [n for n in EXPECTED if n not in live]
    if missing:
        raise ValueError("results.json live 缺少品种：" + "、".join(missing))
    for name in EXPECTED:
        absent = [k for k in REQUIRED_FIELDS if k not in live[name]]
        if absent:
            raise ValueError(f"{name} 缺少字段：{', '.join(absent)}")
    return data


def pct(value) -> str:
    return f"{float(value):+.2f}%"


def build_summary(data: dict) -> str:
    """唯一的摘要模板。两边都调用它，正文不含来源标签，便于逐字比对。"""
    live = data["live"]
    gen_date = (data.get("meta") or {}).get("gen_date", "未知")
    dates = sorted({str(live[n]["last_date"]) for n in EXPECTED})
    date_desc = dates[0] if len(dates) == 1 else f"{dates[0]} ~ {dates[-1]}"
    lines = [f"【A股高低点报表】生成 {gen_date} · 数据截止 {date_desc}", ""]
    for name in EXPECTED:
        rec = live[name]
        lines.append(
            f"{name}｜收盘 {float(rec['last_close']):,.2f}"
            f"｜较年内最低 {pct(rec['dist_low_pct'])}"
            f"｜较年内最高 {pct(rec['dist_high_pct'])}"
            f"｜{rec['zone']}｜{rec['last_date']}"
        )
    lines += ["", CALIBER, f"完整报表：{PAGES_URL}", DISCLAIMER]
    return "\n".join(lines)


def state_key(data: dict) -> dict:
    return {n: str(data["live"][n]["last_date"]) for n in EXPECTED}


def is_silent(state_path: Path, key: dict) -> bool:
    if not state_path.exists():
        return False
    try:
        prev = json.loads(state_path.read_text(encoding="utf-8")).get("last_dates", {})
    except (json.JSONDecodeError, OSError, AttributeError):
        return False
    return all(prev.get(n) == v for n, v in key.items())


def write_state(state_path: Path, key: dict, source: str) -> None:
    state_path.write_text(
        json.dumps(
            {
                "last_dates": key,
                "pushed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "channel": source,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def get_webhook() -> str:
    url = os.environ.get("REPORT_WEBHOOK", "").strip()
    if not url:
        secret_file = ROOT / ".notify_webhook"
        if secret_file.exists():
            url = secret_file.read_text(encoding="utf-8").strip()
    return url


def feishu_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_payload(url: str, content: str) -> bytes:
    if "qyapi.weixin.qq.com" in url:
        body = {"msgtype": "text", "text": {"content": content}}
    elif "open.feishu.cn" in url or "open.larksuite.com" in url:
        body = {"msg_type": "text", "content": {"text": content}}
        secret = os.environ.get("FEISHU_SECRET", "").strip()
        if secret:
            ts = int(time.time())
            body["timestamp"] = str(ts)
            body["sign"] = feishu_sign(secret, ts)
    else:
        raise ValueError("无法识别的 Webhook 地址（仅支持企业微信 / 飞书自定义机器人）")
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def post(url: str, content: str) -> None:
    payload = build_payload(url, content)
    last_error = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            compact = raw.replace(" ", "").replace("\n", "")
            ok = (
                '"errcode":0' in compact           # 企业微信
                or '"StatusCode":0' in compact      # 飞书 v2
                or '"code":0' in compact           # 飞书新版
                or "success" in compact.lower()
            )
            if not ok:
                raise RuntimeError(f"Webhook 返回异常：{raw[:300]}")
            print(f"WEBHOOK_OK {raw[:200]}")
            return
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            last_error = exc
            print(f"[warn] 第 {attempt} 次发送失败：{exc}", file=sys.stderr)
            if attempt < 3:
                time.sleep(3)
    raise RuntimeError(f"Webhook 连续 3 次失败：{last_error}")


def resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    ap = argparse.ArgumentParser(description="A股高低点报表统一推送")
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--state-file", default=".push_state.json")
    ap.add_argument("--write-summary", default="latest_summary.txt")
    ap.add_argument("--source", default="本地 17:00", help="只出现在手机消息首行，不进摘要正文")
    ap.add_argument("--send", action="store_true", help="真实发送；不加则 dry-run")
    args = ap.parse_args()

    try:
        data = load_results(resolve(args.results))
        summary = build_summary(data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"NOTIFY_FAIL 读取/校验 results.json 失败：{exc}", file=sys.stderr)
        return 1

    if not args.send:
        print("---- DRY RUN 摘要 ----")
        print(summary)
        print("---- END DRY RUN ----")
        return 0

    state_path = resolve(args.state_file)
    key = state_key(data)
    if is_silent(state_path, key):
        print("SILENT 五品种数据截止日与上次相同，无新交易日，本次不推送")
        return 0

    summary_path = resolve(args.write_summary)
    summary_path.write_text(summary + "\n", encoding="utf-8")

    webhook = get_webhook()
    if not webhook:
        print("WEBHOOK_NOT_SET 未配置 REPORT_WEBHOOK / .notify_webhook；摘要已写出，未发送手机消息")
        write_state(state_path, key, args.source)
        return 0

    content = f"【来源：{args.source}】\n" + summary
    try:
        post(webhook, content)
    except (ValueError, RuntimeError) as exc:
        print(f"NOTIFY_FAIL {exc}", file=sys.stderr)
        return 1

    write_state(state_path, key, args.source)
    print(f"NOTIFY_OK 已推送（{args.source}），摘要写入 {summary_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
