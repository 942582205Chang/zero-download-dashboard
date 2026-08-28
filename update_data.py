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
import gzip
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


def _enc_bytes(b):
    """bytes 按密钥逐字节 XOR + base64（返回 ascii 字符串）"""
    key = SENS_KEY
    out = bytes(x ^ key[i % len(key)] for i, x in enumerate(b))
    return base64.b64encode(out).decode("ascii")


def _enc_value(v):
    """简单可逆混淆：按密钥逐字节 XOR + base64。空值原样保留。"""
    if v is None or v == "":
        return v
    return _enc_bytes(str(v).encode("utf-8"))


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


def build_day_table(df, overall_label):
    """发布天数分档统计表（8行：整体/不超过15天/已超过15天/16-30/31-60/61-90/91-180/超过180天）。
    overall_label 为表格第一行名称（小初高整体 / 小学整体 / 初中整体 / 高中整体）。"""
    DAY_GROUPS = [
        (overall_label, pd.Series(True, index=df.index)),
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
    return by_days


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

    # 发布天数分档统计表（一、小初高整体）
    by_days = build_day_table(df, "整体")

    # 各学段发布天数分档表（二、各学段数据）：课程前2字 = 学段
    # 学科排序：语文、数学、英语、物理、化学、生物、政治/道法、历史、地理、科学 + 其他学科（字母 ASC，放最后）
    COURSE_ORDER = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "道法", "历史", "地理", "科学"]
    stages = []
    for st in ["小学", "初中", "高中"]:
        sub = df[df["课程"].astype(str).str.startswith(st)]
        stages.append({"stage": st, "rows": build_day_table(sub, "整体")})

    # 三、各课程数据（每学段一张表）：前三行 = 整体/不超过15天/已超过15天（与二、各学段数据一致），
    # 接下来每行 = 该学段内一个"课程 已超过 15 天"的子集（onlyOver15=True）
    course_stages = []
    for st in ["小学", "初中", "高中"]:
        sub = df[df["课程"].astype(str).str.startswith(st)]
        # 该学段全部课程名（去学段前缀后取学科名），按主学科顺序排序，其余按字母 ASC 放最后
        subj = sorted(set(sub["课程"].astype(str).str[2:]))
        subj.sort(key=lambda s: (COURSE_ORDER.index(s) if s in COURSE_ORDER else len(COURSE_ORDER), s))
        rows = build_day_table(sub, "整体")[:3]   # 整体/不超过15天/已超过15天 三行
        for sname in subj:
            # 该课程"已超过 15 天"的子集数据
            go = sub[(sub["课程"].astype(str).str[2:] == sname) & (sub["发布天数"] > 15)]
            rows.append({
                "label": sname,
                "all": int(len(go)),
                "front0": int((go["前台下载"] == 0).sum()),
                "b0": int((go["b端下载"] == 0).sum()),
                "c0": int((go["c端消费"] == 0).sum()),
                "frontSum": int(go["前台下载"].sum()),
                "bSum": int(go["b端下载"].sum()),
                "cSum": int(go["c端消费"].sum()),
            })
        course_stages.append({"stage": st, "rows": rows})

    # 四、各项目数据（品牌系列）：每学段一张表，参照三、课程表——前三行=整体/不超过15天/已超过15天，
    # 其后每行 = 该学段内一个"品牌"的 发布超15天 子集（仅统计审核通过超15天的资源）
    brand_stages = []
    for st in ["小学", "初中", "高中"]:
        sub = df[df["课程"].astype(str).str.startswith(st)]
        brands = [b for b in sorted(sub["品牌"].astype(str).unique()) if str(b) != "nan"]
        rows = build_day_table(sub, "整体")[:3]   # 整体/不超过15天/已超过15天 三行
        for bname in brands:
            go = sub[(sub["品牌"].astype(str) == bname) & (sub["发布天数"] > 15)]
            rows.append({
                "label": bname,
                "all": int(len(go)),
                "front0": int((go["前台下载"] == 0).sum()),
                "b0": int((go["b端下载"] == 0).sum()),
                "c0": int((go["c端消费"] == 0).sum()),
                "frontSum": int(go["前台下载"].sum()),
                "bSum": int(go["b端下载"].sum()),
                "cSum": int(go["c端消费"].sum()),
            })
        brand_stages.append({"stage": st, "rows": rows})

    # 五、各场景数据（场景）：每学段一张表，参照三/四，其后每行 = 该学段内一个"场景"的 发布超15天 子集
    scene_stages = []
    for st in ["小学", "初中", "高中"]:
        sub = df[df["课程"].astype(str).str.startswith(st)]
        scenes = [s for s in sorted(sub["场景"].astype(str).unique()) if str(s) != "nan"]
        rows = build_day_table(sub, "整体")[:3]   # 整体/不超过15天/已超过15天 三行
        for sname in scenes:
            go = sub[(sub["场景"].astype(str) == sname) & (sub["发布天数"] > 15)]
            rows.append({
                "label": sname,
                "all": int(len(go)),
                "front0": int((go["前台下载"] == 0).sum()),
                "b0": int((go["b端下载"] == 0).sum()),
                "c0": int((go["c端消费"] == 0).sum()),
                "frontSum": int(go["前台下载"].sum()),
                "bSum": int(go["b端下载"].sum()),
                "cSum": int(go["c端消费"].sum()),
            })
        scene_stages.append({"stage": st, "rows": rows})

    # 六、各资源类型数据（资源类型）：每学段一张表，参照三/四/五，其后每行 = 该学段内一个"类型"的 发布超15天 子集
    type_stages = []
    for st in ["小学", "初中", "高中"]:
        sub = df[df["课程"].astype(str).str.startswith(st)]
        types = [t for t in sorted(sub["类型"].astype(str).unique()) if str(t) != "nan"]
        rows = build_day_table(sub, "整体")[:3]   # 整体/不超过15天/已超过15天 三行
        for tname in types:
            go = sub[(sub["类型"].astype(str) == tname) & (sub["发布天数"] > 15)]
            rows.append({
                "label": tname,
                "all": int(len(go)),
                "front0": int((go["前台下载"] == 0).sum()),
                "b0": int((go["b端下载"] == 0).sum()),
                "c0": int((go["c端消费"] == 0).sum()),
                "frontSum": int(go["前台下载"].sum()),
                "bSum": int(go["b端下载"].sum()),
                "cSum": int(go["c端消费"].sum()),
            })
        type_stages.append({"stage": st, "rows": rows})

    # 七、组合筛选表：维度取值（动态取自数据表，不写死）
    # 学段 = 课程前2字；品牌/场景/类型 = 对应列去重（非空，升序）；课程 = 课程列去重（自带学段前缀）
    df["学段"] = df["课程"].astype(str).str[:2]
    combo_dims = {
        "学段": sorted(df["学段"].unique().tolist()),
        "品牌": [str(v) for v in sorted(df["品牌"].astype(str).unique()) if str(v) != "nan"],
        "场景": [str(v) for v in sorted(df["场景"].astype(str).unique()) if str(v) != "nan"],
        "类型": [str(v) for v in sorted(df["类型"].astype(str).unique()) if str(v) != "nan"],
        "课程": [str(v) for v in sorted(df["课程"].astype(str).unique()) if str(v) != "nan"],
    }
    # 按 (学段,品牌,场景,类型,课程) 组合预聚合（整体 + 超过15天 两组 9 个统计值）。
    # 只传聚合结果，不传全量行 → data.json 保持 KB/MB 级，30万+ 行也扛得住（重活在 pandas 后端完成）
    # 金额求和用 round(sum)（比 int() 截断更接近真实，见 C端消费 int=2082444 → round=2082548 的口径差）
    combo_stats = []
    for (st, brand, scene, typ, course), g in df.groupby(["学段", "品牌", "场景", "类型", "课程"], dropna=False):
        def agg(sub):
            return {
                "all": int(len(sub)),
                "front0": int((sub["前台下载"] == 0).sum()),
                "b0": int((sub["b端下载"] == 0).sum()),
                "c0": int((sub["c端消费"] == 0).sum()),
                "frontSum": round(sub["前台下载"].sum()),
                "bSum": round(sub["b端下载"].sum()),
                "cSum": round(sub["c端消费"].sum()),
            }
        a_all = agg(g)
        a_over = agg(g[g["发布天数"] > 15])
        combo_stats.append({
            "学段": str(st), "品牌": str(brand), "场景": str(scene), "类型": str(typ), "课程": str(course),
            "all": a_all, "over15": a_over,
        })
    # 让"全部组合之和"与一/二的全量口径对齐：round 逐组进位可能造成±几元的偏差，
    # 把偏差修正到组合数最大的那组上，保证无筛选时七的合计 == 一的全量合计
    if combo_stats:
        _want = int(df["c端消费"].sum())
        _cur = sum(e["all"]["cSum"] for e in combo_stats)
        if _cur != _want:
            _max = max(combo_stats, key=lambda e: e["all"]["all"])
            _max["all"]["cSum"] += (_want - _cur)

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
        "byCourseStages": course_stages,
        # 发布天数分档统计表（一、小初高整体；三、各学段数据复用）
        "byDays": by_days,
        "byDaysStages": stages,
        # 四、各项目数据（品牌系列）：每学段一张表，其后每行 = 品牌"已超过 15 天"子集
        "byBrandStages": brand_stages,
        # 五、各场景数据：每学段一张表，其后每行 = 场景"已超过 15 天"子集
        "bySceneStages": scene_stages,
        # 六、各资源类型数据：每学段一张表，其后每行 = 类型"已超过 15 天"子集
        "byTypeStages": type_stages,
        # 七、组合筛选表：维度取值（动态）+ 各组合预聚合统计（整体/超过15天）
        "comboDims": combo_dims,
    }

    # 全量明细 + 第七块组合表数据：整体加密（XOR+base64），登录后前端整体解密展示；未登录/抓包仅见密文
    # 第七块 comboStats 从 data.json 挪到 detail.json 懒加载（前端在登录后拉 detail 时一并取用），
    # data.json 首屏只留维度取值 comboDims（2026-08-27）
    detail = zero_df.fillna("").to_dict("records")
    detail_json = json.dumps({"detail": detail, "comboDims": combo_dims, "comboStats": combo_stats},
                             ensure_ascii=False)
    detail_bytes = detail_json.encode("utf-8")
    # 明细内容哈希作为版本号：内容不变版本不变 → 浏览器可命中缓存秒开；数据更新版本变化 → 自动取新数据
    data["summary"]["version"] = hashlib.sha1(detail_bytes).hexdigest()[:12]

    # 本页涉及数据整体加密：data.json / detail.json 均只存 {"enc": "..."}，前端登录后解密（密钥 SENS_KEY）
    # detail.json 先 gzip 压缩再加密：明文 7.4MB→480KB，线上加载从 1 分钟+ 降到秒级（2026-08-25）
    data_enc = _enc_value(json.dumps(data, ensure_ascii=False))
    detail_enc = _enc_bytes(gzip.compress(detail_bytes, compresslevel=9))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(json.dumps({"enc": data_enc}))
    with open(os.path.join(BASE_DIR, "detail.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"enc": detail_enc}))

    print(f"OK 已生成 data.json")
    print(f"数据源：{os.path.basename(csv_path)}")
    print(f"总资源 {total}，前台0下载 {zero_front}（{data['summary']['zeroFrontRate']}%）")


if __name__ == "__main__":
    main()
