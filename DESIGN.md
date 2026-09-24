---
name: Labs-Agent
description: A lab you can watch run, handed back as a draft to check.
colors:
  bg: "#141114"
  bg-raised: "#1a161a"
  surface: "#211c20"
  surface-2: "#2a2429"
  surface-3: "#352d33"
  line: "#2e282d"
  line-strong: "#463d44"
  line-control: "#7a6e75"
  ink: "#f4eeea"
  ink-2: "#c7bdbb"
  ink-3: "#aa9f9e"
  ink-ghost: "#877b82"
  accent: "#ff6a4d"
  accent-hover: "#ff8166"
  accent-mark: "#ff6a4d"
  accent-ink: "#ff7d61"
  on-accent: "#1d0c07"
  accent-soft: "rgb(255 106 77 / 0.13)"
  accent-line: "rgb(255 106 77 / 0.5)"
  danger: "#f27092"
  danger-soft: "rgb(242 112 146 / 0.12)"
  danger-line: "rgb(242 112 146 / 0.45)"
  code-string: "#e2bb8a"
  code-number: "#d9a6c4"
  plot-ground: "#ffffff"
  focus: "#ff8a70"
  bg-light: "#fbf8f6"
  bg-raised-light: "#f4efec"
  surface-light: "#ffffff"
  surface-2-light: "#f2ece9"
  surface-3-light: "#e8e0dc"
  line-light: "#e7dfdb"
  line-strong-light: "#d1c6c2"
  line-control-light: "#8f8380"
  ink-light: "#1f1a1d"
  ink-2-light: "#54494d"
  ink-3-light: "#65595b"
  ink-ghost-light: "#827673"
  accent-hover-light: "#ff5634"
  accent-mark-light: "#e04a2c"
  accent-ink-light: "#bf3419"
  danger-light: "#c0264f"
  code-string-light: "#875314"
  code-number-light: "#9a3b73"
  focus-light: "#d4472a"
typography:
  display:
    fontFamily: "'Bricolage Grotesque Variable', 'Geist Variable', system-ui, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.03em"
    fontVariation: "'wdth' 92"
  headline:
    fontFamily: "'Bricolage Grotesque Variable', 'Geist Variable', system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  title-section:
    fontFamily: "'Bricolage Grotesque Variable', 'Geist Variable', system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    letterSpacing: "-0.02em"
  title:
    fontFamily: "'Bricolage Grotesque Variable', 'Geist Variable', system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.015em"
  body:
    fontFamily: "'Geist Variable', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "'ss01', 'cv11'"
  body-prose:
    fontFamily: "'Geist Variable', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.62
  body-sm:
    fontFamily: "'Geist Variable', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 520
    lineHeight: 1.5
  label:
    fontFamily: "'Geist Variable', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.75rem"
    fontWeight: 520
    lineHeight: 1.35
  mono:
    fontFamily: "'Geist Mono Variable', ui-monospace, 'Cascadia Code', Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "'tnum'"
  mono-2xs:
    fontFamily: "'Geist Mono Variable', ui-monospace, 'Cascadia Code', Consolas, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.35
    fontFeature: "'tnum'"
rounded:
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "22px"
  pill: "999px"
spacing:
  "0": "2px"
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "7": "32px"
  "8": "40px"
  "9": "56px"
  "10": "80px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.pill}"
    padding: "0 16px"
    height: "34px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.on-accent}"
  button-primary-lg:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.pill}"
    padding: "0 20px"
    height: "40px"
  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.pill}"
    padding: "0 16px"
    height: "34px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink}"
  button-quiet:
    textColor: "{colors.ink-2}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.pill}"
    padding: "0 12px"
    height: "34px"
  button-quiet-hover:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
  send:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.pill}"
    size: "36px"
  send-disabled:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink-3}"
  chip:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0 12px 0 10px"
    height: "30px"
  chip-selected:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent-ink}"
  composer:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xl}"
    padding: "12px 12px 12px 16px"
  composer-followup:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "8px 8px 8px 16px"
  input-field:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "42px"
  run-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "20px"
  brief:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "20px"
  nav-lab-link:
    textColor: "{colors.ink-2}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "36px"
  nav-lab-link-active:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink}"
  nav-new-lab:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "40px"
  message-user:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "12px 16px"
  toast:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "12px 12px 12px 16px"
---

# Design System: Labs-Agent

## Overview

**Creative North Star: "The Run Panel"**

Every lab is a pipeline you can watch. The system borrows its grammar from CI pipelines and IDE run panels rather than from chat wrappers: five stages present as ghosts from the first frame, each struck forward when it goes live, each drawing its own tick when it completes, and the run folding into its result in place with every earlier step still legible. Progress is real state drawn as marks, never a spinner and a paragraph.

The world is a warm-tinted near-black in stepped surfaces, built for a student alone at a laptop after midnight with the lights off, with a light theme that is a full twin rather than a filter. Neutrals lean a few degrees toward the coral so every grey reads as one warm family. Electric coral is the only accent and is spent on edges, the active mark, and the single action that moves the lab forward. Rose, a different hue, carries failure and always travels with a mark. Density is product-UI calm: a 46rem working column, a 268px labs sidebar, content as the hero.

Refused, as confirmed by the user: the generic SaaS dashboard look, purple-blue gradients, cards inside cards, heavy glassmorphism, bounce easing, emoji overload.

**Key Characteristics:**
- Warm near-black ground in five stepped layers; light theme re-picked role by role.
- One accent, electric coral, on edges, the active mark and one primary action per view.
- Every state is a drawn mark (ring, arc, dot, tick, notch, cross, strike), never colour alone.
- Bricolage Grotesque for titles, Geist for reading, Geist Mono for files, figures and machine output.
- One quart-out curve family, short rises, ticks that draw themselves, one celebration.
- Flat panels divided by hairlines; shadow only where something floats.

## Colors

A warm, low-chroma near-black family with one electric coral and one rose, every role re-picked for the light ground.

### Primary
- **Electric Coral** (accent): the fill of the one primary action per view (Download on the headline file, "Looks good — go", "Try again", the active Send), the caret, the celebration sparks. Text on it is **Coal Ember** (on-accent) at 7:1.
- **Coral Mark** (accent-mark): coral drawn as a stroke, for the running arc, the waiting ring and dot, the stage-connector shimmer. Identical to the fill on dark; deepened (accent-mark-light) on light because the fill coral is only 2.8:1 on white.
- **Coral Ink** (accent-ink): coral used as text: links, the waiting stage detail, toast actions, the primary artifact's icon. Deepened sharply on light (accent-ink-light).
- **Coral Wash** (accent-soft) and **Coral Edge** (accent-line): the tinted ground of a selected chip, primary artifact icon or drop target; the 1px edge of the live briefing and the inset top line of a working run panel.

### Secondary
- **Failure Rose** (danger): failed rings and crosses, the partial notch, error text, the border of a failed run (danger-line). Never used without a mark or words beside it.

### Neutral
- **Midnight Plum Ground** (bg): the page, inset wells (preview, fail detail, briefing inputs).
- **Raised Plum** (bg-raised): the sidebar, a second neutral layer rather than a card.
- **Panel Plum** (surface): composer, run panel, briefing, new-lab button.
- **Well Plum** (surface-2) and **Pressed Plum** (surface-3): hover, user message, chips, wells; pressed, selected and current nav.
- **Hairline** (line): dividers between rows and panel edges. **Strong Hairline** (line-strong): dividers that must be seen; decorative, below 3:1.
- **Control Line** (line-control): the only boundary of a control, clearing 3:1 on surface and surface-2.
- **Ink ramp** (ink, ink-2, ink-3, ink-ghost): primary text; secondary text; tertiary text at 4.5:1 on every ground including surface-3; ghost for unlit state marks and line numbers at 3:1 or better on all five grounds.
- **Code hues** (code-string, code-number): two quiet hues beside ink in syntax highlighting; the accent is never a syntax colour except builtins in coral ink. Notebook plots sit on white (plot-ground) because they are drawn for paper.

### Named Rules
**The Edge-Not-Fill Rule.** Coral lives on edges, the active mark and the one primary action in a view. A panel is never filled coral; a working run shows coral as a 1px inset top line and nowhere else.

**The One Go Rule.** A view carries at most one coral-filled action: the thing that moves the lab forward. Every other action is secondary (control-line outline on surface-2) or quiet.

**The Twin Rule.** Light is not dark inverted. Every role is re-picked for its ground; coral splits into fill, mark and ink so each clears its own contrast bar in both themes (WCAG 2.2 AA: 4.5:1 text, 3:1 marks, controls and focus).

**The Control Line Rule.** A control's boundary is line-control, always. line-strong is decoration; a control drawn with it fails 3:1.

## Typography

**Display Font:** Bricolage Grotesque Variable (with Geist Variable, system-ui)
**Body Font:** Geist Variable (with system-ui, Segoe UI)
**Label/Mono Font:** Geist Mono Variable (with ui-monospace, Cascadia Code, Consolas)

**Character:** Bricolage brings a slightly condensed, characterful grotesque to titles only; Geist does all the reading in a calm, neutral voice with its ss01 and cv11 alternates; Geist Mono marks anything a machine produced or counts. All three are self-hosted.

### Hierarchy
- **Display** (600, 2.5rem, 2rem at 640px and below, line-height 1.2, -0.03em, width axis 92): the home greeting, one per page.
- **Headline** (600, 1.75rem, 1.2, -0.025em): the lab heading and empty-state titles; 1.375rem on narrow screens.
- **Section Title** (600, 1.375rem, -0.02em): About section heads.
- **Title** (600, 1.125rem, 1.35, -0.015em): run panel and briefing titles, the wordmark at 1rem.
- **Body** (400, 0.9375rem, 1.5): the interface default. Reading passages (agent sentences, draft note, About, markdown preview) use 1.62 leading at a 60ch measure.
- **Body Small** (520, 0.8125rem): buttons, nav links, field labels, secondary copy.
- **Label** (520, 0.75rem, 1.35): stage labels, chips, date-group titles in sentence case.
- **Mono** (400, 0.75rem and 0.6875rem, tabular figures): file names, stage details ("2 tasks", "0/2"), task activity, attempt counts, time and cost figures, kbd hints, code at 0.8125rem.

### Named Rules
**The Titles-Only Display Rule.** Bricolage sets titles and the wordmark, nothing else. When a briefing collapses to its done state, its title drops back to Geist.

**The Machine-Speaks-Mono Rule.** If a program, the file system or a counter produced it, it is set in Geist Mono with tabular figures, so numbers that change in place never jitter.

**The Sentence-Case Rule.** Labels are sentence case at normal tracking. No uppercase tracked labels anywhere in the system.

## Layout

A fixed labs sidebar (268px) beside one centred working column (46rem, with a 16px gutter). The sidebar collapses to a 60px icon rail by the user's choice and becomes an off-canvas drawer (min(86vw, 312px), scrim behind) below 900px wide. A 52px top bar with the lab title appears in drawer mode. Pages other than the lab use a 42rem column; prose caps at 60ch.

Spacing runs on a 4px step (2px to 80px). The lab column stacks entries 20px apart; panels pad 20px (16px at 640px and below); rows inside panels pad 12px vertically and divide with hairlines. The home centres the greeting, a short subline (32rem max) and the composer in the viewport with nothing else on screen: no format chips, no keyboard hint, no example prompts.

Responsive: below 640px the display drops to 2rem, the run head stacks, artifact actions wrap under their row, the stage rail tightens further at 480px. Coarse pointers grow every control to 44px, and touch shows row actions permanently since there is no hover.

## Elevation & Depth

Depth is tonal first: five stepped grounds (bg to surface-3) and hairlines carry nearly all hierarchy. Shadows are offset plus blur, warm-tinted in light mode, and reserved for things that float or invite a hand.

### Shadow Vocabulary
- **Rest** (`box-shadow: var(--shadow-1)` = `0 1px 2px rgb(0 0 0 / 0.35)`): the composer, the new-lab button, the selected segment of a segmented control or theme switch.
- **Overlay** (`box-shadow: var(--shadow-3)` = `0 24px 60px -20px rgb(0 0 0 / 0.75), 0 4px 14px -4px rgb(0 0 0 / 0.4)`): the open drawer and the toast only.
- **Working edge** (`box-shadow: inset 0 1px 0 0 var(--accent-line)`): not depth but state; the top edge of a run panel while it works.

### Named Rules
**The Flat Panel Rule.** Run panels, briefings, notices and previews are flat, bordered by a hairline. A shadow on a panel means it floats; nothing in the column floats.

**The Well-Not-Card Rule.** Inside a panel, structure is rows and hairlines. The only nested box is an inset well one step darker (preview, fail detail, input) on bg, never a raised card inside a card.

## Shapes

Soft, product-grade corners that grow with the container: 6px for tiny controls and kbd, 8px for nav links, inputs and notebook cells, 12px for icon tiles, previews and toasts, 16px for panels and the user message, 26px (`--radius-2xl`) for both composers, which are one row (attach · text · send, 54px tall at one line, buttons pinned to the bottom edge as the text grows). Every button, chip, send button, segmented control and theme switch is a full pill (999px). Status marks are 16px circles of 6.2 radius stroked at 1.6 to 1.9. Borders are always 1px; dashed borders mean "not yet" (pending mark, drop target, notebook output seams).

## Components

### The Mark
A solid disc with an L and a quarter-round cut out of it: the letter, and a lens. One colour, no tile, no accent. Geometry on a 512 grid: disc r128 at (224, 256); L `M168 184h36v100h76v36h-112Z`; quarter-round `M232 248v-48a48 48 0 0 1 48 48Z`; evenodd; square frame `80 112 288 288`. In the app the `Logo` in `components/Icons.jsx` fills with `currentColor`, so it is ink in both themes. Masters live in `logos/export/` (`logo.svg` combination mark with the Helvetica wordmark, `icon.svg` the mark alone, plus `-dark` variants in `#f4f4f4`) with PNGs at 16-2048. `web/ui/public/favicon.svg` is the bare mark and flips to light under `prefers-color-scheme: dark`; favicon-32, apple-touch-icon, icon-192/512 and maskable-512 are the `#111111` mark on a white square (maskable keeps the disc inside the 50% centre). Regenerate rasters from `logos/export/icon.svg` and `logo.svg` rather than editing PNGs.

### Status Mark (signature)
The state vocabulary, drawn identically in the sidebar, the stage rail and task rows. 16px SVG, round caps, no fill.
- **Pending:** dashed ghost ring (ink-ghost), present from the first frame.
- **Running:** ghost ring plus a coral arc (accent-mark) rotating at 900ms linear; the only mark that moves.
- **Waiting:** coral ring plus a filled coral dot: your turn.
- **Done:** ink-3 ring plus an ink tick that draws itself (stroke-dashoffset, 460ms).
- **Partial:** the done tick with a rose notch at the upper right.
- **Failed:** rose ring plus a cross.
- **Lost:** ghost ring struck through diagonally. **Skipped:** ghost ring with a dash; its stage label is struck through.
Every mark is paired with a status word (Not started, Running, Needs your answer, Draft ready, Some tasks failed, Failed, Lost on restart, Skipped) for assistive tech and tooltips.

### Stage Rail (signature)
Five equal columns (Read, Plan, Brief, Solve, Package), each a mark, a 1px connector in line-strong, a label and a mono detail. A stage going live strikes forward (scale 0.72 to 1, 380ms); a coral shimmer travels its connector while it runs; the connector fills with ink-3 as it completes and the tick draws, staggered 70ms per stage. Live and waiting labels go ink and semibold; failed labels go rose.

### Buttons
- **Shape:** full pill (999px), 34px tall (40px large, 44px on coarse pointers), 16px side padding.
- **Primary:** coral fill with coal ember text; one per view.
- **Hover / Focus:** hover lifts to accent-hover in 160ms; press scales to 0.97; focus is a 2px focus-colour outline offset 2px, keyboard only.
- **Secondary:** surface-2 fill with a control-line border and ink text; hover steps to surface-3.
- **Quiet:** no fill, ink-2 text; hover and expanded state take surface-2 and ink.
- **Send:** a 36px coral circle with an arrow; disabled sinks to surface-3 and ink-3; busy swaps the arrow for a spinning ring.

### Chips
- **Style:** 30px pill, control-line border, ink-2 label text, with a leading 8px ring.
- **State:** selected fills the ring (a shape change, not only colour), washes the ground in coral wash, borders in coral and sets text in coral ink. Chips live only in the briefing's "Files to produce"; the home composer takes formats in words.

### Cards / Containers
- **Corner Style:** 16px (panels), 12px (previews, notices on disk, toasts).
- **Background:** surface for run panel, briefing and notice; bg for inset wells.
- **Shadow Strategy:** none; see The Flat Panel Rule.
- **Border:** 1px hairline; the live briefing takes coral edge; a failed run takes rose edge; a working run keeps line-strong plus the coral inset top line. A briefing, once answered, collapses to a transparent hairline summary.
- **Internal Padding:** 20px (16px narrow); rows 12px vertical.

### Inputs / Fields
- **Style:** 42px tall, 8px radius, bg ground, control-line border; field label in semibold body small, the question's reason beneath it in ink-3.
- **Focus:** border turns coral plus a 2px focus outline. The composer takes the same ring outside its 22px box, keyed on the text box itself.
- **Error / Disabled:** invalid borders go rose with rose text below; a disabled composer loses its shadow and sinks to bg-raised.

### Navigation
- **Style:** the sidebar on bg-raised holds the wordmark, a New lab button (surface, control-line border, rest shadow, kbd hint), search, date-grouped lab links with status marks, and a footer with About and a three-way theme switch.
- **States:** links are ink-2 on transparent; hover takes surface-2 and ink; current takes surface-3, ink and medium weight. Rename and hide actions appear on hover or focus; hide turns rose on hover.
- **Mobile:** drawer with scrim, top bar with the lab title, 44px targets.

### Artifact List
Divider rows of a 40px icon tile, the file's plain name, its mono file name and size, and actions. The headline file's tile takes coral wash and coral ink and its Download is the view's primary; the rest are secondary. Code files group under a disclosure. Previews open inline with a rendered or raw segmented toggle.

## Do's and Don'ts

### Do:
- **Do** read every colour, size, radius and duration from tokens.css; the tokens are the single source of truth for both themes.
- **Do** give every state a mark from the Status Mark vocabulary, paired with its word.
- **Do** keep one coral-filled action per view and draw every other boundary with line-control.
- **Do** use coral mark for strokes and coral ink for text, never the fill coral, so each clears contrast in light mode.
- **Do** move with the one curve family: quart-out arrivals at 160, 240 or 380ms, rises of 8px, list staggers of 30ms capped at twelve rows, transform and opacity plus stroke-dashoffset for ticks.
- **Do** keep short fades under reduced motion while removing rises, staggers, spins and shimmers; spinners stop on a legible arc.
- **Do** reserve the coral-spark celebration for a finish the student watched live; a lab read back from disk replays nothing.
- **Do** set files, figures and machine output in Geist Mono with tabular figures.

### Don't:
- **Don't** fill panels, text or backgrounds with gradients, and never purple-blue; the only gradients are motion sweeps (skeleton shimmer, stage connector shimmer) running transparent to a token and back.
- **Don't** put a card inside a card; nest only an inset well on bg.
- **Don't** use bounce, overshoot or elastic easing.
- **Don't** use heavy glassmorphism or backdrop blur.
- **Don't** carry state by colour alone; rose never appears without a mark or words.
- **Don't** add format chips, keyboard hints or example prompts to the home composer; formats are requested in words.
- **Don't** set uppercase tracked labels or eyebrow text above titles.
- **Don't** use emoji or text glyphs as icons; icons are inline SVG.
