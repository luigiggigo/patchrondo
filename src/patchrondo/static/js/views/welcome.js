// First-run setup: project, agents, tests and how plan limits are used. Nothing is written
// until the last step, and no provider is called at any point.

import { api, requestId } from "../api.js";
import { clear, h, icon } from "../dom.js";
import { href, navigate } from "../router.js";
import { rondoFace, rondoFull } from "../rondo.js";
import { refresh, state } from "../store.js";
import { AGENTS } from "../tasks.js";
import { badge, busy, button, callout, field, segmented, toast, toggle } from "../ui.js";
import { repositoryField } from "./projects.js";

const STEPS = ["Welcome", "Project", "Agents", "Tests", "Usage"];

export default function mount(root, route, ctx) {
  ctx.setTitle([{ label: "Setup" }]);
  let step = 0;
  let created = null;
  const id = requestId();
  const defaults = { ...(state.settings?.defaults || { developer: "claude", reviewer: "codex" }) };
  const repo = repositoryField({ onChange: () => footer() });
  const name = h("input", { class: "input", type: "text", maxlength: 80, placeholder: "Defaults to the folder name" });
  const testsOn = toggle({ checked: false, label: "Run project tests", onChange: () => { trust.checked = false; body(); testsOn.focus(); } });
  const commands = h("textarea", { class: "input mono", rows: 4, spellcheck: "false", "aria-label": "Test commands", placeholder: "python -m pytest -q" });
  const trust = h("input", { type: "checkbox", class: "check" });
  commands.addEventListener("input", () => { trust.checked = false; });
  const error = h("div", { class: "form-error", role: "alert" });
  const el = { progress: h("ol", { class: "wizard-steps", "aria-label": "Setup steps" }), body: h("div", { class: "wizard-body" }), foot: h("div", { class: "wizard-foot" }) };
  const card = h("div", { class: "card wizard", role: "group", "aria-label": "PatchRondo setup" }, el.progress, el.body, el.foot);
  root.append(h("div", { class: "wizard-page" }, card));

  const hasProject = () => Boolean(created) || Boolean(repo.result?.ok);

  function providerLine(provider) {
    if (!provider) return h("div", { class: "list-row" }, h("span", { class: "skeleton line w60" }));
    let tone = "neutral", label = "Login not confirmed";
    if (provider.installed === false) [tone, label] = ["warn", "Not installed"];
    else if (provider.authenticated === false) [tone, label] = ["warn", "Login needed"];
    else if (provider.authenticated) [tone, label] = provider.warning ? ["warn", "Check billing"] : ["ok", "Ready"];
    return h("div", { class: "list-row" }, h("span", { class: `agent-mark ${provider.name}`, "aria-hidden": "true" }, provider.label.charAt(0)),
      h("div", { class: "list-main" }, h("div", { class: "list-title" }, provider.label),
        h("div", { class: "list-sub wrap" }, provider.hint || provider.warning || provider.version || "")), badge(label, tone, { dot: true }));
  }

  const pages = [
    () => [
      h("div", { class: "wizard-hero" }, rondoFull("welcome", 168),
        h("div", { class: "stack" }, h("h1", {}, "Welcome to PatchRondo"),
          h("p", { class: "lead" }, "I'm Rondo. I let Claude Code and Codex take turns on your project: one develops, the other reviews, and I keep the loop going until the tests pass and the review approves."),
          h("ul", { class: "plain-list checks" },
            h("li", {}, icon("check", "sm"), "Everything runs on this computer, with the official CLIs and your own login."),
            h("li", {}, icon("check", "sm"), "Each task works in its own Git worktree; your checkout is never touched and nothing is committed or pushed for you."),
            h("li", {}, icon("check", "sm"), "Setup takes about a minute and calls no model.")))),
    ],
    () => [
      h("h1", {}, "Choose a project"),
      h("p", { class: "lead" }, "A project is a local Git repository. You can add more later."),
      created ? callout({ tone: "ok", iconName: "check", title: `“${created.name}” is registered`, body: created.repository }) : [repo, field({ label: "Display name", optional: true, control: name, error: false })],
    ],
    () => {
      const view = state.providers || {};
      const options = Object.entries(AGENTS).map(([value, label]) => ({ value, label }));
      const recheck = button("Check again", { size: "sm", variant: "ghost", iconName: "refresh", onClick: () => busy(recheck, () => api.post("/api/providers/refresh", {}).catch(() => {})) });
      return [
        h("h1", {}, "Check your agents"),
        h("p", { class: "lead" }, "PatchRondo drives the official command-line tools. It only checks that they are installed and logged in: no prompt is sent."),
        h("div", { class: "card list" }, ["claude", "codex"].map((key) => providerLine(view.providers?.[key]))),
        h("div", { class: "row" }, h("span", { class: "muted grow" }, view.checking ? "Checking…" : "A missing tool does not block setup: install it later and check again in Settings → Agents."), recheck),
        h("div", { class: "grid c2" },
          h("div", { class: "field" }, h("div", { class: "field-label" }, "Default developer"), segmented({ label: "Default developer", value: defaults.developer, options, onChange: (value) => { defaults.developer = value; } }),
            h("div", { class: "field-hint" }, "Writes the changes.")),
          h("div", { class: "field" }, h("div", { class: "field-label" }, "Default reviewer"), segmented({ label: "Default reviewer", value: defaults.reviewer, options, onChange: (value) => { defaults.reviewer = value; } }),
            h("div", { class: "field-hint" }, "Reviews read-only."))),
      ];
    },
    () => [
      h("h1", {}, "Project tests"),
      h("p", { class: "lead" }, "A task is done only when your tests pass and the review approves. Without tests a task is developed and reviewed once, then pauses."),
      !hasProject() ? callout({ tone: "neutral", iconName: "info", title: "No project chosen", body: "You can set up tests later in Settings → Tests." }) : [
        h("div", { class: "card" }, h("label", { class: "setting" }, h("div", { class: "setting-text" }, h("div", { class: "field-label" }, "Run project tests"),
          h("div", { class: "field-hint" }, "Off unless you turn it on. You can change this any time.")), testsOn)),
        testsOn.checked ? [
          field({ label: "Commands", control: commands, error: false, hint: "One per line, run in order without a shell, in the task worktree." }),
          callout({ tone: "warn", iconName: "shield", title: "These commands run on your computer with your permissions",
            body: "They are not sandboxed, and they run against code written by the agents. Enable them only for a repository you trust." }),
          h("label", { class: "check-row" }, trust, h("span", {}, h("strong", {}, "I trust this repository"), " and want PatchRondo to run these commands automatically.")),
        ] : null,
      ],
    ],
    () => [
      h("div", { class: "row" }, rondoFace("waiting", "lg"), h("h1", {}, "How your plan is used")),
      h("ul", { class: "plain-list checks" },
        h("li", {}, icon("check", "sm"), h("span", {}, h("strong", {}, "Runs use your own plan. "), "Every Run or Resume calls Claude Code and Codex with your login and counts against the usage limits of your subscriptions. PatchRondo asks for confirmation first.")),
        h("li", {}, icon("check", "sm"), h("span", {}, h("strong", {}, "Limits are respected. "), "When a provider reports a usage limit the task pauses. PatchRondo can wait for the reset and retry if you enable recovery, but it never works around a limit.")),
        h("li", {}, icon("check", "sm"), h("span", {}, h("strong", {}, "No secrets are stored. "), "Logins stay with the official CLIs. PatchRondo does not read tokens or API keys.")),
        h("li", {}, icon("check", "sm"), h("span", {}, h("strong", {}, "You stay in charge. "), "Nothing is committed, pushed or merged for you: review the result in the task worktree first."))),
      callout({ tone: "accent", iconName: "info", title: "API billing", body: "If ANTHROPIC_API_KEY is set in your environment, Claude Code may bill the API instead of your subscription. Settings → Agents shows the login method it reports." }),
    ],
  ];

  function validate() {
    error.textContent = "";
    if (step === 3 && hasProject() && testsOn.checked) {
      if (!commands.value.trim()) { clear(error, icon("alert", "sm"), "Enter at least one command, or turn tests off."); commands.focus(); return false; }
      if (!trust.checked) { clear(error, icon("alert", "sm"), "Tick “I trust this repository” to enable tests."); trust.focus(); return false; }
    }
    return true;
  }

  async function finish(el_) {
    await busy(el_, async () => {
      try {
        if (!created && repo.result?.ok) {
          created = await api.post("/api/projects", { path: repo.input.value.trim(), name: name.value.trim() || null, request_id: id });
        }
        if (created && testsOn.checked) {
          await api.patch(`/api/projects/${created.id}/config`, { tests: { enabled: true, trust_acknowledged: trust.checked, commands: commands.value.split("\n") } });
        }
        await api.patch("/api/settings", { defaults, onboarding_completed: true, ...(created ? { selected_project: created.id } : {}) });
        await refresh();
        toast(created ? "You're set. Create your first task!" : "Setup finished. Add a project when you are ready.");
        navigate(created ? href("new", { project: created.id }) : href());
      } catch (failure) {
        clear(error, icon("alert", "sm"), failure.message);
        body();
      }
    });
  }

  async function skip() {
    try { await api.patch("/api/settings", { onboarding_completed: true }); await refresh(); } catch { /* shown again next time */ }
    navigate(href());
  }

  function footer() {
    const last = step === STEPS.length - 1;
    const next = button(last ? "Finish setup" : step === 0 ? "Get started" : "Continue", { variant: "primary", size: "lg", iconName: last ? "check" : null,
      onClick: () => { if (!validate()) return; if (last) finish(next); else go(step + 1); } });
    const noProject = step === 1 && !hasProject();
    if (noProject) next.disabled = true;
    clear(el.foot, error,
      step === 0 ? button("Skip setup", { variant: "ghost", onClick: skip }) : button("Back", { variant: "ghost", iconName: "chevronLeft", onClick: () => go(step - 1) }),
      noProject ? button("Skip this step", { variant: "ghost", onClick: () => go(step + 1) }) : null, next);
  }

  function body() {
    clear(el.progress, STEPS.map((label, index) => h("li", { class: `wizard-step${index === step ? " current" : index < step ? " done" : ""}`, "aria-current": index === step ? "step" : null },
      h("span", { class: "wizard-dot" }, index < step ? icon("check", "sm") : String(index + 1)), h("span", { class: "hide-sm" }, label))));
    clear(el.body, pages[step]());
    footer();
  }

  function go(next) {
    step = Math.max(0, Math.min(STEPS.length - 1, next));
    error.textContent = "";
    body();
    (el.body.querySelector("[autofocus], input, textarea") || el.foot.querySelector(".primary"))?.focus();
  }

  body();
  let shown = "";
  return { update() {
    // Only the agents step shows live data; redraw it when the CLI status actually changed.
    const next = JSON.stringify(state.providers);
    if (step === 2 && next !== shown) body();
    shown = next;
  }, destroy() {} };
}
