# Science/tool visual pack (flat edu)

Science and tool interactive works use a flattened `clean_edu` presentation so
letterbox cards and first-screen demos read as educational UI, not neon-frost
arcade chrome.

## What changed

- `clean_edu` tokens: paper page (`#f8fafc`), light canvas (`#f1f5f9`), ink
  (`#0f172a`), one teal accent (`#0f766e`). No `soft_glow`, `soft_capsule`, or
  dark charcoal playfield.
- `DesktopRuntimeShell` default CSS and `visual_css_from_pack()` emit the same
  flat chrome: solid surfaces, 1px borders, radius ≤ 8px, `max-width: 100%`,
  canvas `min-height` + `max-height:min(38vh,240px)` / `clamp` height,
  `font-size: clamp(14px, 2.8vw, 16px)`.
- Family `draw()` plugins read `--work-*` CSS variables instead of painting
  `#0f172a` + `#facc15` glow.
- HIT/SOFT diversity pins `clean_edu`. MISS / full generate prompts forbid
  backdrop-filter, frosted pills, and neon-yellow-on-charcoal.
- `seed_worthy`, review floors, and DesktopRuntimeShell contracts are unchanged.

## Regenerations vs published works

Assembled HTML is a snapshot. Works already stored in the database keep their
original CSS and canvas fills until someone regenerates them. Covers captured
from those pages also stay as-is.

New HIT/SOFT assemblies and new MISS documents pick up the flat edu look.
Cover generation for `clean_edu` no longer force-mixes the page into
`#020617`, but existing cover images are not rewritten.
