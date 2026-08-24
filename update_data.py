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
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.path.join(BASE_DIR, "csv")
OUTPUT = os.path.join(BASE_DIR, "data.json")

# 明细中不对外暴露的敏感字段（看板公开 + CSV 仅留本地的场景下剔除）
SENSITIVE_COLS = ["审核人", "审核时间", "用户名", "作者id", "提成比例", "定价", "店铺"]


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

    data = {
        "updateTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": os.path.basename(csv_path),
        "summary": {
            "total": int(total),
            "zeroFront": int(zero_front),
            "zeroFrontRate": round(zero_front / total * 100, 2),
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
        },
        "detail": zero_df.drop(columns=[c for c in SENSITIVE_COLS if c in zero_df.columns]).fillna("").to_dict("records")
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK 已生成 data.json")
    print(f"数据源：{os.path.basename(csv_path)}")
    print(f"总资源 {total}，前台0下载 {zero_front}（{data['summary']['zeroFrontRate']}%）")


if __name__ == "__main__":
    main()
