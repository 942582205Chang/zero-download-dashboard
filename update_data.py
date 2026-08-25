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
import hashlib
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.path.join(BASE_DIR, "csv")
OUTPUT = os.path.join(BASE_DIR, "data.json")

# 敏感字段：整体加密覆盖（不再逐条单独加密）
SENSITIVE_COLS = ["审核人", "审核时间", "用户名", "作者id", "提成比例", "定价", "店铺"]

# 加密密钥 = SHA-256(登录密码)，与前端 deriveKey 一致；密码不写进公开代码
# 来源优先级：环境变量 DASH_PASSWORD → dashboard-auto-update/config.json 的 password（本地字段，不进公开仓库）
def _load_password():
    pwd = (os.environ.get("DASH_PASSWORD") or "").strip()
    if pwd:
        return pwd
    try:
        cfg = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "dashboard-auto-update", "config.json"))
        with open(cfg, encoding="utf-8") as f:
            pwd = (json.load(f).get("password") or "").strip()
        if pwd:
            return pwd
    except Exception:
        pass
    raise RuntimeError("缺少加密密码：请设置环境变量 DASH_PASSWORD 或 dashboard-auto-update/config.json 的 password")

SENS_KEY = hashlib.sha256(_load_password().encode("utf-8")).digest()   # 32 字节派生密钥


def _enc_value(v):
    """简单可逆混淆：按密钥逐字节 XOR + base64。空值原样保留。"""
    if v is None or v == "":
        return v
    data = str(v).encode("utf-8")
    key = SENS_KEY
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

    # 发布天数 = now - 审核时间。0下载预警口径：发布(审核通过)超过15天(严格>15) 仍前台0下载
    audit = pd.to_datetime(df["审核时间"], errors="coerce")
    df["发布天数"] = (datetime.now() - audit).dt.days.fillna(-1).astype(int)

    total = len(df)
    zero_df = df[(df["前台下载"] == 0) & (df["发布天数"] > 15)].copy()
    zero_front = len(zero_df)
    eligible = int((df["发布天数"] > 15).sum())   # 已超过15天观察期的资源
    over15 = df[df["发布天数"] > 15]              # 发布超15天的资源（B/C 口径基数，与备注一致）

    # 发布天数分档统计表（页面顶部表格用，2026-08-25 新增）
    DAY_GROUPS = [
        ("小初高整体", pd.Series(True, index=df.index)),
        ("不超过 15 天", df["发布天数"] <= 15),
        ("已超过 15 天", df["发布天数"] > 15),
        ("16 到 30 天",  (df["发布天数"] >= 16) & (df["发布天数"] <= 30)),
        ("31 到 60 天",  (df["发布天数"] >= 31) & (df["发布天数"] <= 60)),
        ("61 到 90 天",  (df["发布天数"] >= 61) & (df["发布天数"] <= 90)),
        ("91 到 180 天", (df["发布天数"] >= 91) & (df["发布天数"] <= 180)),
        ("超过 180 天",  df["发布天数"] > 180),
    ]
    by_days = []
    for name, mask in DAY_GROUPS:
        g = df[mask]
        by_days.append({
            "label": name,
            "all": int(len(g)),
            "front0": int((g["前台下载"] == 0).sum()),
            "b0": int((g["b端下载"] == 0).sum()),
            "c0": int((g["c端消费"] == 0).sum()),
            "frontSum": int(g["前台下载"].sum()),
            "bSum": int(g["b端下载"].sum()),
            "cSum": int(g["c端消费"].sum()),
        })

    data = {
        "updateTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": os.path.basename(csv_path),
        "summary": {
            "total": int(total),
            "eligible": eligible,
            "zeroFront": int(zero_front),
            # 整体占比 = 0下载资源总数 ÷ 全量资源总数（2026-08-25 口径：分母从 eligible 改为全量 total）
            "zeroFrontRate": round(zero_front / total * 100, 2),
            # B端0下载 / C端0消费：口径 = 发布超15天的资源（与页面备注一致），2026-08-25 修正
            "zeroB": int((over15["b端下载"] == 0).sum()),
            "zeroC": int((over15["c端消费"] == 0).sum()),
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
        # 发布天数分档统计表（表格区块数据，占比前端计算）
        "byDays": by_days
    }

    # 全量明细：整体加密（XOR+base64），登录后前端整体解密展示；未登录/抓包仅见密文
    detail = zero_df.fillna("").to_dict("records")
    detail_json = json.dumps(detail, ensure_ascii=False)
    detail_bytes = detail_json.encode("utf-8")
    # 明细内容哈希作为版本号：内容不变版本不变 → 浏览器可命中缓存秒开；数据更新版本变化 → 自动取新数据
    data["summary"]["version"] = hashlib.sha1(detail_bytes).hexdigest()[:12]

    # 本页涉及数据整体加密：data.json / detail.json 均只存 {"enc": "..."}，前端登录后解密（密钥 SENS_KEY）
    data_enc = _enc_value(json.dumps(data, ensure_ascii=False))
    detail_enc = _enc_value(detail_json)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(json.dumps({"enc": data_enc}))
    with open(os.path.join(BASE_DIR, "detail.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"enc": detail_enc}))

    print(f"OK 已生成 data.json")
    print(f"数据源：{os.path.basename(csv_path)}")
    print(f"总资源 {total}，前台0下载 {zero_front}（{data['summary']['zeroFrontRate']}%）")


if __name__ == "__main__":
    main()
