#!/usr/bin/env node
// wmux-rpc.js — direct JSON-RPC client for the wmux daemon.
//
// Provenance: original code, written for this repository (Apache-2.0, see ../LICENSE).
// It is NOT vendored or derived from the wmux source tree — it only speaks wmux's
// daemon protocol over the named pipe. wmux itself (https://github.com/openwong2kim/wmux)
// is a separate third-party project that Link16 depends on but does not distribute
// or vendor; its own terms and availability are its own.
//
// 说明：本文件是本仓自建的客户端，不是从 wmux 源码里搬来的，也没有内嵌任何第三方代码；
// 它只是按 wmux daemon 的协议跟它对话。wmux 本体是独立的第三方项目，本仓只依赖、不分发。
// Bypasses the MCP "Workspace identity unknown" guard by speaking the daemon
// protocol over the named pipe with an explicit workspaceId + the on-disk token.
//
// Usage:
//   node wmux-rpc.js panes                         # list panes (raw RPC)
//   node wmux-rpc.js surfaces                       # list surfaces (ptyId<->pane)
//   node wmux-rpc.js read   <ptyId> [tailLines]     # read a terminal screen
//   node wmux-rpc.js send   <ptyId> <text...>       # type text into a terminal
//   node wmux-rpc.js paste  <ptyId> <text...>       # paste big/multiline text (bracketed-paste + throttled chunks; no truncation)
//   node wmux-rpc.js key    <ptyId> <keyName>       # send a named key (enter, ctrl+c, ...)
//   node wmux-rpc.js enter  <ptyId>                 # convenience: send Enter
//   node wmux-rpc.js split-here [dir] [--ws <id>]   # split a pane in YOUR OWN workspace (race-safe)
//   node wmux-rpc.js close  <ptyId|paneId>          # close ONE pane by id (targeted · ws-guarded · verified)
//   node wmux-rpc.js rpc    <method> [jsonParams]   # raw escape hatch
//
// Env overrides: WMUX_AUTH_TOKEN, WMUX_SOCKET_PATH, WMUX_WS (workspaceId).

const net = require("net");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

function readToken() {
  if (process.env.WMUX_AUTH_TOKEN) return process.env.WMUX_AUTH_TOKEN;
  const p = path.join(os.homedir(), ".wmux-auth-token");
  return fs.readFileSync(p, "utf8").trim();
}

function pipeName() {
  if (process.platform === "win32") {
    const u = os.userInfo().username || "default";
    return `\\\\.\\pipe\\wmux-${u}`;
  }
  return `${os.homedir()}/.wmux.sock`;
}

function tcpPort() {
  try {
    const p = path.join(os.homedir(), ".wmux-tcp-port");
    const n = parseInt(fs.readFileSync(p, "utf8").trim(), 10);
    return Number.isFinite(n) ? n : undefined;
  } catch { return undefined; }
}

function attempt(target, token, method, params) {
  return new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const req = JSON.stringify({ id, method, params, token }) + "\n";
    const sock = net.connect(target);
    let buf = "";
    let done = false;
    const timer = setTimeout(() => {
      if (!done) { done = true; sock.destroy(); reject(new Error(`RPC timeout: ${method}`)); }
    }, 10000);
    sock.on("connect", () => sock.write(req));
    sock.on("data", (chunk) => {
      buf += chunk.toString("utf8");
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        try {
          const res = JSON.parse(t);
          if (res.id === id && !done) {
            done = true; clearTimeout(timer); sock.destroy();
            if (res.ok) resolve(res.result); else reject(new Error(res.error));
          }
        } catch {}
      }
    });
    sock.on("error", (e) => { if (!done) { done = true; clearTimeout(timer); reject(e); } });
    sock.on("close", () => { if (!done) { done = true; clearTimeout(timer); reject(new Error("closed before response")); } });
  });
}

async function rpc(method, params = {}) {
  const token = readToken();
  const targets = [];
  if (process.env.WMUX_SOCKET_PATH) targets.push(process.env.WMUX_SOCKET_PATH);
  targets.push(pipeName());
  const port = process.platform === "win32" ? tcpPort() : undefined;
  let lastErr;
  for (const t of targets) {
    try { return await attempt(t, token, method, params); } catch (e) { lastErr = e; }
  }
  if (port) {
    try { return await attempt({ host: "127.0.0.1", port }, token, method, params); } catch (e) { lastErr = e; }
  }
  throw lastErr ?? new Error("no transport");
}

// Resolve the active workspace id once (cached) for input.* calls.
let _wsId = process.env.WMUX_WS || null;
async function wsId() {
  if (_wsId) return _wsId;
  const list = await rpc("workspace.list", {});
  const arr = Array.isArray(list) ? list : (list && list.workspaces) || [];
  _wsId = arr[0] && arr[0].id;
  return _wsId;
}

// ---- workspace guard (2026-06-12) ----
// Write ops (send/key/enter + rpc input.send/input.sendKey/pane.*) must target the
// allowed workspace (WMUX_WS or workspace.list[0]). Cross-workspace writes are DENIED
// unless an explicit `--allow-ws <workspaceIdOrName>` flag is passed (anywhere in argv).
// Read ops (panes/surfaces/read) are never blocked. Background: a SendKeys experiment
// once killed a working pane in another workspace — never again.
let ALLOW_WS = null;
{
  const i = process.argv.indexOf("--allow-ws");
  if (i !== -1) { ALLOW_WS = process.argv[i + 1] || null; process.argv.splice(i, 2); }
}

// ---- blind pane.split refusal (2026-06-16) ----
// pane.split targets the GLOBAL active pane: the daemon hardcodes activeWorkspaceId and ignores
// any workspaceId/ptyId param (verified in app.asar's pane.split handler). With multiple
// workspaces (e.g. the 4-bot feishu bridge) a blind split lands in whichever workspace has global
// focus — i.e. a random sibling session. Refuse it unless explicitly acknowledged. Create workers
// via an isolated workspace instead: `python orchestrator/wmux_session.py spawn --name <n> --cwd <dir>`.
let ALLOW_BLIND_SPLIT = false;
{
  const i = process.argv.indexOf("--allow-blind-split");
  if (i !== -1) { ALLOW_BLIND_SPLIT = true; process.argv.splice(i, 1); }
}

let _wsList = null;
async function wsList() {
  if (_wsList) return _wsList;
  const list = await rpc("workspace.list", {});
  _wsList = Array.isArray(list) ? list : (list && list.workspaces) || [];
  return _wsList;
}

async function ptyWorkspace(ptyId) {
  const arr = await wsList();
  return arr.find((w) => Array.isArray(w.ptyIds) && w.ptyIds.includes(ptyId)) || null;
}

// Returns the workspaceId to use for the call; throws on cross-workspace write without override.
async function guardPty(ptyId, what) {
  const allowed = await wsId();
  const ws = await ptyWorkspace(ptyId);
  if (!ws) {
    if (ALLOW_WS) { console.error(`[guard] WARN: pty ${ptyId} not in any workspace list; proceeding (--allow-ws).`); return allowed; }
    throw new Error(`guard DENIED: pty ${ptyId} not found in workspace.list — refusing ${what}. Re-check ptyId via "surfaces", or pass --allow-ws <wsIdOrName> if intentional.`);
  }
  if (ws.id !== allowed && ALLOW_WS !== ws.id && ALLOW_WS !== ws.name) {
    throw new Error(`guard DENIED: pty ${ptyId} belongs to "${ws.name}" (${ws.id}); allowed workspace is ${allowed}. Cross-workspace write blocked — pass --allow-ws "${ws.name}" (or its id) to override.`);
  }
  if (ws.id !== allowed) console.error(`[guard] OVERRIDE: writing to "${ws.name}" (${ws.id}) via --allow-ws.`);
  return ws.id;
}

// ---- pane.close support (2026-06-22 · wmux 3.6.0+) ----
// pane.close is TARGETED by {id: paneId} and (unlike pane.split) does NOT touch the global
// active pane — verified end-to-end on 3.8.0. Resolve a ptyId-or-paneId to {paneId, ptyId,
// wsId, wsName} by scanning every workspace's surfaces FRESH (no cache — also used for the
// post-close verify). Returns null if the pane is in no workspace (already closed / a ghost).
async function resolvePane(target) {
  const l = await rpc("workspace.list", {});
  const arr = Array.isArray(l) ? l : (l && l.workspaces) || [];
  for (const w of arr) {
    let surfs;
    try { surfs = await rpc("surface.list", { workspaceId: w.id }); } catch { surfs = []; }
    const list = Array.isArray(surfs) ? surfs : [];
    const s = list.find((x) => x.paneId === target || x.ptyId === target);
    if (s) return { paneId: s.paneId, ptyId: s.ptyId, wsId: w.id, wsName: w.name };
  }
  return null;
}

// Same workspace guard as guardPty, but keyed by a resolved pane (pane.close has no ptyId param).
// Throws on a cross-workspace close without --allow-ws. Returns the resolved pane (or null if
// the pane is gone — the caller treats that as "nothing to close").
async function guardPaneId(target, what) {
  const resolved = await resolvePane(target);
  if (!resolved) return null;
  const allowed = await wsId();
  if (resolved.wsId !== allowed && ALLOW_WS !== resolved.wsId && ALLOW_WS !== resolved.wsName) {
    throw new Error(`guard DENIED: pane ${resolved.paneId} belongs to "${resolved.wsName}" (${resolved.wsId}); allowed workspace is ${allowed}. Cross-workspace ${what} blocked — pass --allow-ws "${resolved.wsName}" (or its id) to override.`);
  }
  if (resolved.wsId !== allowed) console.error(`[guard] OVERRIDE: ${what} pane in "${resolved.wsName}" (${resolved.wsId}) via --allow-ws.`);
  return resolved;
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  let out;
  switch (cmd) {
    case "panes":    out = await rpc("pane.list", {}); break;
    case "surfaces": out = await rpc("surface.list", {}); break;
    case "read": {
      const [ptyId, tail] = rest;
      // read is never blocked; resolve the pty's own workspace so cross-ws reads route correctly
      const ws = await ptyWorkspace(ptyId);
      const params = { ptyId, workspaceId: ws ? ws.id : await wsId() };
      if (tail) params.tail_lines = parseInt(tail, 10);
      out = await rpc("input.readScreen", params);
      break;
    }
    case "send": {
      const [ptyId, ...textParts] = rest;
      out = await rpc("input.send", { text: textParts.join(" "), ptyId, workspaceId: await guardPty(ptyId, "send") });
      break;
    }
    case "paste": {
      // 大/多行文本【内联】送进 TUI 输入框：bracketed-paste 包裹（换行当字符·不触发提交）+ 限速分块。
      // 缘由：Claude 输入框有吞吐速率上限，一次灌太快/太多会丢字符（实测 200字/40ms 丢、120字/80ms 全到；
      // 这里用 100字/90ms 留余量）。桥 _inject 用它取代直接 send，根治长消息截断（2026-06-23）。
      const [ptyId, ...textParts] = rest;
      const text = textParts.join(" ");
      const ws = await guardPty(ptyId, "paste");
      const CHUNK = 100, DELAY = 90, BP_START = "\x1b[200~", BP_END = "\x1b[201~";
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const sendChunk = async (t) => {
        for (let a = 0; a < 3; a++) {
          try { return await rpc("input.send", { text: t, ptyId, workspaceId: ws }); }
          catch (e) { if (a === 2) throw e; await sleep(150); }   // 抗瞬时 ECONNRESET
        }
      };
      await sendChunk(BP_START);
      let n = 0;
      for (let i = 0; i < text.length; i += CHUNK) { await sendChunk(text.slice(i, i + CHUNK)); n++; await sleep(DELAY); }
      await sendChunk(BP_END);
      out = { pasted: text.length, chunks: n };
      break;
    }
    case "key": {
      const [ptyId, key] = rest;
      out = await rpc("input.sendKey", { key, ptyId, workspaceId: await guardPty(ptyId, "key") });
      break;
    }
    case "enter": {
      const [ptyId] = rest;
      out = await rpc("input.sendKey", { key: "enter", ptyId, workspaceId: await guardPty(ptyId, "enter") });
      break;
    }
    case "close": {
      // Close ONE specific pane by id (accepts a ptyId or paneId). Targeted + race-safe: pane.close
      // takes {id: paneId} and never touches the global active pane. Workspace-guarded (refuses a
      // cross-workspace close without --allow-ws) and verified (confirms the pane is gone after).
      const [target] = rest;
      if (!target) throw new Error("close: need a ptyId or paneId");
      const resolved = await guardPaneId(target, "close");
      if (!resolved) { out = { closed: true, alreadyGone: true, target }; break; }
      const res = await rpc("pane.close", { id: resolved.paneId });
      await new Promise((r) => setTimeout(r, 400));
      const still = await resolvePane(resolved.paneId);
      if (still) throw new Error(`close: pane.close returned ${JSON.stringify(res)} but pane ${resolved.paneId} is still present`);
      out = { closed: true, paneId: resolved.paneId, ptyId: resolved.ptyId, workspace: resolved.wsName };
      break;
    }
    case "split-here": {
      // Create a pane in MY OWN workspace, race-safe. pane.split can only split the GLOBAL active
      // pane (daemon hardcodes activeWorkspaceId; the workspaceId param is dropped). So: focus my
      // ws -> confirm it became active -> split -> verify the new pane landed in my ws. If a
      // focus-steal raced it elsewhere, exit ONLY the pane WE just created (never a pre-existing
      // one) and retry. Target defaults to $WMUX_WORKSPACE_ID; override with --ws <id>.
      let direction = "vertical";
      let target = process.env.WMUX_WORKSPACE_ID || null;
      for (let i = 0; i < rest.length; i++) {
        if (rest[i] === "--ws") { target = rest[i + 1]; i++; }
        else if (rest[i] === "horizontal" || rest[i] === "vertical") direction = rest[i];
      }
      if (!target) throw new Error('split-here: no target workspace — set $WMUX_WORKSPACE_ID or pass --ws <id>');

      const freshList = async () => {
        const l = await rpc("workspace.list", {});
        return Array.isArray(l) ? l : (l && l.workspaces) || [];
      };
      // wmux 3.46 can briefly return stale workspace.list.ptyIds immediately after pane.split.
      // surface.list({workspaceId}) is fresher, so build the before/after topology from surfaces
      // instead of trusting the cached ptyIds array.  This also prevents a false "no pane" retry
      // from creating several blank panes in the target workspace.
      const surfaceTopology = async (arr) => {
        const ptys = {}, panes = {};
        for (const w of arr) {
          const raw = await rpc("surface.list", { workspaceId: w.id });
          const surfaces = Array.isArray(raw) ? raw : (raw && raw.surfaces) || [];
          for (const s of surfaces) {
            if (!s || !s.ptyId) continue;
            ptys[s.ptyId] = w.id;
            panes[s.ptyId] = s.paneId || null;
          }
        }
        return { ptys, panes };
      };
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

      const MAX = 2; // second attempt is only for a verified focus failure or one cleaned stray
      let lastErr = "unknown";
      for (let attempt = 1; attempt <= MAX && !out; attempt++) {
        const beforeArr = await freshList();
        if (!beforeArr.some((w) => w.id === target)) {
          throw new Error(`split-here: target workspace ${target} not found in workspace.list`);
        }
        const before = await surfaceTopology(beforeArr);
        // focus my ws, then confirm it actually became active (shrinks the focus-steal window)
        await rpc("workspace.focus", { id: target });
        const cur = await rpc("workspace.current", {});
        const curId = cur && (cur.id || cur.workspaceId || (typeof cur === "string" ? cur : null));
        if (curId !== target) { lastErr = `focus did not stick (active=${curId})`; await sleep(150); continue; }
        // my ws is active -> the split lands here
        const sp = await rpc("pane.split", { direction });
        if (sp && sp.error) { lastErr = `pane.split: ${sp.error}`; break; }
        let after = null, fresh = [];
        for (let poll = 0; poll < 20; poll++) {
          await sleep(poll === 0 ? 250 : 150);
          after = await surfaceTopology(await freshList());
          fresh = Object.keys(after.ptys).filter((p) => !(p in before.ptys));
          if (fresh.length) break;
        }
        if (!fresh.length) {
          // Do not retry after an unobserved split: the pane may exist while the daemon's lists
          // are stale. Retrying is worse because it can multiply unowned blank panes.
          throw new Error("split-here: pane.split returned but no new surface was observable after 3s; refusing a duplicate split");
        }
        const landed = fresh.filter((p) => after.ptys[p] === target);
        if (fresh.length === 1 && landed.length === 1) {
          out = { workspaceId: target, pty: landed[0], paneId: after.panes[landed[0]], direction, attempt, observedBy: "surface.list" };
          break;
        }
        if (fresh.length > 1) {
          // Multiple fresh panes means another split raced us. We cannot prove ownership, so do
          // not close any of them and, crucially, do not perform another split.
          throw new Error(`split-here: ${fresh.length} new panes appeared concurrently (${fresh.join(",")}); ownership is ambiguous, refusing cleanup/retry`);
        }
        // raced. Clean up ONLY a pane we are confident we created (exactly one fresh pane), so we
        // never exit another session's concurrently-created pane.
        if (fresh.length === 1) {
          const p = fresh[0], strayWs = after.ptys[p];
          console.error(`[split-here] raced: new pane ${p} landed in ${strayWs}, not ${target} — exiting our stray`);
          try {
            await rpc("input.send", { text: "exit", ptyId: p, workspaceId: strayWs });
            await rpc("input.sendKey", { key: "enter", ptyId: p, workspaceId: strayWs });
          } catch (e) { console.error(`[split-here] stray cleanup failed for ${p}: ${e.message}`); }
        }
        lastErr = `split raced into ${fresh.map((p) => after.ptys[p]).join(",")}`;
        await sleep(200);
      }
      if (!out) throw new Error(`split-here failed after ${MAX} attempts: ${lastErr}`);
      break;
    }
    case "rpc": {
      const [method, json] = rest;
      const params = json ? JSON.parse(json) : {};
      // pane.split cannot be aimed at a workspace — it always splits the GLOBAL active pane
      // (daemon hardcodes activeWorkspaceId; the workspaceId param is dropped — verified in
      // app.asar). In a multi-workspace env it WILL land in the wrong session. Refuse it.
      if (method === "pane.split" && !ALLOW_BLIND_SPLIT) {
        throw new Error(
          "pane.split splits the GLOBAL active pane and ignores workspaceId (the daemon hardcodes " +
          "activeWorkspaceId). With multiple workspaces it lands in whichever has focus = a random " +
          "sibling session. To split a pane in YOUR OWN workspace use:  node wmux-rpc.js split-here " +
          "[vertical|horizontal]  (focuses your ws -> confirms it's active -> splits -> verifies the " +
          "new pane landed in your ws -> self-cleans+retries on a focus-steal; defaults to " +
          "$WMUX_WORKSPACE_ID, or pass --ws <id>; prints {workspaceId, pty}). " +
          "Pass --allow-blind-split only if you truly mean the current global active pane."
        );
      }
      // pane.close is keyed by {id: paneId} (no ptyId) — guard it by resolving the pane's workspace.
      if (method === "pane.close" && params.id) {
        await guardPaneId(params.id, "pane.close");
      }
      // guard write-ish passthrough that targets a specific pty; reads (readScreen/…list) untouched
      if (/^(input\.send|input\.sendKey|pane\.(?!list)|surface\.close)/.test(method) && params.ptyId) {
        params.workspaceId = await guardPty(params.ptyId, method);
      }
      out = await rpc(method, params);
      break;
    }
    default:
      console.error("commands: panes | surfaces | read <pty> [tail] | send <pty> <text> | paste <pty> <text> | key <pty> <key> | enter <pty> | close <pty|paneId> | split-here [dir] [--ws <id>] | rpc <method> [json]");
      process.exit(2);
  }
  console.log(typeof out === "string" ? out : JSON.stringify(out, null, 2));
}

main().catch((e) => { console.error("ERR:", e.message); process.exit(1); });
