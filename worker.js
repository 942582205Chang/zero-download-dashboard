// detail 明细工作线程：后台下载 detail.json → 用登录密码派生密钥整体解密 → 分批回传主线程
// 目的：把 ~10MB 下载 + 整体解密移到后台，页面加载/交互保持流畅
// 密钥由主线程登录后派生并经 postMessage 传入（不在公开代码里硬编码）
let decKey = null;

function decSens(s, key) {
  if (!s) return s;
  try {
    const raw = atob(s);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i) ^ key[i % key.length];
    return new TextDecoder('utf-8').decode(bytes);
  } catch (e) { return s; }
}

// XOR 解密为原始字节（detail 新格式解出的是 gzip 压缩字节）
function decBytes(s, key) {
  const raw = atob(s);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i) ^ key[i % key.length];
  return bytes;
}

// gzip 解压（Worker 内可用 DecompressionStream）
async function gunzip(bytes) {
  const ds = new DecompressionStream('gzip');
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  const buf = await new Response(stream).arrayBuffer();
  return new TextDecoder('utf-8').decode(buf);
}

// 解密 detail：兼容旧格式（未压缩明文）与新格式（gzip 压缩）
async function parseDetail(enc, key) {
  const dec = decBytes(enc, key);
  try { return JSON.parse(new TextDecoder('utf-8').decode(dec)); }
  catch (e) { return JSON.parse(await gunzip(dec)); }
}

self.onmessage = e => {
  decKey = e.data.key;
  start();
};

function start() {
  // 先取 data.json 拿版本号（内容不变则版本不变，命中浏览器缓存秒开；数据更新版本变化自动取新）
  fetch('data.json?v=' + Date.now())
    .then(r => r.json())
    .then(d => {
      const d2 = JSON.parse(decSens(d.enc, decKey));
      if (d2.updateTime) self.postMessage({ type: 'time', updateTime: d2.updateTime });   // 把更新时间传回主线程显示
      const ver = (d2.summary && d2.summary.version) ? d2.summary.version : Date.now();
      return fetch('detail.json?v=' + ver);
    })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(edet => (async () => {
      const det = await parseDetail(edet.enc, decKey);
      // detail.json 现在结构为 {detail, comboDims, comboStats}（第七块 comboStats 也装在这里懒加载）；兼容旧纯数组格式
      const rows = det.detail || det;
      const total = rows.length;
      self.postMessage({ type: 'start', total });
      const out = new Array(total);
      for (let i = 0; i < total; i++) {
        out[i] = Object.assign({}, rows[i]);
        if ((i + 1) % 2000 === 0) self.postMessage({ type: 'progress', done: i + 1, total });
      }
      const BATCH = 800;   // 分批回传，避免一次性克隆大对象卡顿
      for (let i = 0; i < total; i += BATCH) {
        self.postMessage({ type: 'batch', data: out.slice(i, i + BATCH) });
      }
      self.postMessage({ type: 'done', total });
    })().catch(e => self.postMessage({ type: 'error', message: e.message })))
}
