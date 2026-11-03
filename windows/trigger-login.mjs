// Trigger Qoder desktop device login over the Electron DevTools protocol.
const PORT = Number(process.env.CDP_PORT || 9222);
const TIMEOUT_MS = Number(process.env.CDP_TIMEOUT_MS || 180000);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function targets() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  if (!res.ok) throw new Error(`CDP ${res.status}`);
  return res.json();
}

function evaluate(wsUrl, expression) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timer = setTimeout(() => {
      try { ws.close(); } catch {}
      reject(new Error("CDP evaluate timeout"));
    }, 30000);
    ws.addEventListener("open", () => {
      ws.send(JSON.stringify({
        id: 1,
        method: "Runtime.evaluate",
        params: { expression, awaitPromise: false, returnByValue: true },
      }));
    });
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id === 1) {
        clearTimeout(timer);
        try { ws.close(); } catch {}
        resolve(msg.result);
      }
    });
    ws.addEventListener("error", (e) => {
      clearTimeout(timer);
      reject(new Error("CDP websocket error"));
    });
  });
}

const deadline = Date.now() + TIMEOUT_MS;
let lastCount = -1;
while (Date.now() < deadline) {
  let list = [];
  try {
    list = await targets();
  } catch (e) {
    await sleep(2000);
    continue;
  }
  if (list.length !== lastCount) {
    lastCount = list.length;
    for (const t of list) {
      console.log(`target: ${t.type} | ${(t.title || "").slice(0, 40)} | ${(t.url || "").slice(0, 120)}`);
    }
  }
  const pages = list.filter((t) => t.type === "page" && t.webSocketDebuggerUrl);
  for (const p of pages) {
    try {
      const r = await evaluate(
        p.webSocketDebuggerUrl,
        "typeof window.qoderDesktop!=='undefined' && typeof window.qoderDesktop.login==='function' ? (window.qoderDesktop.login(), 'triggered') : 'not-ready'"
      );
      const value = r?.result?.value;
      if (value === "triggered") {
        console.log(`login triggered on ${p.url}`);
        process.exit(0);
      }
    } catch (e) {
      // page may be mid-navigation; try next round
    }
  }
  await sleep(3000);
}

console.error("could not trigger login via CDP");
process.exit(1);
