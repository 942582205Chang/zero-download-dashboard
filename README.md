# 品牌0下载预警看板

线上数据看板：统计资源中"前台下载为 0"的资源数量与分布，用于 0 下载预警。

## 在线地址
部署完成后：`https://<你的用户名>.github.io/<仓库名>/`

## 目录结构

```
├── index.html          # 看板页面（前端读取 data.json 渲染）
├── data.json           # 看板数据（由 update_data.py 生成，勿手改）
├── csv/                # 原始数据目录（放入 DataWorks 导出的 CSV）
├── update_data.py      # 数据更新脚本（读取 csv/ 最新 CSV → data.json）
├── .github/workflows/  # GitHub Actions 定时更新
└── README.md
```

## 更新数据的两种方式

### 方式一：自动（推荐）
1. 从 DataWorks 导出最新 CSV，放到本仓库的 `csv/` 目录
2. push 到 GitHub
3. 定时任务每天自动运行 `update_data.py` 更新 `data.json`
4. 也可在仓库 **Actions → update-data → Run workflow** 手动立即更新

### 方式二：手动
本地运行：
```bash
python update_data.py
```
生成的 `data.json` 会更新，push 即可生效。

## 首次部署（GitHub Pages）

1. 在 GitHub 新建仓库（**Public**），上传本项目所有文件（含 `csv/`）
2. 仓库 **Settings → Pages**：
   - Source：`Deploy from a branch`
   - Branch：`main` / `/ (root)`
   - Save
3. 等待 1-2 分钟，访问线上地址
4. 确认 Actions 里 `update-data` 工作流存在且可运行

## 本地预览

```bash
cd zero-download-dashboard
python -m http.server 8000
# 浏览器打开 http://localhost:8000
```

> 注意：直接双击打开 index.html（file:// 协议）时浏览器会拦截 fetch 本地文件，
> 请用上面的 http 服务方式预览。

## 字段说明（csv 原始数据）

| 字段 | 说明 |
|---|---|
| 前台下载 | 前台累计下载量，**为 0 即判定为"0下载"预警** |
| b端下载 | B端渠道下载量 |
| c端消费 | C端渠道消费量 |
| 课程 / 年级 / 品牌 / 地区 | 分组维度，用于分布分析 |
