// Reusable interface components: buttons, badges, fields, dialogs, menus, toasts, tabs.

import { $, append, h, icon, uid } from "./dom.js";
import { activityOf, agentName } from "./tasks.js";

// --- Buttons -------------------------------------------------------------------------

export function button(label, { variant = "", size = "", iconName, onClick, type = "button", tip, disabled, href, attrs } = {}) {
  const className = `btn ${variant} ${size}`.replace(/\s+/g, " ").trim();
  const content = [iconName ? icon(iconName) : null, label];
  if (href) return h("a", { class: className, href, "data-tip": tip, ...attrs }, content);
  return h("button", { type, class: className, onclick: onClick, disabled, "data-tip": tip, ...attrs }, content);
}

export function iconButton(iconName, label, onClick, attrs = {}) {
  return h("button", { type: "button", class: "icon-btn", "aria-label": label, "data-tip": label, onclick: onClick, ...attrs }, icon(iconName));
}

// Shows a spinner in the button while the action runs and ignores further activations.
// The button keeps the focus and its own disabled state, which the action may change.
export async function busy(el, action) {
  if (el.classList.contains("busy")) return undefined;
  el.classList.add("busy");
  el.setAttribute("aria-busy", "true");
  try {
    return await action();
  } finally {
    el.classList.remove("busy");
    el.removeAttribute("aria-busy");
  }
}

export function copyButton(text, label = "Copy", attrs = {}) {
  return h("button", {
    type: "button", class: "btn sm ghost", "data-tip": "Copy to clipboard", ...attrs,
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(typeof text === "function" ? text() : text);
        toast("Copied to clipboard");
      } catch {
        toast("Copying is not available in this browser", { tone: "warn" });
      }
    },
  }, icon("copy", "sm"), label);
}

// --- Badges --------------------------------------------------------------------------

export function badge(label, tone = "neutral", { dot = false, live = false, iconName, outline = false, tip } = {}) {
  return h("span", { class: `badge tone-${tone}${outline ? " outline" : ""}`, "data-tip": tip },
    dot ? h("span", { class: `dot${live ? " live" : ""}` }) : null, iconName ? icon(iconName, "sm") : null, label);
}

export function activityBadge(task) {
  const meta = activityOf(task);
  return badge(meta.label, meta.tone, { dot: true, live: Boolean(meta.live) });
}

export function agentChip(name, role) {
  return h("span", { class: "agent", "data-tip": role ? `${role}: ${agentName(name)}` : null },
    h("span", { class: `agent-mark ${name}`, "aria-hidden": "true" }, agentName(name).charAt(0)), agentName(name));
}

export function agentPair(task) {
  return h("span", { class: "row" }, agentChip(task.developer, "Developer"), icon("arrowRight", "sm faint"),
    agentChip(task.reviewer, "Reviewer"));
}

// --- Content blocks ------------------------------------------------------------------

export function callout({ tone = "neutral", iconName = "info", title, body, actions }) {
  return h("div", { class: `callout tone-${tone}`, role: tone === "danger" ? "alert" : "note" }, icon(iconName),
    h("div", { class: "grow" }, title ? h("div", { class: "callout-title" }, title) : null,
      body ? h("div", { class: "callout-body" }, body) : null,
      actions?.length ? h("div", { class: "callout-actions" }, actions) : null));
}

export function skeleton(rows = 3) {
  return h("div", { class: "stack", "aria-hidden": "true" },
    Array.from({ length: rows }, (_, index) => h("div", { class: `skeleton line ${["w80", "w60", "w40"][index % 3]}` })));
}

export function loadingBlock(label = "Loading…") {
  return h("div", { class: "card pad", role: "status", "aria-label": label }, skeleton(4));
}

export function section(title, trailing, body) {
  return h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, title), trailing || null), body);
}

// --- Forms ---------------------------------------------------------------------------

export function field({ label, hint, control, optional = false, error = true }) {
  const id = control.id || uid("f");
  control.id = id;
  const hintEl = hint ? h("div", { class: "field-hint", id: `${id}-hint` }, hint) : null;
  const errorEl = error ? h("div", { class: "field-error", id: `${id}-error`, role: "alert" }) : null;
  const describedBy = [hintEl && hintEl.id, errorEl && errorEl.id].filter(Boolean).join(" ");
  if (describedBy) control.setAttribute("aria-describedby", describedBy);
  const el = h("div", { class: "field" },
    h("label", { class: "field-label", for: id }, label, optional ? h("span", { class: "optional" }, "optional") : null),
    control, hintEl, errorEl);
  el.setError = (message) => {
    if (errorEl) errorEl.textContent = message || "";
    control.setAttribute("aria-invalid", message ? "true" : "false");
  };
  return el;
}

export function toggle({ checked = false, label, onChange, disabled = false }) {
  return h("input", { type: "checkbox", class: "switch", role: "switch", checked, disabled, "aria-label": label,
    onchange: (event) => onChange?.(event.target.checked) });
}

// A radio group drawn as a segmented control. Arrow keys move the selection.
export function segmented({ options, value, onChange, label }) {
  const group = h("div", { class: "segmented", role: "radiogroup", "aria-label": label });
  const render = () => {
    group.replaceChildren(...options.map((option) => h("button", {
      type: "button", role: "radio", "aria-checked": String(option.value === value), disabled: option.disabled,
      tabindex: option.value === value ? "0" : "-1", "data-tip": option.tip,
      onclick: () => select(option.value),
    }, option.icon ? icon(option.icon, "sm") : null, option.label)));
  };
  const select = (next, focus = false) => {
    if (next === value) return;
    value = next;
    render();
    if (focus) group.querySelector('[aria-checked="true"]')?.focus();
    onChange?.(value);
  };
  group.addEventListener("keydown", (event) => {
    const enabled = options.filter((option) => !option.disabled);
    const index = enabled.findIndex((option) => option.value === value);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step || !enabled.length) return;
    event.preventDefault();
    select(enabled[(index + step + enabled.length) % enabled.length].value, true);
  });
  group.setValue = (next) => { value = next; render(); };
  Object.defineProperty(group, "value", { get: () => value });
  render();
  return group;
}

export function selectInput(options, value, attrs = {}) {
  return h("select", { class: "input", ...attrs },
    options.map((option) => h("option", { value: option.value, selected: option.value === value }, option.label)));
}

// A numeric setting with its unit and the range enforced by the backend.
export function numberInput({ value, min, max, unit, label, onInput }) {
  const input = h("input", { type: "number", class: "input num", value: value ?? "", min, max, step: 1, inputmode: "numeric",
    "aria-label": label, oninput: () => onInput?.() });
  const wrap = h("div", { class: "input-unit" }, input, unit ? h("span", { class: "faint" }, unit) : null);
  wrap.input = input;
  // Returns an integer, or throws a message suitable for the field.
  wrap.read = () => {
    const raw = input.value.trim();
    const number = Number(raw);
    if (raw === "" || !Number.isInteger(number)) throw new Error("Enter a whole number");
    if (number < min || number > max) throw new Error(`Must be between ${min} and ${max}`);
    return number;
  };
  return wrap;
}

// --- Overlays ------------------------------------------------------------------------

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])';

function trapFocus(container, event) {
  if (event.key !== "Tab") return;
  const items = [...container.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

// Modal dialog with focus trap, Escape to close and focus restored afterwards.
// `build(close)` returns { body, actions }.
export function dialog({ title, description, build, wide = false, dismissible = true, className = "" }) {
  const previous = document.activeElement;
  const titleId = uid("dlg");
  const overlay = h("div", { class: "overlay" });
  let closed = false;
  const close = (result) => {
    if (closed) return;
    closed = true;
    overlay.remove();
    document.removeEventListener("keydown", onKey, true);
    previous?.focus?.();
    overlay.resolve?.(result);
  };
  const onKey = (event) => {
    if (event.key === "Escape" && dismissible) { event.stopPropagation(); close(undefined); }
    else trapFocus(overlay, event);
  };
  const { body, actions } = build(close);
  const panel = h("div", { class: `dialog ${wide ? "wide" : ""} ${className}`.trim(), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("div", { class: "dialog-head" }, h("div", { class: "grow" }, h("h2", { id: titleId }, title),
      description ? h("p", {}, description) : null)),
    body ? h("div", { class: "dialog-body" }, body) : null,
    actions ? h("div", { class: "dialog-foot" }, actions) : null);
  overlay.append(panel);
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay && dismissible) close(undefined); });
  document.addEventListener("keydown", onKey, true);
  document.body.append(overlay);
  (panel.querySelector("[autofocus]") || panel.querySelector(".dialog-body input, .dialog-body textarea, .dialog-body select") ||
    panel.querySelector(".dialog-foot .primary") || panel.querySelector(FOCUSABLE))?.focus();
  const done = new Promise((resolve) => { overlay.resolve = resolve; });
  return { close, done, panel };
}

// Confirmation for sensitive actions. Resolves true only when confirmed.
export function confirm({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", variant = "primary", iconName }) {
  return dialog({
    title,
    build: (close) => ({
      body: body ? h("div", { class: "stack" }, body) : null,
      actions: [
        button(cancelLabel, { variant: "ghost", onClick: () => close(false) }),
        button(confirmLabel, { variant, iconName, onClick: () => close(true) }),
      ],
    }),
  }).done.then(Boolean);
}

export function toast(message, { tone = "ok", action, duration } = {}) {
  const host = $("#toasts");
  const iconName = { ok: "check", warn: "alert", danger: "alert", accent: "info" }[tone] || "info";
  const el = h("div", { class: `toast tone-${tone}`, role: tone === "danger" ? "alert" : "status" }, icon(iconName),
    h("div", { class: "grow" }, message),
    action ? h("button", { type: "button", class: "link", onclick: () => { action.onClick(); dismiss(); } }, action.label) : null,
    h("button", { type: "button", class: "icon-btn", "aria-label": "Dismiss", onclick: () => dismiss() }, icon("x", "sm")));
  const dismiss = () => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 200);
  };
  host.append(el);
  while (host.children.length > 4) host.firstElementChild.remove();
  setTimeout(dismiss, duration ?? (tone === "danger" ? 8000 : tone === "warn" ? 6000 : 3200));
  return dismiss;
}

export function toastError(error, prefix = "") {
  toast(`${prefix}${error?.message || error}`, { tone: "danger" });
}

// A menu anchored to a button. `build(close)` returns the menu items.
export function popover(anchor, build, { align = "start" } = {}) {
  document.querySelector(".popover")?.dispatchEvent(new Event("dismiss"));
  const menu = h("div", { class: "popover", role: "menu" });
  const close = () => {
    menu.remove();
    document.removeEventListener("mousedown", outside, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", close);
    anchor.setAttribute("aria-expanded", "false");
  };
  const outside = (event) => { if (!menu.contains(event.target) && !anchor.contains(event.target)) close(); };
  const onKey = (event) => {
    const items = [...menu.querySelectorAll(".menu-item")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") { event.stopPropagation(); close(); anchor.focus(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); items[(index + 1) % items.length]?.focus(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); items[(index - 1 + items.length) % items.length]?.focus(); }
    else if (event.key === "Tab") close();
  };
  menu.addEventListener("dismiss", close);
  append(menu, build(close));
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  const width = Math.max(menu.offsetWidth, align === "stretch" ? rect.width : 0);
  if (align === "stretch") menu.style.minWidth = `${rect.width}px`;
  let left = align === "end" ? rect.right - width : rect.left;
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
  let top = rect.bottom + 6;
  if (top + menu.offsetHeight > window.innerHeight - 8) top = Math.max(8, rect.top - menu.offsetHeight - 6);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  anchor.setAttribute("aria-expanded", "true");
  document.addEventListener("mousedown", outside, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", close);
  menu.querySelector(".menu-item")?.focus();
  return close;
}

export function menuItem(label, { iconName, onClick, checked, sub, lead } = {}) {
  return h("button", { type: "button", class: "menu-item", role: checked === undefined ? "menuitem" : "menuitemradio",
    "aria-checked": checked === undefined ? null : String(checked), onclick: onClick },
    lead || (iconName ? icon(iconName) : null),
    h("span", { class: "grow truncate" }, label, sub ? h("small", {}, ` ${sub}`) : null),
    checked === undefined ? null : icon("check", "tick"));
}

// Tabs following the ARIA pattern: arrow keys move between tabs, only the selected one is in the tab order.
export function tabs({ items, selected, onSelect, label }) {
  const list = h("div", { class: "tabs", role: "tablist", "aria-label": label });
  const render = () => {
    list.replaceChildren(...items.map((item) => h("button", {
      type: "button", class: "tab", role: "tab", id: `tab-${item.key}`, "aria-selected": String(item.key === selected),
      "aria-controls": "tabpanel", tabindex: item.key === selected ? "0" : "-1", onclick: () => choose(item.key),
    }, item.icon ? icon(item.icon, "sm") : null, item.label,
      item.count != null ? h("span", { class: `count${item.alert ? " alert" : ""}` }, String(item.count)) : null)));
  };
  const choose = (key, focus = false) => {
    if (key === selected) return;
    selected = key;
    draw();
    if (focus) list.querySelector('[aria-selected="true"]')?.focus();
    onSelect(key);
  };
  list.addEventListener("keydown", (event) => {
    const index = items.findIndex((item) => item.key === selected);
    const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: items.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    choose(items[(next + items.length) % items.length].key, true);
  });
  let drawn = "";
  const draw = () => {
    const sig = JSON.stringify([items, selected]);
    if (sig === drawn) return; // unchanged: keep the buttons, and with them the keyboard focus
    drawn = sig;
    const focused = list.contains(document.activeElement);
    render();
    if (focused) list.querySelector('[aria-selected="true"]')?.focus();
  };
  list.update = (nextItems, nextSelected = selected) => { if (nextItems) items = nextItems; selected = nextSelected; draw(); };
  draw();
  return list;
}

// --- Tooltips ------------------------------------------------------------------------

let tip = null;
let tipTimer = null;

function showTip(target) {
  hideTip();
  const text = target.getAttribute("data-tip");
  if (!text) return;
  tip = h("div", { class: "tooltip", role: "tooltip" }, text);
  document.body.append(tip);
  const rect = target.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - tip.offsetWidth / 2;
  left = Math.max(6, Math.min(left, window.innerWidth - tip.offsetWidth - 6));
  let top = rect.top - tip.offsetHeight - 6;
  if (top < 6) top = rect.bottom + 6;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function hideTip() {
  clearTimeout(tipTimer);
  tip?.remove();
  tip = null;
}

export function initTooltips() {
  document.addEventListener("mouseover", (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (!target) return;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => showTip(target), 350);
  });
  document.addEventListener("mouseout", (event) => { if (event.target.closest?.("[data-tip]")) hideTip(); });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (target && target.matches(":focus-visible")) showTip(target);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("mousedown", hideTip, true);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") hideTip(); });
  document.addEventListener("scroll", hideTip, true);
}
