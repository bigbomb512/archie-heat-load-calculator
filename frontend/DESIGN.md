# Toki frontend conventions

Use `css/design-system.css` for shared tokens and controls. The palette has one primary blue, one ink colour, one muted text colour, white, a canvas colour, a border and a wash. Status colours are reserved for warnings, errors and readiness.

- Body: Alte Haas Grotesk. Monospace is reserved for drawing labels and engineering data.
- UI text: 12 / 14 / 16px. Small headings: 20 / 24 / 32px. Marketing display headings remain responsive.
- Spacing: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px.
- Corners: 6px controls, 10px panels. Circular identity marks and photographic composition are intentional exceptions.
- Buttons: primary and secondary, 44px minimum. Dense secondary controls may use the 36px mini variant.
- Shadows: reserve the shared shadow for transient messages and the overlapping photograph. Forms and panels use borders.
- Numeric results: tabular figures, with units retained in visible labels.
- Keep the exact supplied logo artwork, road-video hero and overlapping method photos.

`workspace-theme.css` owns calculator layout. `hvac-identity.css` owns the shared visual identity and landing compositions. Both consume shared tokens. Earlier stylesheets still supply legacy components; migrate them incrementally when those components change rather than introducing a competing page redesign.

Do not remove engineering evidence, review gates, scope labels, or infer missing engineering values to simplify the interface. Do not introduce nonfunctional navigation or imply a CAD editor exists where the product supplies calculation and evidence review.
