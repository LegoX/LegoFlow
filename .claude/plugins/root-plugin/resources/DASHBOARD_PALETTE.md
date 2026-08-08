# Dashboard palette

The shared color contract for every block's `dashboard/`. It exists so curator,
tracer, trainer and evaluator read as one product, and so they match the docs site
(`docs/src/app/global.css`, published at <https://legoflow-docs.pages.dev/docs>).

Source of truth for the warm "paper" neutrals and the rust accent is the docs
stylesheet — those hexes are copied here unchanged. Everything else (dark-mode
steps, status scale, chart series) was derived to sit with them and **validated**;
see § Validation before changing any value.

## Surfaces, chrome & ink

| Role | Token | Light | Dark |
|---|---|---|---|
| Page plane | `bg` | `#fafaf7` | `#14110e` |
| Panel / card | `panel` | `#ffffff` | `#1a1714` |
| Panel, soft | `panel-soft` | `#f6f4ef` | `#211d19` |
| Panel, softer | `panel-softer` | `#f1efe9` | `#262119` |
| Hairline / rule | `border` | `#e6e3da` | `#302a24` |
| Primary ink | `fg` | `#111111` | `#f0ede7` |
| Secondary ink | `fg-dim` | `#4a453e` | `#c9c2b6` |
| Muted ink | `fg-mute` | `#6b6b66` | `#a9a297` |
| Faint ink | `fg-faint` | `#8c8c85` | `#8a847a` |
| Brand accent | `accent` | `#b3431f` | `#efa07c` |
| Accent wash | `accent-soft` | `#b3431f1f` | `#efa07c26` |
| Accent rule | `accent-border` | `#b3431f80` | `#efa07c80` |
| Chart gridline | `chart-grid` | `#e6e3da` | `#302a24` |
| Chart axis | `chart-axis` | `#c9c3b6` | `#4a423a` |
| Chart tick label | `chart-tick` | `#6b6b66` | `#a9a297` |

`#b3431f` is the docs' `--swe-live-accent`; `#fafaf7` / `#111111` / `#e6e3da` /
`#f1efe9` are its `--color-fd-background` / `-foreground` / `-border` / `-muted`.

**The accent is UI chrome only** — buttons, links, active tabs, focus rings, the
brand mark. It is never a data color. See § The rust/orange rule.

## Status scale (fixed — never themed)

Reserved meaning, never reused as "series N". Always shipped with an icon or a
text label, never color alone.

| Role | Light | contrast | Dark | contrast |
|---|---|---|---|---|
| good | `#3f8f2f` | 3.88 | `#4a9440` | 4.76 |
| warning | `#c8860d` | 2.92 | `#fab219` | 9.73 |
| serious | `#c96a2e` | 3.60 | `#ec835a` | 6.77 |
| critical | `#d03b3b` | 4.59 | `#d03b3b` | 3.71 |

Contrast is WCAG against that mode's `panel`. Light-mode `warning` sits just under
3:1 **by design** — the icon + label pairing is the mitigation, as in the upstream
method. Every other step clears 3:1.

## Chart series (categorical — identity only)

Eight hues in a **fixed order**, assigned in sequence and never cycled:

| Slot | Hue | Light | Dark |
|---|---|---|---|
| 1 | orange | `#eb6834` | `#d95926` |
| 2 | aqua | `#1baf7a` | `#199e70` |
| 3 | blue | `#2a78d6` | `#3987e5` |
| 4 | yellow | `#eda100` | `#c98500` |
| 5 | magenta | `#e87ba4` | `#d55181` |
| 6 | green | `#008300` | `#008300` |
| 7 | violet | `#4a3aa7` | `#9085e9` |
| 8 | red | `#e34948` | `#e66767` |

The order is the colorblind-safety mechanism, not decoration — do not reorder
without re-running the enumeration in § Validation.

**Series cap for all-pairs forms.** Scatter, bubble, and small-multiples charts —
where any two marks can end up adjacent — carry **at most 3 series** (slots 1–3).
The 4th slot puts yellow beside orange, which fails the separation floor in both
modes (normal-vision ΔE 13.7 light, 10.6 dark). Past three, fold the tail into
"Other" or facet. Line/bar/stack charts use the adjacent pairlist and may run the
full eight.

Three light-mode slots (aqua, yellow, magenta) sit below 3:1 on the light surface.
The **relief rule** applies: those charts must ship visible direct labels, a
tooltip, or a table view. All four dashboards already do.

## The rust/orange rule

The brand accent and series slot 1 are the same hue family. Measured separation is
small (normal-vision ΔE 14.1 light, and the *first* dark accent tried measured 2.6
— effectively identical), so the dark accent was re-picked as `#efa07c`, which
holds ΔE 16.7 from the dark series orange at 8.49:1 contrast.

The standing rule: **accent never appears inside a plot**, and a status color never
carries meaning without its icon + label. Same-hue-family neighbours lean on
placement and labels, never on hue alone.

## Validation

Values here were produced with the `dataviz` skill's `validate_palette.js` against
**these** surfaces (`#fafaf7` light, `#1a1714` dark), not the script's defaults.

Categorical palette, both modes, adjacent pairlist:

| Mode | CVD ΔE (≥8) | Normal-vision ΔE (≥15) | Contrast |
|---|---|---|---|
| light | 9.2 | 19.6 | relief on 3 slots |
| dark | 9.4 | 19.3 | all ≥ 3:1 |

The slot order was chosen by enumerating all 40 320 orderings of the eight hues,
keeping only the **160** that clear every hard gate in both modes with CVD at the
≥8 target (not the 6–8 floor), and picking a warm-leading one from the top-scoring
tie. Re-run that enumeration if the hues change:

```bash
node validate_palette.js "#eb6834,#1baf7a,#2a78d6,#eda100,#e87ba4,#008300,#4a3aa7,#e34948" \
  --mode light --surface "#fafaf7"
node validate_palette.js "#d95926,#199e70,#3987e5,#c98500,#d55181,#008300,#9085e9,#e66767" \
  --mode dark  --surface "#1a1714"
```

Add `--pairs all` to re-check the 3-series cap. Status and accent steps are single
colors, so they were checked with WCAG text contrast (`contrast()`), not the
categorical six.

## Applying it

Each dashboard defines these as CSS custom properties in **one** block and
references them by role — never a raw hex at a call site:

- `blocks/curator/dashboard/` — `base_4ds.html` + the duplicate block in `progress_monitor_multi.py`
- `blocks/tracer/dashboard/` — `progress_monitor.py`
- `blocks/trainer/dashboard/` — `src/index.css` (+ `tailwind.config.js` maps the families to those vars)
- `blocks/evaluator/dashboard/` — `static/styles.css`

Dark is the default in all four; light is opt-in via `[data-theme="light"]`.
