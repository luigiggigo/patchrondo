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
   cannot confirm one, the label says "unverified". A saved retry plan with no
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
(Develop → Test → Review → Complete with the repeat loop), the four-segment
mini workflow in list rows, the task row and the timeline event.

Every view implements the same states: loading (skeleton), empty (Rondo and an
action), ready, error (callout with a retry where one makes sense), disabled
(with the reason in a tooltip) and, for the whole page, connection lost (a
banner that names the time of the last confirmed state).

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
| Task running | Dashboard, task note | full / head | gentle bob | bolt, accent | Who is working, which phase and iteration |
| Waiting for a usage limit | Task note, wizard usage step | head | slow breathing | clock, violet | That only waiting helps, and until when |
| Needs attention | Dashboard, task note | head | none | pause, amber | What is wrong and what to do |
| Error, blocked, stale lock | Task note | head | slight tilt, static | alert, red | The cause in plain words |
| Review completed, task done | Task note, dashboard | head / full | two small hops | check, green | The result |
| About | About page | full | none | none | Who he is |

Also: the brand mark in the sidebar, the page icon and the boot screen.

Rules:

- Rondo never covers or replaces operational information: the status badge,
  the workflow bar and the details are always present beside him.
- Animations are transforms on a box with fixed dimensions, so they cause no
  layout shift, never block input and stop under reduced motion.
- One sentence at a time, in the first person, concrete and without jokes
  about failures.

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
- Live regions: Rondo's note and toasts are polite; errors are alerts.
- Keyboard: Tab order follows the reading order; arrow keys in tabs, menus,
  segmented controls, the palette and the task list; shortcuts are listed on
  the About page.
- Color is never the only carrier of meaning.

## Untrusted content

Task titles, descriptions, handoffs, reviews, logs, diffs and error messages
come from repositories and agents. The page inserts them only as text nodes or
attribute values (`js/dom.js`); no API that parses a string as HTML is used,
Markdown is rendered by a small renderer that creates elements itself, and
links inside such content stay plain text. `tests/test_frontend.py` enforces
this on every script.
