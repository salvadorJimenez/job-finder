# Configuration

job-finder reads one TOML file, normally `config/config.toml` (a `config.toml` in the folder you run it from takes precedence, see [Where the config file is](#where-the-config-file-is)). Start from the example, where every key is commented:

```bash
mkdir -p config && cp config.example.toml config/config.toml
```

For Docker, create the data folders in the same step, before `docker compose up -d --build`:

```bash
mkdir -p config data exports && cp config.example.toml config/config.toml
```

On Windows (cmd.exe): `mkdir config` (skip it if the folder exists) and `copy config.example.toml config\config.toml`.

The config is **private**: git ignores `config/` and `config.toml`, so your searches, stack and paths never end up in a commit. [`config.example.toml`](../config.example.toml) is the tracked template. Back to the [README](../README.md).

## Where the config file is

- Without `--config`, the app uses `config.toml` in the current folder if it exists, otherwise `config/config.toml`. A root `config.toml` still works locally, and it wins if both exist.
- `job-finder --config path/to/other.toml <command>` uses exactly that file (the option goes before the subcommand).
- Docker mounts the `config/` **folder** at `/app/config`, so the container reads `config/config.toml`; it does not see a root `config.toml`. A folder (unlike a single-file mount) still shows the file after an editor replaces it (atomic save, `sed -i`), so every edit is seen on the next search without restarting. To move an existing root config: `mkdir -p config && mv config.toml config/config.toml` before `docker compose up -d --build`; its content and your data stay as they are.
- **Relative paths** in `[storage]` (`database`, `exports_dir`) are relative to the config file's folder, except when the file is inside a folder named `config`: then they are relative to the project folder (the parent of `config/`). So `data/` and `exports/` stay at `./data` and `./exports` with either layout. Absolute paths always win.

If neither default file exists, every CLI command (including `serve`) prints a one-line hint and exits with status 1: `config file not found: looked for config.toml and config/config.toml. Create it with: mkdir -p config && cp config.example.toml config/config.toml`. In Docker the container exits with that message and restarts until `config/config.toml` exists; read it with `docker compose logs`. If the file breaks or disappears while `serve --reload` is running, the page shows the error and recovers by itself once the file reads again.

## What is read when

| Section | Read |
|---|---|
| `[search]` | **on every search**: edit it while the server runs and the next search uses it. A typo is reported on the page and the search does not start. |
| `[profile]`, `[goal]`, `[storage]`, `[web]` | when the server starts: restart it (or use `serve --reload`, or `docker compose restart` in Docker) after editing. |

## First try: what to edit first

The example config works unchanged: copy it and press **SEARCH**. Once you want results that fit you, edit these keys, in this order:

1. **`[search] terms`**: the job titles or keywords to look for. Keep the list short (see [Terms, places and sites multiply](#terms-places-and-sites-multiply)).
2. **`[search] location` and `country_indeed`**: where to look. Change them together, with the full `City, State, Country` form for `location` (see [Locations](#locations)).
3. **`[profile] stack`**: your technologies, so offers that mention more of them are listed first. Read when the server starts, so restart it after editing.

`[search]` edits apply to the next search without a restart. Leave the rest (`sites`, `results_wanted`, `pause_seconds`) alone until you have run a search.

## `[search]`

| Key | Default | Meaning |
|---|---|---|
| `terms` | required | Words to search for. Every term runs on every site and in every place. |
| `sites` | required | Any of `"linkedin"`, `"indeed"`, `"glassdoor"`. |
| `location` | required | One place (`"Mexico"`) or an ordered list of places. |
| `country_indeed` | required | Country used by Indeed and Glassdoor (JobSpy's `country_indeed`). |
| `results_wanted` | `30` | Results requested per term, place and site. |
| `first_search_days` | `7` | How many days back the very first search of a site looks. |
| `overlap_hours` | `6` | Extra hours added to the "since last search" window; duplicates are absorbed. |
| `pause_seconds` | `3` in code, `30` in the example | Pause between requests to the same site. |

### Terms, places and sites multiply

Each search makes one request for every combination of **term × place × site**. Three terms in two places on three sites is 18 requests, each followed by a pause. The progress line names each step (`linkedin: software engineer · Mexico`) and the counter shows `step / total`.

### How long a search takes

Plan for **several minutes**. A search runs its requests one after another, and `pause_seconds` is waited between consecutive requests to the same site. Each request also fetches the description of every offer it finds, which adds to the time. With the example config (3 terms, 1 place, 3 sites = 9 requests, `pause_seconds = 30`) the pauses alone add about 3 minutes (2 per site), and the scraping on top of that makes it roughly **5 to 10 minutes**. This is an estimate: it depends on the sites, your connection and how many offers match. The first search, which looks back `first_search_days` days, can take longer; later searches cover only what is new.

While it runs, the page shows an overlay with the step counter and the current `site: term · place`, and reloads by itself when the search ends, showing the outcome. `job-finder search` in the terminal prints one `searching site: term · place` line per step and a final summary. More terms, places, sites or `results_wanted` make it longer, and `pause_seconds` is the other lever: lowering it speeds the search up but raises the chance of being rate limited.

### Locations

`location` is a single place or a list searched in order:

```toml
location = "Mexico"
# or, a city first and then the whole country (an example of the list form):
location = ["Monterrey, Nuevo León, Mexico", "Mexico"]
```

- Every term runs in each place, and a job found in more than one place is stored once.
- A country-wide search ranks every city together and keeps only the first `results_wanted` per term, so a city you care about gets more results from its own entry.
- Write the full `City, State, Country` form: it avoids places with the same name elsewhere (Córdoba exists in Mexico, Argentina and Spain), and Glassdoor needs a location it can resolve.
- An empty list or a blank place is a config error: the page shows it and refuses to search, and terminal commands stop with it.

### Country and market

The defaults target **Mexico / Latin America**: the example uses `location = "Mexico"` and `country_indeed = "Mexico"`. To search somewhere else, change both, for example `country_indeed = "Colombia"` or `"Spain"` with a matching `location`. Be aware of what is tuned for Mexico/LatAm today:

- Salary parsing recognizes **MXN** and **USD** in posting text (`$`, `US$`, `MXN`, `pesos`, `dólares`…), and the **Currency** filter offers those two. Other currencies in structured site data are shown as their code.
- Work mode and gross/net detection understand **Spanish and English** (`híbrido`, `presencial`, `bruto`, `neto`, `remote`, `gross`…).
- Duplicate detection ignores Mexican company suffixes (`S.A. de C.V.`, `S. de R.L.`) and noise words such as `mexico` and `cdmx`, next to the English ones.
- Indeed and Glassdoor country comes from `country_indeed`; LinkedIn uses `location`.

Elsewhere the app still works; those heuristics just find less (for example, a salary in another currency shows without a parsed amount).

### Rate limiting

Sites rate limit scrapers; LinkedIn starts after a few pages. When a site answers a rate limit (HTTP 429 or "blocked"), the offers it returned before the block are kept, and the search sends **no more requests to that site** (no further pauses either; the progress counter simply ends short) and carries on with the next site. The site is reported as *rate-limited* (a *partial* search, or *failed* if every site failed), the raw error says how many requests were skipped, and the next search retries the site's whole time window. Other errors only skip that one request. If searches start failing:

- raise `pause_seconds`;
- lower `results_wanted`;
- use fewer terms or places (each one repeats every request).

Keep the lists short. More requests means a higher chance of being blocked.

## `[profile]`

```toml
[profile]
stack = ["Python", "Django", "TypeScript", "React", "PostgreSQL"]
```

Optional. Your stack, by the names shown on the offers' tags (`Node.js`, `PostgreSQL`, `TypeScript`…); case does not matter. Offers that mention more of your stack are listed first and the matching tags are highlighted. Leave it empty to turn ranking off (the list is then newest first). The values above are only an example; use your own technologies. See [Best match first](features.md#best-match-first).

## `[goal]`

```toml
[goal]
daily = 5
```

Applications to send on every weekday. Optional (default `5`); `0` turns the daily goal and streak off. See [Daily goal](applications.md#daily-goal).

## `[storage]`

```toml
[storage]
database = "data/jobs.db"
exports_dir = "exports"
```

The SQLite database (every offer and application; attached CVs live in `cvs/` next to it) and the folder where `job-finder export` writes its Markdown files. Both folders are git-ignored and created on first use.

Relative paths are relative to the config file's folder, except for a file inside a folder named `config`, where they are relative to the project folder: with `config/config.toml`, `data/jobs.db` is `<project>/data/jobs.db`, the same place as with a root `config.toml`. Absolute paths are used as they are.

## `[web]`

```toml
[web]
host = "127.0.0.1"
port = 8000
```

Address and port of the local page. Keep `127.0.0.1` so it stays private to this machine. `serve --host` and `--port` override these values for one run. In Docker the container listens on all interfaces but `compose.yaml` publishes it on `127.0.0.1` only.
