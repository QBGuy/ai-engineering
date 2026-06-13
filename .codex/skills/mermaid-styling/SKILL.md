---
name: mermaid-styling
description: Create, restyle, or review Mermaid diagrams in this repository. Use whenever adding or editing a Mermaid code block, architecture diagram, flowchart, sequence diagram, node colors, typography, layout, labels, arrows, or dark-mode styling.
---

# Mermaid Styling

Create Mermaid diagrams that are readable inside Markdown, visually consistent, and verified by rendering.

## Workflow

1. Read the surrounding document and preserve the diagram's technical meaning.
2. Choose layout for the rendered page, not just the source:
   - Prefer `flowchart TB` for diagrams with many stages or branches.
   - Use `direction LR` inside groups only when it remains readable.
   - Avoid a page-wide `LR` layout that shrinks labels into a thin strip.
3. Start from [references/template.md](references/template.md) and adapt it.
4. Apply the repository palette and hierarchy below.
5. Render the containing Markdown with `scripts/render-mermaid.sh`.
6. Inspect the PNG. Fix clipping, overlap, tiny labels, awkward routing, and excess whitespace.
7. Render once more and run `git diff --check`.

Do not claim a diagram is finished without a successful render and visual inspection.

## Visual Principles

- Read this repository as a technical learning resource: calm, clear, devtool-like, and information-first.
- Use one cobalt accent. Do not assign a saturated color to every category.
- Emphasize the main request path and decision point. Let supporting nodes recede.
- Use opaque dark surfaces so the diagram remains legible on both light and dark Markdown pages.
- Use rounded nodes consistently.
- Bold the component name and keep its explanation on the next line.
- Keep node text at `14px` or larger. Use generous node padding and rank spacing.
- Use dashed, muted styling only for optional or future paths.
- Avoid shadows, gradients, neon colors, and decorative complexity.

## Repository Palette

| Role | Value |
| --- | --- |
| Canvas / edge labels | `#0b1220` |
| Container surface | `#111827` |
| Node surface | `#182235` |
| Edge node surface | `#132340` |
| Main accent | `#2563eb` |
| Accent line | `#60a5fa` |
| Accent border | `#93c5fd` |
| Primary text | `#e5edf7` |
| Muted text | `#94a3b8` |
| Supporting line / border | `#64748b` |
| Container border | `#334155` |

## Hard Gotchas

- **Subgraph heading overlap:** Mermaid routes external arrows through the top-center of subgraphs, where headings render. Do not place visible labels in subgraph headers when external arrows cross them. Use a short Markdown legend above the diagram and blank subgraph labels: `subgraph edge[" "]`.
- **Clipped subgraph labels:** Mermaid HTML labels can size `foreignObject` elements too tightly and hide final characters. Do not work around this with trailing spaces. Move those labels outside the diagram.
- **Arrow labels:** Give labels an opaque `edgeLabelBackground`; otherwise arrows visually pass through text.
- **Edge indexes:** A chained declaration such as `a --> b --> c` creates two edges. Count every expanded edge before writing `linkStyle` indexes.
- **Wide diagrams:** Rendered Markdown width matters. If labels become small, switch the root direction from `LR` to `TB`.
- **Internal direction hints:** Mermaid may ignore `direction LR` inside a subgraph when external edges constrain layout. Verify the render rather than assuming the hint worked.
- **Dark mode:** Never rely on the Markdown page background. Set explicit node and container fills.
- **Unicode and HTML:** Use HTML labels only when needed for bolding and line breaks. Keep text concise.

## Validation

Run:

```bash
bash .codex/skills/mermaid-styling/scripts/render-mermaid.sh path/to/file.md
```

The script prints the temporary PNG path. Inspect it with the available image viewer. Check:

- every word is complete;
- no arrow crosses text;
- no label sits on a border;
- main flow is obvious;
- optional paths are visibly secondary;
- diagram fits normal Markdown width;
- text remains readable at default zoom.

For detailed configuration and a copy-ready starting point, read [references/template.md](references/template.md).
