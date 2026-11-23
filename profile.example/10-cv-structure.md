# CV structure (template)

Every generated CV follows this structure and **fits on one page**. ✅
`scripts/render_resume.py` implements it; the script's constants are the source of truth for the numbers.
**To change the look** (colors, fonts, sizes, margins), edit the constants at the top of `scripts/render_resume.py`
and update this file to match; the teal accent and the 66 pt margins are design defaults, not requirements.

## Page

| Property | Value |
|---|---|
| Size | US Letter, 612 × 792 pt (8.5 × 11 in), 1 page |
| Margins | left/right **66 pt (~0.92 in)** → text width 480 pt; top ≈ 40 pt; bottom ≥ 36 pt |
| Generator | ReportLab (PDF), text selectable (ATS-friendly) |
| Layout | single column, no tables, no photo, no icons |

## Type and color

| Element | Font | Size | Color |
|---|---|---|---|
| Name | Helvetica-Bold | 20 pt | near-black `#1A1A1A` |
| Headline | Helvetica | 9.5 pt | `#1A1A1A` |
| Contact line | Helvetica | 8.5 pt | `#1A1A1A` |
| Section headings | Helvetica-Bold | 9.5 pt | **teal `#1A7A7A`**, with a thin teal rule (0.75 pt, full text width) under it |
| Body text, bullets | Helvetica | 8 pt, leading ≈ 10.5 pt | `#1A1A1A` |
| Role line ("Company — Title"), skill labels | Helvetica-Bold | 8 pt | `#1A1A1A` |
| Dates | Helvetica | 8 pt | gray `#555555` |

## Sections, in order

1. **Header** (centered)
   - Name (from `01-identity.md`)
   - Headline: `<Role> · <Focus> · <Main stack>` (variants in `01-identity.md`)
   - Contact: `email | linkedin.com/in/…`
2. **Professional Summary** — one paragraph, 5–8 lines (the longest block; first candidate to trim when space is tight).
3. **Technical Skills** — one line per category, `Label: item, item, item`. Choose the categories
   that fit you (for example Languages · Frameworks · Databases · Tools); the renderer accepts any label.
4. **Professional Experience** — per job:
   - `Company — Title` (bold)
   - `Month Year – Month Year` (gray)
   - bullets `•`, wrapped lines indented (~10 pt); each bullet 1–3 lines.
5. **Education** — `University | Degree` with dates on the same line.
6. **Languages** — one line: `<Language>: <level> · <Language>: <level>`.

No Projects section unless your `06-cv-rules.md` says otherwise (the renderer supports extra `##` sections).

## Length budget (to stay on one page)

| Block | Budget |
|---|---|
| Summary | 5–8 lines |
| Skills | 6–8 lines |
| Experience | 8–10 bullets, ≤ 25 lines |
| Education + Languages | 3–4 lines |

If content overflows: shorten the summary first, then merge or drop the least relevant bullets for the
vacancy; never shrink fonts below the sizes above or cut margins.

## File

`Resume_<First>_<Last>_<Company>.pdf` (see `01-identity.md`).
