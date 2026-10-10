// Formatting helpers and a small Markdown renderer that only ever creates text nodes.

import { h } from "./dom.js";

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const UNITS = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];

export function ago(iso, short = false) {
  const time = typeof iso === "number" ? iso : Date.parse(iso);
  if (!Number.isFinite(time)) return "";
  const seconds = Math.round((time - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  if (short) {
    if (abs < 60) return "now";
    for (const [unit, size] of UNITS) if (abs >= size) return Math.floor(abs / size) + (unit === "month" ? "mo" : unit[0]);
  }
  for (const [unit, size] of UNITS) if (abs >= size) return relative.format(Math.round(seconds / size), unit);
  return abs < 10 ? "just now" : relative.format(seconds, "second");
}

export function clock(iso) {
  const time = typeof iso === "number" ? iso : Date.parse(iso);
  return Number.isFinite(time) ? new Date(time).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
}

export function fullDate(iso) {
  const time = typeof iso === "number" ? iso : Date.parse(iso);
  return Number.isFinite(time) ? new Date(time).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

export function dayLabel(iso) {
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return "";
  const date = new Date(time), today = new Date();
  const days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate()) -
    new Date(date.getFullYear(), date.getMonth(), date.getDate())) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function duration(seconds) {
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const hours = seconds / 3600;
  return hours < 48 ? `${Math.round(hours * 10) / 10} h` : `${Math.round(hours / 24)} days`;
}

export function bytes(size) {
  if (!Number.isFinite(size)) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1048576) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`;
  return `${(size / 1048576).toFixed(1)} MB`;
}

export function humanize(text) {
  const words = String(text ?? "").replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function plural(count, word, many = `${word}s`) {
  return `${count} ${count === 1 ? word : many}`;
}

export function baseName(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function inline(text) {
  const out = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0, match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const piece = match[0];
    out.push(piece[0] === "`" ? h("code", {}, piece.slice(1, -1)) : h("strong", {}, piece.slice(2, -2)));
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Headings, paragraphs, lists, inline code/bold and fenced code. Links stay plain text:
// the content is written by agents.
export function markdown(text) {
  const root = h("div", { class: "md" });
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let list = null, paragraph = [];
  const flush = () => {
    if (paragraph.length) root.append(h("p", {}, inline(paragraph.join(" "))));
    paragraph = [];
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      root.append(h("pre", {}, h("code", {}, code.join("\n"))));
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) { flush(); root.append(h(`h${Math.min(heading[1].length + 2, 6)}`, {}, inline(heading[2]))); continue; }
    const item = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.*)$/);
    if (item) {
      if (!list) { flush(); list = h("ul"); root.append(list); }
      list.append(h("li", {}, inline(item[1])));
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    if (list && /^\s+\S/.test(line)) { list.lastChild.append(" ", ...inline(line.trim())); continue; }
    list = null;
    paragraph.push(line.trim());
  }
  flush();
  return root;
}
