// HTTP access to the local API. The session token arrives in the URL fragment, is kept
// for this tab only and is removed from the address bar; it is sent as a header, never
// in a URL.

const KEY = "patchrondo.token";

function takeToken() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const fromUrl = params.get("token");
  let stored = null;
  try { stored = sessionStorage.getItem(KEY); } catch { /* storage disabled */ }
  if (fromUrl) {
    try { sessionStorage.setItem(KEY, fromUrl); } catch { /* keep it in memory only */ }
    const route = params.get("route") || "";
    history.replaceState(null, "", location.pathname + (route ? `#${route}` : "#/"));
    return fromUrl;
  }
  return stored || "";
}

export const token = takeToken();

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(method, path, body, signal) {
  const headers = { "X-PatchRondo-Token": token };
  const options = { method, headers, cache: "no-store", signal };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("PatchRondo is not reachable. Is it still running in your terminal?", 0, "offline");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || `Request failed (${response.status})`, response.status, data.code || "failed");
  return data;
}

export const api = {
  get: (path, signal) => request("GET", path, undefined, signal),
  post: (path, body = {}) => request("POST", path, body),
  patch: (path, body = {}) => request("PATCH", path, body),
  del: (path) => request("DELETE", path, {}),
};

export function requestId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Server-sent events over fetch, so the token can travel in a header.
// Calls onEvent(name, data) per event and resolves when the stream ends.
export async function stream(path, { signal, onEvent, onOpen }) {
  const response = await fetch(path, { headers: { "X-PatchRondo-Token": token }, cache: "no-store", signal });
  if (!response.ok || !response.body) throw new ApiError(`Event stream failed (${response.status})`, response.status, "stream");
  onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let name = "message", data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (frame.startsWith(":") && !data) { onEvent("ping", null); continue; }
      let parsed = null;
      try { parsed = data ? JSON.parse(data) : null; } catch { continue; }
      onEvent(name, parsed);
    }
  }
}
