# Interface design

How the local interface looks and behaves, and the rules that keep it
consistent. The code is in `src/patchrondo/static`: three style sheets and
plain ES modules, no build step.

## Principles

1. **The backend decides, the page shows.** What a task is doing, whether a
   process is attached to it and which actions are possible arrive from the
   API (`task.runtime`). The page never infers a process from a saved status
   and never estimates progress.
2. **Say what is known.** "Running" means a verified process. When the host
   cannot confirm one, the label says "unverified", and nothing that would
   assume the process is gone is offered as safe. A saved retry plan with no
   process is "Retry planned · nothing waiting", never "retrying".
3. **One primary action per screen.** New task on the dashboard, Run or Resume
   on a task, Save on a settings form, Continue in the setup wizard.
4. **Quiet by default.** One accent color, flat surfaces, thin borders. No
   glow, glass or decorative gradients; the only gradient is a faint tint
   behind empty states and the setup wizard.
5. **Useful without motion.** Every state is readable from text, icon and
   color together. Motion only repeats what is already written.

## Tokens

All values are CSS custom properties in `css/tokens.css`. Components use
tokens only; a color literal outside that file is a defect.

### Color

The palette is sampled from the official mascot image
(`docs/assets/rondo-mascot-v1.png`): cream belly `#f8d8c8`, warm grey fur
`#887878`, charcoal mask `#282828`, ink outline `#080818` and the blue patch
`#5878a8`. Neutrals are warm, slightly plum charcoals taken from the mask and
outline; text is a cream white; the patch blue is the single accent.

| Role | Dark (default) | Light |
|---|---|---|
| Page background `--bg` | `#131115` | `#f7f3ef` |
| Sunken (sidebar, inputs, logs) `--bg-sunken` | `#0e0d10` | `#efe9e3` |
| Surface `--surface` / raised `--surface-2`, `--surface-3` | `#1a181d` / `#211f25`, `#2b2830` | `#ffffff` / `#faf7f3`, `#f0eae4` |
| Border `--border` / `--border-strong` | `#2d2a32` / `#3d3944` | `#e5ddd5` / `#cfc5bb` |
| Text `--text` / `--text-2` / `--text-3` | `#f3ebe6` / `#b8adab` / `#918788` | `#221f23` / `#5c5355` / `#7b7072` |
| Accent (the patch) `--accent` | `#82a8de`, ink text on it | `#3f66a3`, white text on it |

The dark accent is the patch blue lifted for contrast on a dark surface; the
light accent is the patch blue darkened for contrast on white.

Status colors are functional and always paired with a label and an icon:

| Tone | Meaning | Used for |
|---|---|---|
| `accent` | in progress | starting, running |
| `wait` (violet) | waiting on time | a process waiting for a quota retry |
| `ok` (green) | finished well | done, tests passed, approved |
| `warn` (amber) | needs a person, not broken | paused, retry planned without a process, interrupted |
| `danger` (red) | stopped | blocked, stale lock, failed tests |
| `neutral` | nothing happening | ready, skipped, unknown |

Text contrast is checked by `tools/ui_e2e.py` in both themes: body text at
least 7:1 and the faintest text used for hints at least 4.5:1 (WCAG AA).

### Type, space, shape, motion

- **Type.** System fonts only, so nothing is downloaded: `Inter` when
  installed, otherwise the platform UI font; a platform monospace for paths,
  identifiers, commands, logs and diffs. Sizes: 11.5, 12.5, 13.5 (body), 15,
  18, 22 and 28 px. Numbers that change use tabular figures.
- **Space.** A 4 px scale: 4, 8, 12, 16, 20, 24, 32, 40, 48.
- **Shape.** Radii 6, 8, 12 and 16 px; full radius for badges and dots.
- **Motion.** 120 ms for hover and press, 180 ms for panels, 320 ms for the
  brand mark. One easing curve. Under `prefers-reduced-motion: reduce` every
  animation and transition is switched off.

## Components

Defined in `css/app.css` and built by `js/ui.js`:

| Component | Notes |
|---|---|
| Button | default, `primary`, `ghost`, `danger`; sizes `sm`, default, `lg`; `busy` shows a spinner and ignores further activations |
| Icon button | always has an accessible name and a tooltip |
| Badge, dot, chip | take a tone class; a pulsing dot only for verified live states |
| Agent card | one per role on the Agents page: agent, preset, model and effort first; everything else behind *Advanced*, grouped by category in native `details`; the command preview and the result of *Test configuration* at the bottom; one *Apply* per card |
| Origin chip | beside every agent setting: the level its value comes from (dashed for PatchRondo's default), with the way back to the inherited value |
| Card, list row, key–value list | flat surfaces with a 1 px border |
| Callout | tone, icon, title, body and actions; `role="alert"` for danger |
| Empty state | dashed "stitched" border in the accent color, like the patch on Rondo's chest, with Rondo, one sentence and one action |
| Skeleton | shown while a first load is pending |
| Field, switch, segmented control, number with unit | labels are real `<label>`s; errors appear next to the field and are announced |
| Dialog | focus trap, Escape, focus returned to the opener |
| Menu / popover | arrow keys, Escape, closes on outside click |
| Tabs | ARIA tab pattern with roving focus |
| Toast | non-blocking, polite; errors stay longer and use `role="alert"` |
| Tooltip | from `data-tip`, on hover and on keyboard focus |
| Command palette | Ctrl/Cmd+K: navigation, projects and tasks |

Task-specific pieces are in `js/components.js`: the workflow bar
(Prepare → Develop → Test → Review → Validate → Complete with the repeat
loop), the six-segment mini workflow in list rows, the task row and the
timeline event. *Prepare* is what PatchRondo does itself before an agent is
called; its state is the environment check saved with the task, and it is
drawn as running only while the backend verifies a running process.
*Validate* is where a task whose code is approved waits for evidence the
agents cannot give; nobody works there, so it is never drawn as running, and
it says "waiting for you".

**Criteria and checks** (`js/views/criteria.js`) is the task's own section for
its acceptance criteria. It is built for somebody who will not read JSON or
logs:

- Two results side by side, never merged: *Code* (approved or not) and *Task
  as a whole*. A progress bar counts required criteria that are verified.
- One row per criterion, a native `details`: a mark in the tone of its status,
  its text, its ID, the status in words, its kind. Rows that need the user
  (failed, blocked, waiting for an authorization or a check, unclear) are open
  by default; what the reader opened or closed is kept across live updates.
- Inside a row: one sentence that says why it is where it is; who does it and
  who verifies it; what it takes and who has that; the reviewer's opinion,
  marked as not being evidence when the criterion is not the reviewer's to
  verify; the evidence, each record with who made it, when, its result and
  whether it still counts; and the actions.
- Actions exist only where the backend carries them out: *Attach evidence…*,
  *Authorize or decline…*, *Check again*, *Complete the task*. An action the
  backend would refuse is not drawn; when a check by the user cannot be
  recorded, the reason the backend gives is shown in its place. Nothing here
  claims to build, download or publish.
- *Who can do what in this task* is closed by default: the four executors,
  each capability with *Observed*, *Configured*, *Not available* or *Unknown*
  and where that was read from.

The *New task* form shows the preflight under the criteria while they are
typed: counts by what each one will take, then one line per criterion. It is
information, never an obstacle: the form says that nothing in it stops the
task from being created.

Three pieces carry the "nothing to configure" path:

- **The composer** on the home page (`js/views/home.js`): one box for what
  should be done, the project with *Choose folder…*, the two roles as small
  selects with their defaults, a readiness badge taken from the checks the
  backend already made, and one primary button. It is the only card with an
  accent outline on that page. *More options* leads to the long form. What was
  typed survives a reload of the tab.
- **The guided fix** on the task page (`js/views/task.js`): a callout with one
  sentence, at most one primary button, and a closed *Technical details*
  block. Rondo's note above it says the same thing in the first person. Paths,
  check names, access lists and system error text appear only inside the
  details block.
- **The test consent** on the task page: the commands, an explicit tick and
  one button. It is a card, not a dialog, because it is a decision to read,
  not an interruption; what was typed in it survives live updates.

The Live Activity view (`js/views/live.js`) has three parts. A header says who
is working, on which iteration, what the latest event was and how long the
step has been running. A toolbar holds a filter, the state of the connection,
*Follow* and a link to the full logs. The list has one row per event: time,
a mark in the tone of the outcome, one line of text, and an expandable block
for a long message or an output. Rows are appended and never rebuilt, so
scroll position, focus and opened blocks survive; a tool call changes its own
row when it ends. Off-screen rows are skipped by layout
(`content-visibility`), and the list keeps at most 4000 rows.

Every view implements the same states: loading (skeleton), empty (Rondo and an
action), ready, error (callout with a retry where one makes sense), disabled
(with the reason in a tooltip) and, for the whole page, connection lost (a
banner that names the time of the last confirmed state).

The Agents page draws its controls from the description the backend sends
(`agents.schema`), so a setting, its limits and its availability are defined
in one place. A setting that cannot be used is shown disabled with its reason
in words under it (the role cannot have it; the installed CLI does not list
its option), never hidden, and an unavailable choice stays in its list,
disabled, with the reason below the control. Changes are applied with a
visible *Apply* on the card, not as you type: an option that widens what an
agent can touch asks for a tick first, in a warning callout that states what
it allows, and the tick is cleared whenever such a value changes. Rondo says
one thing at the top of the page (what is being checked, what is missing,
that changes are not applied yet) and never covers a control.

## Layout

- Sidebar (248 px, collapsible to a 60 px rail) with the brand, the project
  switcher, navigation, agent status and connection status; a top bar with
  breadcrumbs, search and New task; one scrolling content column up to
  1120 px wide.
- Below 860 px the sidebar becomes a drawer behind a menu button, settings
  navigation turns horizontal and two-column pages stack.
- Below 640 px the workflow bar shows only the label of the current step.
- No page scrolls horizontally from 1440 px down to 480 px; long paths wrap,
  long titles truncate, and logs and diffs scroll inside their own box.

## Rondo

Rondo is the product's voice. Only the two official images are used:
`rondo.webp` (full figure) and `rondo-head.webp` (head). His appearance,
colors and proportions are never altered; a mood is expressed by a small
motion and a status mark on the avatar, not by a different drawing.

| Situation | Where | Image | Motion | Mark | What he says |
|---|---|---|---|---|---|
| First start | Setup wizard | full | one wave | none | Welcome and what PatchRondo does |
| No project | Dashboard, Projects, Tasks | full | one wave | none | Invites to add a repository |
| No task | Dashboard, Tasks | head | none | none | Suggests creating one |
| Preparing | Task note | head | gentle bob | bolt, accent | Which stage he is at: the agents, the project, the safe environment, or that he is fixing an access problem |
| Task running | Dashboard, task note | full / head | gentle bob | bolt, accent | Who is working, which phase and iteration |
| A fix or a decision is needed | Task note | head | none | pause, amber | That a small fix is needed and which button applies it, or that the tests wait for the user's say-so |
| Code approved, criteria open | Task note | head | none | pause, amber | That the code is approved, that he did not send it back to the developer for things the developer cannot do, and how many criteria still need a check or a decision |
| Waiting for a usage limit | Task note, wizard usage step | head | slow breathing | clock, violet | That only waiting helps, and until when |
| Needs attention | Dashboard, task note | head | none | pause, amber | What is wrong and what to do |
| Error, blocked, stale lock | Task note | head | slight tilt, static | alert, red | The cause in plain words |
| Review completed, task done | Task note, dashboard | head / full | two small hops | check, green | The result |
| Live Activity header | Task, Live Activity tab | head | the motion of the task's verified state | the same mark | Nothing of his own: the lines beside him name the agent and its latest event |
| About | About page | full | none | none | Who he is |

Also: the brand mark in the sidebar, the page icon and the boot screen.

Rules:

- Rondo never covers or replaces operational information: the status badge,
  the workflow bar and the details are always present beside him.
- Animations are transforms on a box with fixed dimensions, so they cause no
  layout shift, never block input and stop under reduced motion.
- One sentence at a time, in the first person, concrete and without jokes
  about failures.
- His mood follows the verified state of the workflow (`task.runtime`), never
  the stream of events: a burst of activity does not make him work harder, and
  a silent step does not make him stop.

### Illustration variants that would help

The current two images cover every state through motion and marks. Dedicated
drawings, in the same style and palette, would communicate more at a glance.
Until they exist the fallback in the last column is what ships.

| Variant | Use | Current fallback |
|---|---|---|
| Waving | welcome, first project | full figure with a wave motion |
| Working (holding the patch or a needle) | running task | bob motion and bolt mark |
| Sleeping or looking at a clock | usage limit, waiting | breathing motion and clock mark |
| Puzzled | paused, needs attention | pause mark |
| Worried | blocked, stale lock, errors | tilt and alert mark |
| Celebrating | task done | hop motion and check mark |
| Square icon with safe margins | page icon, app icon | the head image |

## Accessibility

- Landmarks (`main`, named `nav`, `aside`), a skip link, one `h1` per page and
  `lang="en"`.
- Every control has an accessible name; icons are decorative
  (`aria-hidden`) and never the only label.
- Visible focus on every focusable element; dialogs trap focus and return it.
- Live regions: Rondo's note and toasts are polite; errors are alerts. The
  activity list is a `log` that is not announced row by row; the one line that
  says what the agent is doing is a polite status.
- Keyboard: Tab order follows the reading order; arrow keys in tabs, menus,
  segmented controls, the palette and the task list; shortcuts are listed on
  the About page.
- Color is never the only carrier of meaning.

## Untrusted content

Task titles, descriptions, handoffs, reviews, logs, diffs, error messages and
every activity event come from repositories and agents. The page inserts them only as text nodes or
attribute values (`js/dom.js`); no API that parses a string as HTML is used,
Markdown is rendered by a small renderer that creates elements itself, and
links inside such content stay plain text. `tests/test_frontend.py` enforces
this on every script.
