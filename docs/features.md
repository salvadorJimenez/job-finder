# Offers: searching, listing and filters

Detailed reference for the **Jobs** page. For the application tracker (board, phases, daily goal) see [applications.md](applications.md); for every `config.toml` key see [configuration.md](configuration.md). Back to the [README](../README.md).

Contents: [How searching works](#how-searching-works) · [The listing](#the-listing) · [Split view](#split-view-screens-1100-px-wide-or-more) · [Detail page](#detail-page) · [Readable descriptions](#readable-descriptions) · [Best match first](#best-match-first) · [Filters](#filters) · [Stack filter](#stack-filter) · [Pagination](#pagination) · [Pinning](#pinning) · [Database upgrades](#database-upgrades) · [Export](#export-for-red-flag-review) · [Install as an app](#install-as-an-app-pwa) · [Sources](#sources)

## How searching works

- A search covers everything posted **since your last successful search**, plus `overlap_hours` of margin. Duplicates that come in through that margin are ignored.
- The first search ever covers `first_search_days` days.
- Each site keeps its own range. If LinkedIn fails and Indeed succeeds, the next search covers LinkedIn's missing days again. A site you add later starts with the `first_search_days` window, since it has no previous search of its own.
- Requests multiply: every **term** runs on every **site** in every **place** (terms × places × sites), with `pause_seconds` between requests to the same site. See [Rate limiting](configuration.md#rate-limiting).
- The **search panel** sits right under the header: the status on the left and the **SEARCH** button on the right. The status says when you last searched (`Searched today at 09:12 · today ✓`, or `Last searched Nov 9 at 18:30 (1 day ago)`, or `Never searched`) and how that search ended: `✓ 12 new · 30 already seen`; `! Partial: LinkedIn was rate-limited…` (amber, warnings only) or `× Search failed: …` (red). Errors are written in plain words (429 or "blocked" means *rate-limited*, anything else *failed*) and the next search retries what was missed. The raw technical error is kept in a collapsed **details** under the message.
- If you already searched today, SEARCH turns the panel into an inline question (`You already searched today at 09:12. Search again?` with **SEARCH AGAIN** and **CANCEL**; Escape cancels). There is no browser dialog. The server still answers `409` unless `confirm=1` is sent.
- Only one search can run at a time.
- While a search runs (also after reloading the page mid-search) the filters, export, pinned offers and the list are locked (`inert` + `aria-busy`) and dimmed, and an overlay in the middle shows a pixel loader, the step counter (`3 / 16`, one step per site, place and term), the current site, term and place (`linkedin: software engineer · Mexico`), and a progress bar. The menu still works and the button reads SEARCHING… (disabled). With *reduce motion* enabled the loader is static. If the progress check fails it says `lost connection, retrying…`. When the search finishes the page reloads and the panel shows the outcome. `GET /search/status` returns `running`, `progress`, `step`, `total`, `result` and `error`.
- The search settings are re-read from `config.toml` on every search. A typo in the file shows a notice on the page and the search refuses to start (`422`); the page keeps working with the settings loaded at startup.

## The listing

Each offer shows its title (linked to the offer's detail page), company, location, source, stack, and salary.

- **Salary** shows currency and period when the posting publishes them. Salaries found in the description text are parsed for **MXN and USD** (symbols, codes, `pesos`, `dólares`, `k` suffixes, ranges); a structured salary from the site may carry another ISO currency, which is shown as it comes. If the currency is not stated it shows `?` instead of guessing. LinkedIn rarely publishes salaries; Indeed often does (Mexico, in our experience). The **Currency** filter offers MXN and USD.
- **gross / net** is read from the posting text in Spanish and English ("bruto", "neto", "sueldo libre", "gross", "net", "take-home", "after tax"). If the posting does not say, it shows `gross/net not specified`.
- **Status**: `new`, `seen`, `applied`, `discarded`. The status survives future searches. Opening an offer's detail page turns `new` into `seen` automatically; `applied`, `discarded` and `seen` are never changed by it. Discarded offers are hidden unless you pick another status filter.
- **Work mode**: a small `REMOTE`, `HYBRID` or `ONSITE` tag next to the title. It is detected from the title and location first (Spanish and English: "Híbrido", "Desde casa", "Presencial", "Remote"...), then from the source's own remote flag, then from the description. If a posting mentions both remote and onsite work, it is treated as remote. If it mentions nothing, no tag is shown. Hybrid wins over remote when both appear in the title or location.
- **Read vs unread**: unread (`new`) rows have a bright bold title, a white dot and a bar on the left. Read (`seen`) rows are dimmed and have no marker. `applied` rows carry a white **✓ APPLIED** tag and `discarded` rows a dashed **DISCARDED** tag with a struck-through title.
- **NEW** marks unread offers first seen in the latest search; it disappears once you open the offer.
- **Possible duplicates are grouped.** The same role from the same company found on several sites, or reposted, shows as **one row** (the *representative*: the first posting in the current order among those matching your filters, so with *best match* it is the one that mentions most of your stack) plus a collapsed line under it, `▸ 2 more postings · linkedin, indeed` (add `· 1 unread` when some of them are unread). Click it (or Tab to it and press Enter or Space) to open the other postings as smaller nested rows with a thin line on the left: title, site, company, date posted (or first seen), the status selector, PIN and a link to the detail page. Each posting keeps its own status, pin, and NEW/unread marker; opening one does not change the others. If any posting of the group is unread, the representative row shows the white dot and bar too, even when the representative itself is read. Only postings that match the active filters are shown, so a group whose other postings are filtered out (or discarded, with the default status filter, or pinned) is a normal single row; it then keeps the small **possible duplicate ×N** tag. Nothing is deleted or merged. An old `?hide_dups=1` link still works and is the same as the default.

How duplicates are detected (`jobfinder/dedup.py`): company names are normalized (accents, punctuation and legal suffixes such as `S.A. de C.V.`, `Inc.`, `LLC`, `GmbH` removed) and must be at least 90% similar; titles are normalized (`sr`/`jr`/`dev`/`eng` expanded, noise words such as `remote`, `hybrid`, `presencial`, `mexico`, `cdmx` dropped), must agree on level words (`senior`, `junior`, `lead`, `II`…) and be at least 90% similar. Only offers seen in the last 90 days are compared.

## Split view (screens 1100 px wide or more)

The list keeps its filters and search panel on top; below them the page is two columns, like LinkedIn's job search. On the left is the job list (rows are compact, and the selected row has a bar on its left and a lighter background); on the right a **pane** that sticks to the top of the window and scrolls on its own. Click a job's title (or a posting inside a group) and the pane shows that job without leaving the page: title, company, location, work mode, source, salary with gross/net, posted and first-seen dates, stack (your stack highlighted), its **possible duplicates** (links that load in the pane), the actions row (status selector, PIN/UNPIN, **COPY FOR RESUME**, **Open posting ↗** in a new tab, **Open full page**) and **About the job**, the tidied description.

- The address becomes `/?<your filters>&job=<id>`, so Back and Forward step through what you looked at and the page can be shared or reloaded. Picking a job also marks it `seen` (only `new` changes), and its row's unread dot and `NEW` tag go away in place.
- The pane is the same server-rendered fragment as the detail page (`GET /jobs/<id>?pane=1`, one job query plus its duplicates; the list queries are unchanged).
- Keyboard: with focus in the list, **j / ↓** and **k / ↑** move the selection, **Esc** (or **✕ Close**) clears it. Nothing is selected when the page opens, so nothing is marked as seen by merely visiting the list.
- The status selector and PIN inside the pane post normally and return to the same `/?...&job=<id>`; choosing `applied` still takes you to the application page.
- `?job=` is a selection, not a filter: it does not count as an active filter, changing a filter drops it, and the filters remembered for the Jobs link leave it out. An unknown or invalid id is ignored.
- With a modifier key (open in a new tab), or below 1100 px, a title is a plain link to the full detail page.

## Detail page

Click an offer's title to open `/jobs/<id>` inside the app (the same job view the pane shows, on its own page). It shows the title, company, location, work mode, salary with gross/net, stack, the date posted and the date first seen, the status selector, PIN/UNPIN, and the full description.

- **Open posting ↗** opens the original posting in a new tab.
- **COPY FOR RESUME** (in the sticky header, also in the split pane) copies the job as plain text to the clipboard: `Company`, `Role`, `Location · work mode`, `Source · posting URL`, `Posted`, `Salary` (with gross/net, or `not published`; missing facts read `—`), then the tidied description as Markdown, ready to paste into a resume prompt (see [Resume tools](../README.md#resume-tools-optional)). The button says `COPIED ✓` for two seconds, or `COPY FAILED` and opens the text selected so you can copy it with Ctrl+C (Cmd+C on macOS). The text is rendered by the server into the page, HTML-escaped, never run.
- **Back to list** returns to the same filters and page you came from (the list passes its own URL as the `back` query parameter; only local paths are accepted, anything else goes to `/`).
- Changing the status or pinning from the detail page keeps you on it.
- The description is rendered as untrusted text: raw HTML is shown as plain text, never run, images are not loaded, and links with unsafe schemes such as `javascript:` are not clickable.

## Readable descriptions

Scraped descriptions are tidied into Markdown (`jobfinder/describe.py`) every time they are shown; the stored text is never changed. It:

- removes lines of spaces and blank-line runs, and undoes escaped punctuation (`\-`, `\*`, `\.`, `\(`);
- turns bullet glyphs (`•`, `·`, `▪`, `◦`, a line-leading `–`/`—`, `o Item`) into lists (sub-items stay nested) and `1)` into `1.`;
- joins list items and bold phrases that the scraper broke over several lines;
- turns short title-like lines (a fully bold line, an ALL CAPS line, or a short line ending in a colon, followed by content) into headings; ALL CAPS is written in Title Case but keeps short acronyms like PLC or SQL;
- turns `Ubicación:`, `Categoría:`, `ID de Requisición:`, `Salary:`, `Sueldo:` and similar lines at the very top into a compact key/value list;
- after a heading, turns three or more short lines in a row (the Glassdoor style, one item per line) into a bullet list.

Nothing is dropped, fenced code is left alone, and it is idempotent on text that is already good Markdown. The rendering rules above (no raw HTML, no images, safe links in a new tab) are unchanged, and the **EXPORT .MD** file uses the same tidied text (its headings one level below `### Description`).

## Best match first

The list is ordered by how many technologies of *your stack* each offer mentions (most first), then newest first. Offers that match none still appear, after the rest. Your stack is the `[profile] stack` list in `config.toml` ([configuration](configuration.md#profile)); the **sort** filter switches between *best match* (default) and *newest* (the plain newest-first order). Without a configured stack there is nothing to rank, so the list is newest first and the sort filter is hidden. In each row, the tags that are in your stack are bold with a white border, and `3/7 match` says how many of your 7 technologies the offer mentions.

## Filters

Each filter has a visible label: Search (text), Status, Source, Currency, Work mode (toggle chips: remote, hybrid, onsite, not specified; pick several to see offers with any of them, e.g. remote + hybrid), First seen (day), Posted (any time, or the last 2, 3 or 4 weeks; offers without a posting date count from the day they were first seen), Sort, Stack, and the Options checkboxes (with salary, only new).

- **Reset filters** appears at the top right of the filters only while a filter is active.
- Filters apply as soon as you change them; the text field, the stack chips and the work mode chips wait for a half-second pause, so you can pick several chips in a row. Changing a filter goes back to page 1.
- Your filters are kept while you move around the app (Jobs, the logo, Applications and back) until you close the tab; **Reset filters** and **Clear all filters** forget them.
- **PIN** / **UNPIN** keep your place: after the page reloads, the list is scrolled back so the rows you were looking at stay under the pointer.

## Stack filter

Every technology found in your offers is a clickable chip, and you can select several. A chip's count is how many offers (groups of duplicates count once) the list would show for that chip under the other active filters (day, mode, status, source…), so it always matches what you get when you select it; chips with no offers under the current filters stay visible with a dashed border. An offer is shown when it mentions **any** selected technology (`?stack=kotlin&stack=rust`, case-insensitive; a single `?stack=Spring` still works). Selected chips are filled, bold and carry a ✓, so the state never depends on colour alone. Your stack comes first, in the order of your config, then the most common technologies; the rest are under **more**, which opens by itself when one of its chips is selected. **clear** next to *Stack* drops the selection only; **Reset filters** clears every filter. Page links keep every selected chip. The filter and the ranking read an indexed `job_stack` table (one row per offer and technology), not a text search over the stack column, so they stay fast with tens of thousands of offers.

**Stack detection** (`jobfinder/enrich.py`) uses a fixed list of known technologies. It knows about Express: `Express.js`, `ExpressJS` and `Express JS` always count, and a bare capitalized `Express` counts only when the same posting mentions Node (so "express your ideas" or "American Express" do not).

## Pagination

The list shows 20 offers per page (a group of duplicates counts as one), with PREV / `Page X of Y` / NEXT below it. The page is the `page` query parameter, and the links keep every active filter. A bad page number falls back to page 1, and one past the end shows the last page. The `N offers` count is the number of groups matching your filters, not just the current page, and says `(M postings)` after it when duplicates are grouped (`1141 offers (1416 postings)`). The page is chosen with a window function (`ROW_NUMBER() OVER (PARTITION BY the group …)`) and the nested postings of the page come from one more query, so the cost does not grow with the number of groups. Applying the filters starts again at page 1, and changing an offer's status keeps you on the same page.

## Pinning

Press **PIN** on an offer to keep it at the very top, in a **PINNED** section above the list on every page. Pinned offers ignore the filters (status and the rest) and are never grouped; if a pinned offer has duplicates, the others still group in the list, so they never disappear, and the most recently pinned comes first. They are not repeated in the paginated list and do not count toward its 20 per page or its total. Press **UNPIN** to send one back. Pinning does not change the offer's status.

## Database upgrades

Databases from older versions are upgraded automatically the next time the app opens them. Upgrades are additive and idempotent (see `_migrate` in `jobfinder/db.py`); nothing is dropped, and one-time backfills are guarded by flags in the `meta` table.

- `pinned_at` column (pinning) and `work_mode` column; `work_mode` is filled in once from the stored title, location and description of the offers you already have.
- `job_stack` table: the stack of every stored offer is extracted again once from its description plus the skills already saved (so technologies added to the detector since, like Express, are picked up). This rewrites only the derived `stack` column, never descriptions, statuses or anything else.
- Applications tables, phases, `timeline_entries.phase_id` and an index for the board: see [applications.md](applications.md#database-upgrades).

## Export for red-flag review

Pick a day and press **EXPORT .MD**, or run `job-finder export --date YYYY-MM-DD`. The file includes the full (tidied) description of every offer first seen that day, and a `Work mode` line (`remote`, `hybrid`, `onsite` or `not specified`), so it can be pasted into a chat to look for red flags (low pay for the requirements, long hours, years of experience that don't match the salary). Manual jobs appear in the export of the day you added them.

## Install as an app (PWA)

job-finder is a PWA, so you can open it in its own window instead of a browser tab. Start the server first (`docker compose up -d`, or `.venv/bin/job-finder serve`), then:

- **Chrome / Edge** (Windows, macOS, Linux): open http://127.0.0.1:8000 and click the install icon in the address bar, or use the browser menu → **Install page as app** / **Install job-finder** (Edge: **Apps** → **Install this site as an app**).
- **Safari (macOS)**: open the page and choose **File → Add to Dock**.
- Browsers without PWA install support (for example Firefox on desktop) can simply keep the page open in a tab.

The app only works while the server runs. If it is not running, the window shows a short page saying how to start it; reload once it is up. Nothing from your data is cached offline (only that page, the styles, fonts and icons), so what you see always comes from the server. Links to job postings and CVs still open in your normal browser.

To regenerate the icons (`jobfinder/static/icons/`), run `scripts/make_icons.sh` (needs Chrome or Chromium; see the [README](../README.md#development)).

## Sources

`sites` in `config.toml` lists where to search: `linkedin`, `indeed` and `glassdoor`. Indeed and Glassdoor take their country from `country_indeed` (with `"Mexico"` Glassdoor uses `glassdoor.com.mx`). The source filter shows every configured site, any other source already stored, and `manual`.

Glassdoor matches terms loosely (a "software engineer" search also brings support or CNC roles), and some of its salaries are placeholders such as `MXN 1 / month`; use the stack chips and the text filter to narrow it.
