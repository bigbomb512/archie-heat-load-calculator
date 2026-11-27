# Archie frontend conventions

Use `css/design-system.css` for shared tokens and controls. The palette has one primary blue (steel blue `#246b91`), a neutral near-black ink (`#1f2328`, not navy), neutral muted greys (`#4f5862`, `#59636e`), white, a light grey canvas (`#f6f8fa`), a neutral border (`#d1d9e0`) and a pale blue wash. Chart series that used navy use graphite `#3d4650`. (Chosen 26 Nov 2026: the blue kept, the dark navy text dropped.) Status colours are reserved for warnings, errors and readiness.

- Body: Manrope (variable, 200–800, `assets/fonts/Manrope-Variable.ttf`, SIL Open Font License). Monospace is reserved for drawing labels and engineering data.
- UI text: 12 / 14 / 16px. Small headings: 20 / 24 / 32px. The landing page heading remains responsive.
- Spacing: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px.
- Corners: 6px controls and panels. Circular identity marks and photographic composition are intentional exceptions.
- Buttons: primary and secondary, 44px minimum. Dense secondary controls may use the 36px mini variant.
- Shadows: reserve the shared shadow for transient messages. Forms and panels use borders.
- Numeric results: tabular figures, with units retained in visible labels.
- Use the Archie logo artwork on both the workspace and the entry page.

`job.css` owns the job workflow. `landing-workspace.css` owns the compact entry page and uses the same ink, page, line and action colours. Older stylesheets still supply legacy components.

Do not remove engineering evidence, review gates, scope labels, or infer missing engineering values to simplify the interface. Do not introduce nonfunctional navigation or imply a CAD editor exists where the product supplies calculation and evidence review.

## Entry page appearance

`landing.html` leads directly to Jobs and New job. `landing-workspace.css` is
scoped to that page and has light and dark palettes. The page describes the
actual four-step job flow and current calculation scope.
The dark palette uses a neutral page `#0d1117`, sheet `#161b22`, ink `#e6edf3`,
muted text `#9198a1`, border `#30363d`, faint steel wash `#1f2a33`, and light
steel action `#7cc0e8`. Filled action text uses the dark page colour.

`landing-theme.js` runs in the head before styles paint. It follows the system
colour preference until an explicit toggle selection, then persists that choice
under `archie.landing.theme` (and reads the former Toki key for migration).
Blocked storage still permits in-page switching.
The header control exposes its state and target action to assistive technology.

Verification: `node --test frontend/tests/landing-theme.test.cjs`; browser checks
at desktop and 390px mobile, both palettes, keyboard activation, reload
persistence, and console errors. Calculator workspace styling is independent.
