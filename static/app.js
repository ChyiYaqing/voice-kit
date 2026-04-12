'use strict';

// ── Sidebar collapse ──────────────────────────────────────────────────────────
const sidebar    = document.getElementById('sidebar');
const toggleBtn  = document.getElementById('toggle-btn');
const iconCollapse = toggleBtn.querySelector('.icon-collapse');
const iconExpand   = toggleBtn.querySelector('.icon-expand');

// Persist state
const sidebarCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
if (sidebarCollapsed) {
  sidebar.classList.add('collapsed');
  iconCollapse.style.display = 'none';
  iconExpand.style.display   = '';
}

toggleBtn.addEventListener('click', () => {
  const isNowCollapsed = sidebar.classList.toggle('collapsed');
  iconCollapse.style.display = isNowCollapsed ? 'none' : '';
  iconExpand.style.display   = isNowCollapsed ? ''     : 'none';
  localStorage.setItem('sidebarCollapsed', isNowCollapsed);
});

// ── Nav tabs ──────────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');

    if (tab.dataset.tab === 'logs')    startLogStream();
    if (tab.dataset.tab === 'history') loadHistory(true);
  });
});

// ── Service status ping ───────────────────────────────────────────────────────
const statusDot   = document.getElementById('status-dot');
const statusLabel = document.getElementById('status-label');
async function checkStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const ok = d.voice_service === 'active';
    statusDot.className   = 'status-dot ' + (ok ? 'active' : 'inactive');
    statusLabel.textContent = ok ? '运行中' : '已停止';
    statusDot.title         = `${d.llm_provider} / ${d.model}`;
  } catch {
    statusDot.className   = 'status-dot inactive';
    statusLabel.textContent = '离线';
  }
}
checkStatus();
setInterval(checkStatus, 15000);

// ── Voice recognition ─────────────────────────────────────────────────────────
const voiceBtn  = document.getElementById('voice-btn');
const textInput = document.getElementById('text-input');
let recognition = null;
let recognising = false;

(function initRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    voiceBtn.disabled = true;
    voiceBtn.title    = '浏览器不支持语音（请用 Chrome）';
    return;
  }
  recognition = new SR();
  recognition.lang            = 'zh-CN';
  recognition.interimResults  = true;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    recognising = true;
    voiceBtn.classList.add('recording');
    textInput.value = '';
    autoResize();
  };

  recognition.onresult = e => {
    const result = e.results[e.results.length - 1];
    const text   = result[0].transcript;
    textInput.value = text;
    autoResize();
    if (result.isFinal && text.trim()) sendMessage(text.trim());
  };

  recognition.onend = () => {
    recognising = false;
    voiceBtn.classList.remove('recording');
  };

  recognition.onerror = e => {
    console.warn('Speech error:', e.error);
    recognising = false;
    voiceBtn.classList.remove('recording');
  };
})();

voiceBtn.addEventListener('mousedown',   e => { e.preventDefault(); startVoice(); });
voiceBtn.addEventListener('touchstart',  e => { e.preventDefault(); startVoice(); }, { passive: false });
voiceBtn.addEventListener('mouseup',     stopVoice);
voiceBtn.addEventListener('mouseleave',  stopVoice);
voiceBtn.addEventListener('touchend',    stopVoice);
voiceBtn.addEventListener('touchcancel', stopVoice);

function startVoice() {
  if (!recognition || recognising) return;
  try { recognition.start(); } catch(e) { console.warn(e); }
}
function stopVoice() {
  if (!recognition || !recognising) return;
  try { recognition.stop(); } catch(e) {}
}

// ── Chat ──────────────────────────────────────────────────────────────────────
const messagesEl = document.getElementById('messages');
const chatHero   = document.getElementById('chat-hero');
const sendBtn    = document.getElementById('send-btn');
let isSending    = false;
let hasMessages  = false;

// Auto-resize textarea
function autoResize() {
  textInput.style.height = 'auto';
  textInput.style.height = Math.min(textInput.scrollHeight, 200) + 'px';
}
textInput.addEventListener('input', autoResize);

// Hide hero when first message arrives
function hideHero() {
  if (!hasMessages) {
    hasMessages = true;
    chatHero.classList.add('hidden');
  }
}

function addMessage(role, text, streaming = false) {
  hideHero();

  const row    = document.createElement('div');
  row.className = `msg-row ${role}`;

  const label  = document.createElement('div');
  label.className   = 'msg-label';
  label.textContent = role === 'user' ? '你' : 'AI 助手';

  const bubble = document.createElement('div');
  bubble.className  = 'bubble' + (streaming ? ' streaming' : '');
  bubble.textContent = text;

  row.appendChild(label);
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || isSending) return;

  textInput.value = '';
  autoResize();
  sendBtn.disabled = true;
  isSending = true;

  addMessage('user', text);
  const aiBubble = addMessage('assistant', '', true);

  try {
    const resp = await fetch('/api/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text }),
    });

    if (!resp.ok) {
      aiBubble.classList.remove('streaming');
      aiBubble.textContent = `请求失败 (${resp.status})`;
      return;
    }

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', full = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let payload;
        try { payload = JSON.parse(line.slice(6)); } catch { continue; }
        if (payload.chunk) {
          full += payload.chunk;
          aiBubble.textContent = full;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        if (payload.done || payload.error) break;
      }
    }

    aiBubble.classList.remove('streaming');
    if (!full) aiBubble.textContent = '（无回复）';

  } catch (err) {
    aiBubble.classList.remove('streaming');
    aiBubble.textContent = '网络错误: ' + err.message;
  } finally {
    isSending    = false;
    sendBtn.disabled = false;
    textInput.focus();
  }
}

sendBtn.addEventListener('click', () => sendMessage(textInput.value));

textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(textInput.value);
  }
});

// ── Logs ──────────────────────────────────────────────────────────────────────
const logBodyEl  = document.getElementById('log-body');
const logStatus  = document.getElementById('log-status');
const autoScroll = document.getElementById('auto-scroll');
let logSource    = null;

function startLogStream() {
  if (logSource) return;
  logSource = new EventSource('/api/logs');
  logStatus.textContent = '● 连接中…';
  logStatus.className   = 'log-status';

  logSource.onopen = () => {
    logStatus.textContent = '● 已连接';
    logStatus.className   = 'log-status connected';
  };

  logSource.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.ka) return;           // keepalive ping

    const line = data.line || '';
    const div  = document.createElement('div');
    div.className  = 'log-line';
    div.textContent = line;

    // Colour coding
    const l = line.toLowerCase();
    if (l.includes('[error]') || l.includes('error') || l.includes('failed'))
      div.classList.add('ll-error');
    else if (l.includes('[idle]') || l.includes('ready') || l.includes('[done]'))
      div.classList.add('ll-ok');
    else if (l.includes('[querying') || l.includes('[recording') || l.includes('[speaking'))
      div.classList.add('ll-active');
    else if (l.includes('ai  :') || l.includes('ai:'))
      div.classList.add('ll-ai');

    logBodyEl.appendChild(div);
    if (autoScroll.checked) logBodyEl.scrollTop = logBodyEl.scrollHeight;

    // Keep at most 500 lines in DOM
    while (logBodyEl.children.length > 500)
      logBodyEl.removeChild(logBodyEl.firstChild);
  };

  logSource.onerror = () => {
    logStatus.textContent = '● 已断开，3s 后重连…';
    logStatus.className   = 'log-status disconnected';
    logSource.close();
    logSource = null;
    setTimeout(startLogStream, 3000);
  };
}

document.getElementById('clear-logs').addEventListener('click', () => {
  logBodyEl.innerHTML = '';
});

// ── History ───────────────────────────────────────────────────────────────────
const historyBodyEl = document.getElementById('history-body');
const loadMoreBtn   = document.getElementById('load-more');
const historyCount  = document.getElementById('history-count');
let histOffset      = 0;
let histTotal       = 0;
const HIST_LIMIT    = 60;

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;');
}

function fmtTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month:'2-digit', day:'2-digit',
      hour:'2-digit',  minute:'2-digit',
    });
  } catch { return ts; }
}

function makeMsgRow(msg) {
  const role = msg.role === 'user' ? 'user' : 'assistant';

  const row   = document.createElement('div');
  row.className = `msg-row ${role}`;

  const label = document.createElement('div');
  label.className   = 'msg-label';
  const timeStr = fmtTime(msg.timestamp);
  label.textContent = role === 'user'
    ? `你${timeStr ? '  ' + timeStr : ''}`
    : `AI 助手${timeStr ? '  ' + timeStr : ''}`;

  const bubble = document.createElement('div');
  bubble.className  = 'bubble';
  bubble.textContent = msg.content || '';

  row.appendChild(label);
  row.appendChild(bubble);
  return row;
}

async function loadHistory(reset = false) {
  if (reset) {
    historyBodyEl.innerHTML = '';
    histOffset = 0;
    histTotal  = 0;
    loadMoreBtn.style.display = 'none';
  }

  try {
    const r = await fetch(`/api/history?limit=${HIST_LIMIT}&offset=${histOffset}`);
    const d = await r.json();
    histTotal = d.total || 0;
    historyCount.textContent = histTotal ? `共 ${histTotal} 条消息` : '';

    if (!d.messages || d.messages.length === 0) {
      if (histOffset === 0)
        historyBodyEl.innerHTML = '<div class="h-empty">暂无历史记录</div>';
      loadMoreBtn.style.display = 'none';
      return;
    }

    // Messages arrive newest-first; prepend them so timeline flows top→bottom
    const frag = document.createDocumentFragment();
    // reverse so we insert in chronological order into the fragment
    for (const msg of [...d.messages].reverse()) {
      frag.appendChild(makeMsgRow(msg));
    }
    // Prepend fragment (older messages go above existing ones)
    historyBodyEl.insertBefore(frag, historyBodyEl.firstChild);

    histOffset += d.messages.length;
    loadMoreBtn.style.display = histOffset < histTotal ? 'block' : 'none';

    // On first load, scroll to bottom (most recent)
    if (histOffset === d.messages.length) {
      historyBodyEl.scrollTop = historyBodyEl.scrollHeight;
    }
  } catch (err) {
    console.error('History error:', err);
    if (histOffset === 0)
      historyBodyEl.innerHTML = `<div class="h-empty">加载失败: ${escHtml(err.message)}</div>`;
  }
}

loadMoreBtn.addEventListener('click', () => loadHistory(false));

document.getElementById('refresh-history').addEventListener('click', () => {
  loadHistory(true);
});
