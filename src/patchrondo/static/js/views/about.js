// About: what PatchRondo is, what this installation can do on this platform, and shortcuts.

import { h, icon } from "../dom.js";
import { href } from "../router.js";
import { rondoFull } from "../rondo.js";
import { state } from "../store.js";
import { badge, button, copyButton } from "../ui.js";

const yes = (value, text) => h("dd", {}, badge(value ? "Available" : "Not available", value ? "ok" : "neutral", { dot: true }), text ? h("div", { class: "field-hint" }, text) : null);

export default function mount(root, route, ctx) {
  ctx.setTitle([{ label: "About" }]);
  const page = h("div", { class: "page narrow" });
  root.append(page);
  function update() {
    const about = state.about || {};
    const platform = about.platform || {};
    if (page.dataset.sig === JSON.stringify(about)) return;
    page.dataset.sig = JSON.stringify(about);
    page.replaceChildren(
      h("div", { class: "about-hero" }, rondoFull("idle", 190),
        h("div", { class: "stack" }, h("h1", {}, "PatchRondo"), h("div", { class: "lead" }, "Code. Review. Repeat."),
          h("p", { class: "muted" }, "A local, resumable orchestrator that lets Claude Code and OpenAI Codex CLI work on your project together: one develops, the other reviews, until the tests pass and the review approves."),
          h("div", { class: "row wrap" }, badge(`Version ${about.version || "?"}`, "accent"), badge("Experimental alpha", "warn", { outline: true }), badge("MIT license", "neutral", { outline: true })),
          h("div", { class: "row wrap" }, button("Source and documentation", { iconName: "external", href: "https://github.com/luigiggigo/patchrondo", attrs: { target: "_blank", rel: "noopener noreferrer" } }),
            button("Open setup again", { variant: "ghost", href: href("welcome") })))),
      h("section", { class: "section card" }, h("div", { class: "card-head" }, h("h2", {}, "Meet Rondo")),
        h("div", { class: "card-body stack" },
          h("p", {}, "Rondo is PatchRondo's raccoon: the blue patch on his chest is the work being mended, and his looped tail is the develop–review loop. A rondo is a piece of music whose main theme keeps coming back, like a task that returns to its developer until it is right."),
          h("p", { class: "muted" }, "Rondo tells you what is going on: he bobs while a task runs, rests while one waits for a usage limit, and hops when one is done. Motion is reduced automatically when your system asks for it."))),
      h("section", { class: "section card" }, h("div", { class: "card-head" }, h("h2", {}, "This installation")),
        h("div", { class: "card-body" }, h("dl", { class: "kv" },
          h("dt", {}, "State directory"), h("dd", {}, h("div", { class: "row wrap" }, h("span", { class: "mono break" }, about.home || ""), about.home ? copyButton(about.home, "Copy") : null)),
          h("dt", {}, "Platform"), h("dd", {}, `${platform.system || "?"}${platform.wsl ? " (WSL)" : ""} · Python ${platform.python || "?"}`),
          h("dt", {}, "Stop a run from here"), yes(platform.stop_supported, platform.stop_supported ? "Sends an interrupt, like Ctrl+C in the run's terminal."
            : "Native Windows cannot deliver an interrupt to a detached run safely. Use Ctrl+C in its terminal, or WSL2."),
          h("dt", {}, "Release a stale lock"), yes(platform.unlock_supported, platform.unlock_supported ? "After the engine confirms that every recorded process is gone."
            : "On native Windows the engine cannot verify the agents' child processes, so a stale lock is removed by hand."),
          h("dt", {}, "Process verification"), h("dd", {}, "Running and waiting processes are confirmed by process ID and start time. When that is not possible the interface says “unverified” instead of guessing.")))),
      h("section", { class: "section card" }, h("div", { class: "card-head" }, h("h2", {}, "Keyboard")),
        h("div", { class: "card-body" }, h("dl", { class: "kv" },
          h("dt", {}, "Search and commands"), h("dd", {}, h("span", { class: "kbd" }, "Ctrl"), " ", h("span", { class: "kbd" }, "K"), " or ", h("span", { class: "kbd" }, "/")),
          h("dt", {}, "New task"), h("dd", {}, h("span", { class: "kbd" }, "N")),
          h("dt", {}, "Go to Home, Tasks, Projects, Settings"), h("dd", {}, h("span", { class: "kbd" }, "G"), " then ", h("span", { class: "kbd" }, "H"), " ", h("span", { class: "kbd" }, "T"), " ", h("span", { class: "kbd" }, "P"), " ", h("span", { class: "kbd" }, "S")),
          h("dt", {}, "Move in lists, tabs and menus"), h("dd", {}, "Arrow keys"),
          h("dt", {}, "Close a dialog or menu"), h("dd", {}, h("span", { class: "kbd" }, "Esc"))))),
      h("p", { class: "faint about-foot" }, icon("shield", "sm"), " PatchRondo is an independent project, not affiliated with or sponsored by Anthropic or OpenAI. It uses the official CLIs and never extracts tokens."));
  }
  return { update, destroy() {} };
}
