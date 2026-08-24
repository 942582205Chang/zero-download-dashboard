// detail 明细工作线程：后台下载 detail.json → 解密敏感字段 → 分批回传主线程
// 目的：把 7.8MB 下载 + 8千条解密移到后台，页面加载/交互保持流畅
const SENS_KEY = 'xkw-0dl-2026';
const SENS_FIELDS = ['审核人','审核时间','用户名','作者id','提成比例','定价','店铺'];

function decSens(s) {
  if (!s) return s;
  try {
    const raw = atob(s);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i) ^ SENS_KEY.charCodeAt(i % SENS_KEY.length);
    return new TextDecoder('utf-8').decode(bytes);
  } catch (e) { return s; }
}

fetch('detail.json?v=' + Date.now())
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(det => {
    const total = det.length;
    self.postMessage({ type: 'start', total });
    const out = new Array(total);
    for (let i = 0; i < total; i++) {
      const c = Object.assign({}, det[i]);
      for (const k of SENS_FIELDS) { if (c[k]) c[k] = decSens(c[k]); }
      out[i] = c;
      if ((i + 1) % 2000 === 0) self.postMessage({ type: 'progress', done: i + 1, total });
    }
    const BATCH = 800;   // 分批回传，避免一次性克隆大对象卡顿
    for (let i = 0; i < total; i += BATCH) {
      self.postMessage({ type: 'batch', data: out.slice(i, i + BATCH) });
    }
    self.postMessage({ type: 'done', total });
  })
  .catch(e => self.postMessage({ type: 'error', message: e.message }));
