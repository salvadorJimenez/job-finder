# Applications, phases and daily goal

Detailed reference for the application tracker. For the offers list see [features.md](features.md); for `config.toml` see [configuration.md](configuration.md). Back to the [README](../README.md).

Contents: [Applications and phases](#applications-and-phases) · [The board and the list](#the-board-and-the-list) · [Filters and sort](#filters-and-sort) · [The application page](#the-application-page) · [Adding an application by hand](#adding-an-application-by-hand) · [Importing past applications](#importing-past-applications) · [Daily goal](#daily-goal) · [Activity calendar](#activity-calendar) · [Database upgrades](#database-upgrades)

## Applications and phases

Marking an offer `applied` (from the list or the detail page) creates its **application** in the first phase, dated now, with a timeline entry recording the move, and takes you straight to that application's page so you can attach the CV you sent. Marking it `applied` again does nothing, and changing the status later never deletes the application or its timeline.

The header has a **Jobs** / **Applications** nav. **Applications** opens the board (see below); the phase settings live at `/applications/phases`, linked from the board as **Phases**:

- **Default phases**: Applied, Recruiter screen, Hiring manager, Technical, Final, Offer. They are created once, and never again after you delete them.
- **Add** a phase: 1–40 characters, unique ignoring upper/lower case; it goes at the end.
- **Rename** a phase: type the new name in the box on its row and press **SAVE** (`POST /applications/phases/{id}/rename` with `name`; it works without JavaScript). Same rules as Add: 1–40 characters (trimmed), unique ignoring upper/lower case, except that a phase may keep its own name or change only its capitalization; otherwise nothing changes and you see the error. The new name shows everywhere (board, **Move to**, application page, filters) and **past timeline entries of that phase show it too**, including the automatic "Moved to …" text. Only entries linked to that phase are rewritten (see below); entries of a removed phase, or that could not be linked, keep their name.
- **Remove** a phase: refused with "Move the N applications in X to another phase first" while applications are on it. Past timeline entries keep the phase name. If there are no phases at all, new applications are *unassigned*.
- **Reorder** with the ▲ / ▼ buttons. `POST /applications/phases/order` (`order=3,1,2`) reorders in one go and must list every phase exactly once.
- Each application also has an **outcome** (`active`, `accepted`, `rejected`, `withdrawn`, `ghosted`), separate from its phase.

Each timeline entry records which phase it belongs to (`timeline_entries.phase_id`) next to the name text (`phase_name`, a snapshot), which is how a rename knows exactly what to rewrite. Removing a phase keeps its entries and their name but clears that link, so a later phase with the same name (or id) does not inherit them on a rename. Editing and saving such an entry from the application page links it to whichever phase you pick in its form.

## The board and the list

`/applications` shows your applications in one of two views, chosen with the **BOARD | LIST** switch at the right of the filter bar (`?view=list`; the board is the default). Both views share the same filters and sort, described [below](#filters-and-sort).

From top to bottom the page has the title row, the **activity calendar** ([below](#activity-calendar)), the **filter bar** and the results. The title row shows the overall counts (`18 active · 61 closed`, never changed by filters) and the actions: **Hide closed** / **Show closed** (a shortcut for the outcome filter `active`; the old `?closed=0` link still works), **Phases** and **Add application**. With no applications the page says so and points to marking a job as applied or **Add application**.

### Board

One column per phase in your order, then a final **Closed** column. An **Unassigned** column appears only while some matching active application has no phase (for example when you had no phases defined).

- The board page is wider than the rest of the app (up to 1600px, the header follows it). **Columns with cards share the width (146–300px each)**, so all seven phases plus Closed fit side by side down to about 1280px even when every one has cards; as a column narrows its cards get more compact (smaller type, the company wraps to two lines, tighter tags; done with a container query). **Empty columns collapse to a narrow strip** with the phase name written vertically and its `0`. Only when the columns no longer fit (about 1210px for eight) does the board scroll sideways, with a thin dark scrollbar; on phones the columns stack one under another instead.
- **A long column scrolls on its own**: each column's cards are capped at about the window height (minus the header, at least 360px) with a thin dark scrollbar, so Closed with dozens of cards does not make the page endless and the other columns stay in view.
- Each card is compact: the job title (link to the application page, two lines at most), then one line with company, work mode tag and `Nd ago` (hover it for `N days since applied`), and the next upcoming interview, if any: the earliest timeline note dated now or later (date and time, or nothing). A small **Move to** phase selector shows when you hover or focus the card, and always on touch screens and narrow windows.
- **Drag** a card onto another phase column to move it, also into a column that scrolls or one that is collapsed; the move is recorded in its timeline. While you drag, collapsed columns open up as drop zones, and the one under the card opens fully. Dropping on the same phase does nothing, and Closed / Unassigned are not drop targets. Without JavaScript (or on touch screens), the **Move to** selector does the same through a normal form post.
- Applications whose outcome is not `active` appear **only** in Closed, which collapses like any other column when it is empty. Closed is left out when the outcome filter is only `active`.
- Filters apply to the board too: columns show only matching cards, their counts are the filtered counts, and the sort orders the cards inside each column.

### List

A dense table, one row per application (closed ones included), with a sticky header and thin lines instead of stripes. **The whole row is the link** to the application page (the role link stretches over it, so there is one tab stop per row, with a visible focus outline and a hover highlight). Rejected, withdrawn and ghosted rows are dimmed (accepted ones are not):

| Column | Shows |
| --- | --- |
| Role / company | the role in bold (the link), the company muted under it. The header sorts by company A–Z |
| Phase | the phase name and a small stepper of its position among your phases (`Phase 4 of 6`, also read aloud); `Unassigned` has none |
| Outcome | a badge with a glyph and the word: `● active`, `✓ accepted`, `× rejected`, `↩ withdrawn`, `… ghosted`, each with its own border style |
| Applied | relative: `today`, `12d ago`, and `2mo ago` from 60 days on; hover for the exact date (`2026-10-20 · 15 days ago`) |
| Last activity | the short day (`Oct 25`, with the year only when it is not this one; the full date in the tooltip) and the text of the latest timeline entry that already happened (`→ Technical` for a move, the first line of a note); entries dated in the future are not activity yet |
| Next interview | the earliest note dated now or later, in amber with the weekday: `Thu Nov 14 · 15:00` (`—` when there is none) |
| Source | a small tag: `linkedin`, `indeed`, `glassdoor`, `manual` |
| CV | a document icon with the file name in its tooltip (and for screen readers), or `—` |

Click **Role / company**, **Phase**, **Applied** (flips between newest and oldest), **Last activity** or **Next interview** in the header to sort by it: the sorted column shows ▲ / ▼ (and `aria-sort`), the others a quiet ↕. Under about 860px each row becomes a small card (role, company and outcome on top, then the other fields with their names as labels; empty next interview and CV are left out).

## Filters and sort

One toolbar row, search first: **search field** (⌕, placeholder `Search company, role or notes`, a clear **×** while there is text, and **`/`** focuses it from anywhere that is not a text field), then the dropdowns **Outcome**, **Phase**, **Source**, **Date** and **More**, then **Sort** and the **BOARD | LIST** switch on the right. Each dropdown is a native `<details>` popover: **Escape** closes it (focus returns to its button), so does a click outside, and opening one closes the others. A dropdown with something selected shows a **count badge** (`Outcome 2`) and a brighter border. While you pick several values in one dropdown it stays open after the page reloads. On narrow screens the search takes the full width, the dropdown buttons wrap and the popover spans the width.

Below the bar, one line holds the **result count** (`16 of 79 applications`, always) and, only while a filter is active, one **removable pill per value** (`“spring” ×`, `rejected ×`, `Technical ×`, `Source: indeed ×`, `CV missing ×`, `Upcoming interview ×`, `Last 30 days ×`, `Since 2026-10-01 ×`, `Until 2026-10-30 ×`, `Day Wed Oct 14 ×`) and **Clear all**. A pill is a plain link to the same page without exactly that value, so it works without JavaScript; **Clear all** keeps the view and the sort. With no results the pills stay so you can undo one.

Filters apply as soon as you change them (the text field and the outcome / phase chips wait for a half-second pause, so you can pick several in a row), combine with **AND across groups**, and chips within a group combine with **OR**. A value the page does not know (an old link with a removed outcome, a bad date or day, an odd window) is ignored instead of hiding everything. Everything is a plain `GET` form, so it also works without JavaScript (a **FILTER** button appears).

- **Search**: company, role or the notes of the timeline, case-insensitive (also for accents: `sénior` finds `SÉNIOR`); `%` and `_` are searched literally. Automatic move entries are not searched.
- **Outcome** (dropdown): toggle chips `active`, `accepted`, `rejected`, `withdrawn`, `ghosted`. Each chip shows how many applications it would give under the other filters.
- **Phase** (dropdown): toggle chips for your phases plus **Unassigned** (shown while some application has no phase). The phase of a closed application is the one it was on.
- **Source** (dropdown): only sources that have applications.
- **Date** (dropdown): **Applied** quick ranges (any time, last 7 / 30 / 90 days, counted in local days like `Nd ago`), **From** and **To** (inclusive dates) and, while one is active, the selected **day** with a way to clear it.
- **More** (dropdown): **CV** (any / attached / missing) and **upcoming interview only**.
- **Day** (`?day=2026-10-14`, set by clicking a day in the activity calendar, no dropdown of its own): applications **applied that day, or with any timeline entry dated that day** (a note, an upcoming interview or a phase move), in local time. An invalid day is ignored.
- **Sort**: applied newest (default), applied oldest, last activity (latest first, none last), next interview (soonest first, none last), company A–Z (unknown companies last), furthest phase (Offer first, unassigned last). Ties fall back to newest applied, then newest id, so the order is stable.
- The view, filters and sort are in the URL and are **kept for the tab**: the header **Applications** link, the logo/Jobs round trip and every **Back to board** link return to the same view with the same filters until you close the tab or press Clear all. Repeated parameters (`?outcome=rejected&outcome=ghosted&phase=4`) are the shareable form.

Filtering, the latest and next timeline entries and the sort all run in a single SQL statement (the timeline is aggregated once, not per application), helped by an index on `timeline_entries(application_id, happened_at)`. Stored times are compared as local times: those with an offset (moves, applied dates) are converted with the current UTC offset, plain ones (notes you type) are taken as local.

## The application page

`/applications/<id>` puts the **timeline** in the wide left column (the focus of the page) and a sticky sidebar on the right with the phase and outcome selectors (`active`, `accepted`, `rejected`, `withdrawn`, `ghosted`; outcome is separate from phase, so "rejected at Technical" keeps both), the applied date, **Job details**, **Open posting ↗** (only for `http`/`https` links) and the CV card. Under about 860 px it is a single column: summary, timeline, CV.

- **Timeline** (newest first, grouped by day, a vertical line with a marker per entry): **+ ADD ENTRY** at the top opens an inline form with the phase (defaults to the application's current phase), the date and time (defaults to now, with seconds: type `HH:MM` and the seconds are `:00`; notes in the same minute are ordered by their seconds, then by the order you wrote them) and notes (up to 5000 characters, the box grows as you type; **CANCEL** or Escape closes it). Notes entries are prominent; those dated now or later are listed first under **UPCOMING** in amber. Every entry shows its time with seconds (`14:09:40`, or `Nov 10 · 14:09:40` for an upcoming one), so notes written in the same minute can be told apart; an entry saved without seconds shows `:00`. Automatic phase moves are compact muted one-liners (`→ Hiring manager 09:29:07`). Notes are plain text: HTML and Markdown are shown literally, line breaks are kept. **EDIT** and **DELETE** are quiet buttons that show on hover or keyboard focus (always visible on touch screens and narrow windows); DELETE asks for a second click (**CONFIRM?**) when JavaScript is on. Notes entries can be edited, move entries only deleted. Entries keep the phase name even if the phase is removed later. Without JavaScript the composer and the edit forms are plain `<details>` forms.
- **CV sent**: a compact card. With no CV it is a drop zone (**Drop your CV here**, PDF or DOCX up to 10 MB) plus a collapsed **use one you uploaded** list of CVs already stored for other applications; with a PDF it shows a small **preview** of its first page (drawn by the browser's own PDF viewer; click it, the file name or **OPEN** to read the whole CV in a modal with **Open in new tab ↗** and **CLOSE**; Escape or a click outside also close it, and the full viewer only loads when it opens), the file name, **OPEN** and a quiet **Replace**. Without JavaScript, or with a ctrl/cmd-click, the PDF opens in a new tab. A `.docx` has no preview and shows **DOWNLOAD** instead, since browsers cannot display Word files.
- **CV upload rules**: CV responses are only allowed to be framed by the app itself (`frame-ancestors 'self'`). **Drag a file anywhere on the page** (a full-page "Drop to attach CV" overlay appears) or drop it on the zone, or press browse: it uploads at once, shows "Uploading…" and reloads with the CV attached. The type (`.pdf`/`.docx`) and the 10 MB limit are pre-checked in the page, but the server is the source of truth and its message is shown on a refusal. Without JavaScript the file input and the **ATTACH** button submit the same form. The extension and the file's first bytes must both match (`%PDF-` for PDF, a ZIP header for DOCX), so a renamed executable is refused. Files are stored under `cvs/` next to the database (`data/cvs/` by default) as `<sha256>.<ext>`: the name you uploaded is never used in a path, it is only kept for display and for the download name, and identical files are stored once (`/cvs/<id>`, `/cvs/<id>/view`).

## Adding an application by hand

**Add application** on the board (`/applications/new`) is for jobs found outside this tool: title and company (required, up to 200 characters), link (optional, `http`/`https` only), location, work mode, salary as free text, and the applied date (defaults to today). It creates a job with source `manual` (listed in the main list and available in the source filter, never marked NEW) and its application in the first phase. The salary text is saved in the description as `Salary: ...`; no structured salary is invented.

Manual jobs need a `first_seen_run`, so the first one creates a single bookkeeping row in `runs` with status `manual`. Searches only look at `success` / `partial` runs, so it is never shown as "Last searched" and does not affect search ranges. A scraped offer that matches a manual one can still be marked *possible duplicate*, and manual jobs appear in the export of the day you added them.

## Importing past applications

`scripts/import_applications.py` loads applications you made before using the tool, from a JSON file, as manual applications with their phase, outcome and dated timeline. Run it without `--apply` first: it only prints what it would do. Records are keyed by `ref`, so running it twice never creates duplicates. The accepted JSON shape is documented in the script's docstring.

```bash
.venv/bin/python scripts/import_applications.py my-applications.json            # dry run
.venv/bin/python scripts/import_applications.py my-applications.json --apply    # write them
```

## Daily goal

A goal of applications per weekday (Monday to Friday; **5 by default**) is shown in the header of every page, so you always see how many you have sent today:

- **`TODAY 3/5`** with a 5-segment meter (a segment is filled per application) while the goal is not met; **`✓ TODAY 5/5`** once it is (more than the goal shows as `7/5`). On Saturday and Sunday there is no goal: it shows **`WEEKEND · 2 applied`** and no meter. Larger goals scale the meter to at most 10 segments.
- **`STREAK 4`** appears next to it when the streak is at least 1.
- Set it in `config.toml`; `0` turns the whole feature off (nothing is shown). It is read when the server starts, like the stack.

```toml
[goal]
daily = 5
```

**What counts.** Every application counts for the *local* calendar date of its applied date: the moment you marked the job `applied`, or the date you chose when adding it by hand. Applications are never deleted, so changing a job's status back later does not lower the count. Applications made on a weekend are shown and counted in the totals, but never add to the streak.

**Streak.** The number of consecutive weekdays that met the goal, counting back from today. Today counts once it is met; while it is still short it does not break the streak (it is in progress), so the count starts from the previous weekday. Weekend days are skipped: not applying on a Saturday or Sunday never breaks the streak. It stops at the first weekday below the goal.

"Local" means the time zone of the process: your system time zone, or `TZ` inside Docker (see [Docker](../README.md#quickstart-docker)).

## Activity calendar

Above the filters, `/applications` shows a GitHub-style calendar of the job hunt: one column per week (oldest on the left, this week at the right), rows Monday to Sunday (labels for Mon / Wed / Fri and the months), weekend rows dimmer, today outlined. It starts on the Monday of your first activity (at least 8 weeks, at most 26; wider than the screen it scrolls sideways, starting at the newest week) and ends on this week's Sunday, or on the week of the furthest upcoming interview if that is within 8 weeks.

- **Intensity** (five grey levels: 0, 1, 2, 3–4, 5+) is the day's events: applications made that day + timeline notes that already happened + phase moves. An application's own `Applied` move is the application, not a second event; interviews still in the future are not activity yet.
- **Markers** on top, different by shape and not only by colour: a **dot** for an interview or process note (a note dated after the day its application was made; notes written on the day you applied are application notes), a boxed **×** for a rejection, and an **amber outline** for a day with an upcoming interview (future days included). A rejection is *inferred* from the note text: it contains `reject`, `not moving forward`, `not continuing`, `not advancing` or `not progressing`, or in Spanish `rechaz…`, `no pasé`, `no continúo`, `no avancé`, `no seguí`, `descartado`, `no fui seleccionado` (case and accents ignored; the list is `goal.REJECTION_PHRASES`). Outcome changes are not dated in the database, so they cannot be shown.
- **Tooltip and accessible name** per day: `Tue Mar 10 · 1 application (Acme) · 2 notes (Globex: Pairing session…; …) · 1 rejection · 1 phase move`, up to three snippets and `+N more`; `no activity` for an empty day.
- **Click a day** with activity to filter the board or list to it (the `day` filter above, shown as a pill); click it again, or the pill's ×, to clear it. The view, the other filters and the sort are kept.
- **Legend**: `Less ▢▢▢▢▢ More` and the marker keys. Next to the title (so it stays when the section is folded): **`this week`** (since Monday), **`30 days`** (the last 30) and **`days met X/Y`** weekdays (today is left out of Y while it is still short); these totals are part of the daily goal and disappear when it is off (`daily = 0`), while the calendar stays.
- The **Activity** header folds the whole section (**Hide ▴** / **Show ▾**); the choice is remembered in this browser (`localStorage`). It starts open, and closed on a phone until you open it. Without JavaScript it is a plain open `<details>`.

The calendar is built from two queries over all applications and timeline entries, grouped by local date, with the same date logic as the day filter, so a cell and its click always agree.

## Database upgrades

Existing databases get the new tables (`phases`, `applications`, `timeline_entries`, `cv_files`, `meta`) added the next time the app opens them, plus the default phases. Offers already marked `applied` get an application in the first phase, dated at the **moment of the upgrade** (the real application date was never recorded). Nothing is deleted or rewritten.

The `timeline_entries.phase_id` column is added the same way; the entries you already have are linked **once**, by name, only when it matches exactly one current phase (ignoring upper/lower case and surrounding spaces). Entries matching no phase or several stay unlinked. This never changes any `phase_name` or deletes anything. The index on `timeline_entries(application_id, happened_at)` is also created on open.
