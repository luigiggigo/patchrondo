// DOM helpers. Everything dynamic is inserted as text nodes or attributes: task data,
// logs and reviews come from agents and repositories and are untrusted.

const SVG = "http://www.w3.org/2000/svg";

const ICONS = {
  home: ["M4 11l8-7 8 7", "M6 10v9h12v-9"],
  tasks: ["M9 6h11", "M9 12h11", "M9 18h11", "M4 6l1 1 2-2", "M4 12l1 1 2-2", "M4 18l1 1 2-2"],
  folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  settings: ["M4 7h10", "M18 7h2", "M4 17h4", "M12 17h8", "M16 5v4", "M10 15v4"],
  plus: ["M12 5v14", "M5 12h14"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  chevronDown: ["M6 9l6 6 6-6"],
  chevronRight: ["M9 6l6 6-6 6"],
  chevronLeft: ["M15 6l-6 6 6 6"],
  arrowRight: ["M5 12h14", "M13 6l6 6-6 6"],
  play: ["M7 5l12 7-12 7z"],
  pause: ["M9 5v14", "M15 5v14"],
  stop: ["M7 7h10v10H7z"],
  repeat: ["M17 3l4 4-4 4", "M3 11v-1a3 3 0 0 1 3-3h15", "M7 21l-4-4 4-4", "M21 13v1a3 3 0 0 1-3 3H3"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"],
  alert: ["M12 3.5L21.5 20h-19z", "M12 10v4.5", "M12 17.4v.1"],
  info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 11v5", "M12 7.6v.1"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-3.5-3.5"],
  code: ["M8 7l-5 5 5 5", "M16 7l5 5-5 5"],
  flask: ["M9 3h6", "M10 3v6l-5.5 10a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"],
  eye: ["M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"],
  flag: ["M5 21V4", "M5 4h11l-2 4 2 4H5"],
  file: ["M7 3h7l5 5v13H7z", "M14 3v5h5"],
  diff: ["M12 5v8", "M8 9h8", "M8 18h8"],
  terminal: ["M4 5h16v14H4z", "M7 9l3 3-3 3", "M12 15h5"],
  activity: ["M3 12h4l3-8 4 16 3-8h4"],
  book: ["M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z", "M5 18a2 2 0 0 1 2-2h12"],
  git: ["M6 4v10", "M6 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6z", "M18 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z", "M18 10c0 4-6 3-9 6"],
  sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 2v2", "M12 20v2", "M4 12H2", "M22 12h-2", "M5 5l1.5 1.5", "M17.5 17.5L19 19", "M5 19l1.5-1.5", "M17.5 6.5L19 5"],
  moon: ["M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"],
  sidebar: ["M4 5h16v14H4z", "M9 5v14"],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  trash: ["M5 7h14", "M10 7V4h4v3", "M7 7l1 13h8l1-13"],
  edit: ["M4 20h4L19 9l-4-4L4 16z", "M13 7l4 4"],
  refresh: ["M20 11a8 8 0 0 0-14.5-4.5L4 8", "M4 4v4h4", "M4 13a8 8 0 0 0 14.5 4.5L20 16", "M20 20v-4h-4"],
  lock: ["M7 11V8a5 5 0 0 1 10 0v3", "M5 11h14v10H5z"],
  shield: ["M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"],
  bolt: ["M13 2L4 14h7l-1 8 9-12h-7z"],
  database: ["M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6", "M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v6H4V6h6"],
  wrap: ["M4 6h16", "M4 12h13a3 3 0 0 1 0 6h-4", "M15 16l-2 2 2 2", "M4 18h5"],
  down: ["M12 4v14", "M6 13l6 6 6-6"],
  up: ["M12 20V6", "M6 11l6-6 6 6"],
  heart: ["M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"],
  dots: ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
  user: ["M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M4 21c0-4 3.6-6 8-6s8 2 8 6"],
};

export function icon(name, className = "") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `icon ${className}`.trim());
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (const d of ICONS[name] || ICONS.info) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value" || key === "checked" || key === "disabled" || key === "selected") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child == null || child === false || child === "") continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function clear(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

// Keyed update: a node whose key and signature are unchanged is kept as it is, so focus,
// hover and scroll survive live refreshes. `items` is [{ key, sig, render }].
export function sync(parent, items) {
  const existing = new Map();
  for (const child of parent.children) if (child.dataset.key) existing.set(child.dataset.key, child);
  let cursor = parent.firstElementChild;
  for (const item of items) {
    const sig = typeof item.sig === "string" ? item.sig : JSON.stringify(item.sig);
    let node = existing.get(item.key);
    if (!node || node.dataset.sig !== sig) {
      const fresh = item.render();
      fresh.dataset.key = item.key;
      fresh.dataset.sig = sig;
      if (node) {
        const focused = node.contains(document.activeElement);
        node.replaceWith(fresh);
        if (cursor === node) cursor = fresh;
        if (focused) (fresh.matches("a,button,[tabindex]") ? fresh : fresh.querySelector("a,button,[tabindex]"))?.focus();
      }
      node = fresh;
    }
    existing.delete(item.key);
    if (node !== cursor) parent.insertBefore(node, cursor);
    else cursor = cursor.nextElementSibling;
  }
  for (const stale of existing.values()) stale.remove();
  // Anything without a key that was left behind (placeholders, skeletons) goes too.
  for (const child of [...parent.children]) if (!child.dataset.key) child.remove();
}

// Rebuild an element's children only when `sig` changed since the last call.
export function memo(el, sig, build) {
  const next = typeof sig === "string" ? sig : JSON.stringify(sig);
  if (el.dataset.sig === next) return false;
  el.dataset.sig = next;
  clear(el, build());
  return true;
}

export const $ = (selector, root = document) => root.querySelector(selector);

export function uid(prefix = "id") {
  uid.n = (uid.n || 0) + 1;
  return `${prefix}-${uid.n}`;
}
