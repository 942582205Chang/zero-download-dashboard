# -*- coding: utf-8 -*-
"""
品牌0下载预警 数据更新脚本
用法：
  - 本地: python update_data.py          （自动读取 csv/ 目录下最新的 CSV）
  - GitHub Actions 定时: python update_data.py
生成 data.json 供 index.html 读取。
"""
import pandas as pd
import json
import os
import glob
import base64
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.path.join(BASE_DIR, "csv")
OUTPUT = os.path.join(BASE_DIR, "data.json")

# 敏感字段：不再剔除，改为加密后写入 detail.json（公开仓库不明文暴露，登录后前端解码展示）
SENSITIVE_COLS = ["审核人", "审核时间", "用户名", "作者id", "提成比例", "定价", "店铺"]

# 混淆密钥（与 index.html 的 SENS_KEY 保持一致；配合登录门，防公开仓库明文 + 防未登录抓取）
SENS_KEY = "xkw-0dl-2026"


def _enc_value(v):
    """简单可逆混淆：按密钥逐字节 XOR + base64。空值原样保留。"""
    if v is None or v == "":
        return v
    data = str(v).encode("utf-8")
    key = SENS_KEY.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.b64encode(out).decode("ascii")


def find_latest_csv():
    """从 csv/ 目录找最新的 CSV 文件"""
    if not os.path.isdir(CSV_DIR):
        raise FileNotFoundError(f"未找到 csv/ 目录：{CSV_DIR}")
    files = glob.glob(os.path.join(CSV_DIR, "*.csv"))
    if not files:
        raise FileNotFoundError("csv/ 目录下没有 CSV 文件")
    return max(files, key=os.path.getmtime)


def load_csv(path):
    """读取 CSV，自动处理 gb18030 / utf-8 编码"""
    for enc in ["gb18030", "utf-8-sig", "utf-8"]:
        try:
            df = pd.read_csv(path, encoding=enc)
            return df
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法识别 CSV 编码：{path}")


def group_rate(df, zero_df, col, topn=None):
    """按列分组：零下载数 + 总资源数 + 占比"""
    zero = zero_df.groupby(col).size().reset_index(name="零下载数")
    tot = df.groupby(col).size().reset_index(name="总资源数")
    merged = zero.merge(tot, on=col, how="right").fillna(0)
    merged["零下载数"] = merged["零下载数"].astype(int)
    merged["占比"] = (merged["零下载数"] / merged["总资源数"] * 100).round(1)
    merged = merged.sort_values("零下载数", ascending=False)
    return merged.head(topn) if topn else merged


def main():
    csv_path = find_latest_csv()
    df = load_csv(csv_path)

    # 下载列转数值
    for col in ["b端下载", "c端消费", "前台下载"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 发布天数 = now - 审核时间。0下载预警口径：发布(审核通过)≥15天 仍前台0下载
    audit = pd.to_datetime(df["审核时间"], errors="coerce")
    df["发布天数"] = (datetime.now() - audit).dt.days.fillna(-1).astype(int)

    total = len(df)
    zero_df = df[(df["前台下载"] == 0) & (df["发布天数"] >= 15)].copy()
    zero_front = len(zero_df)
    eligible = int((df["发布天数"] >= 15).sum())   # 已过15天观察期的资源

    data = {
        "updateTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": os.path.basename(csv_path),
        "summary": {
            "total": int(total),
            "eligible": eligible,
            "zeroFront": int(zero_front),
            "zeroFrontRate": round(zero_front / eligible * 100, 2),
            "zeroB": int((df["b端下载"] == 0).sum()),
            "zeroC": int((df["c端消费"] == 0).sum()),
            "thresholdDays": 15
        },
        "byCourse": {
            "categories": group_rate(df, zero_df, "课程")["课程"].tolist(),
            "zero": group_rate(df, zero_df, "课程")["零下载数"].tolist(),
            "rate": group_rate(df, zero_df, "课程")["占比"].tolist()
        },
        "byGrade": {
            "categories": group_rate(df, zero_df, "年级")["年级"].tolist(),
            "zero": group_rate(df, zero_df, "年级")["零下载数"].tolist(),
            "rate": group_rate(df, zero_df, "年级")["占比"].tolist()
        },
        "byBrand": {
            "categories": group_rate(df, zero_df, "品牌", 15)["品牌"].tolist(),
            "zero": group_rate(df, zero_df, "品牌", 15)["零下载数"].tolist(),
            "rate": group_rate(df, zero_df, "品牌", 15)["占比"].tolist()
        },
        "byRegion": {
            "categories": group_rate(df, zero_df, "地区", 15)["地区"].tolist(),
            "zero": group_rate(df, zero_df, "地区", 15)["零下载数"].tolist(),
            "rate": group_rate(df, zero_df, "地区", 15)["占比"].tolist()
        },
        "byVersion": {
            "categories": group_rate(df, zero_df, "版本", 15)["版本"].tolist(),
            "zero": group_rate(df, zero_df, "版本", 15)["零下载数"].tolist(),
            "rate": group_rate(df, zero_df, "版本", 15)["占比"].tolist()
        }
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 全量明细单独输出 detail.json（敏感字段加密，登录后前端解码展示）
    detail = zero_df.fillna("").to_dict("records")
    for rec in detail:
        for col in SENSITIVE_COLS:
            if col in rec:
                rec[col] = _enc_value(rec[col])
    with open(os.path.join(BASE_DIR, "detail.json"), "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False)

    print(f"OK 已生成 data.json")
    print(f"数据源：{os.path.basename(csv_path)}")
    print(f"总资源 {total}，前台0下载 {zero_front}（{data['summary']['zeroFrontRate']}%）")


if __name__ == "__main__":
    main()
