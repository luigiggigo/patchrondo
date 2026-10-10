// Hash routing: no page reloads, and the session token never appears in a route.

export function parseRoute(hash = location.hash) {
  const raw = hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = Object.fromEntries(new URLSearchParams(query));
  const [head, a, b, c] = parts;
  if (!head) return { name: "home", params };
  if (head === "tasks" && a && b) return { name: "task", project: a, task: b, tab: c || "overview", params };
  if (head === "tasks") return { name: "tasks", params };
  if (head === "new") return { name: "new", params };
  if (head === "projects") return { name: "projects", params };
  if (head === "settings") return { name: "settings", scope: a || "global", section: b || "general", params };
  if (head === "welcome") return { name: "welcome", params };
  if (head === "about") return { name: "about", params };
  return { name: "missing", params };
}

export function href(...parts) {
  let query = "";
  const last = parts[parts.length - 1];
  if (last !== null && typeof last === "object") {
    const params = new URLSearchParams(Object.entries(parts.pop()).filter(([, value]) => value != null && value !== ""));
    query = params.toString() ? `?${params}` : "";
  }
  return `#/${parts.filter((part) => part != null && part !== "").map(encodeURIComponent).join("/")}${query}`;
}

export function navigate(...parts) {
  const target = typeof parts[0] === "string" && parts[0].startsWith("#") ? parts[0] : href(...parts);
  if (location.hash === target) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = target;
}

export function replace(...parts) {
  history.replaceState(null, "", href(...parts));
}
