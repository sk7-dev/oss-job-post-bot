# Job Watcher Bot

A lightweight Python bot that watches multiple career sites, filters matching roles, sends new job alerts to Discord, and publishes a Next.js dashboard of everything it found.

This is a clean template: no one's personal job search data, filters, or secrets are baked in. Everything under "Data files" below is a placeholder you populate yourself.

## What it does

* Fetches jobs from supported source types:
  * Greenhouse
  * Lever
  * Ashby
  * Workday
  * Phenom embedded pages
  * Entertime
  * GovernmentJobs
  * Robert Half
  * ADP
  * KPMG
  * Custom HTML (currently: Petco)
* Filters roles by title keywords, locations, and excluded keywords
* Tracks previously seen jobs in `state_seen.json` so you're only alerted once per job
* Sends alerts for new matching roles to a Discord webhook
* Runs on a schedule via GitHub Actions and commits its own state back to the repo
* Exports snapshot JSON to `dashboard_data/` that a Next.js dashboard (in `dashboard/`) reads and renders — job list, source health, scan activity, filters in use

## Repo layout

```
watcher.py              # main bot logic — fetch, filter, dedupe, alert
dashboard_export.py     # writes dashboard_data/*.json after each run
config.json             # YOUR sources + filters (sample data — replace)
state_seen.json         # job IDs already seen (starts empty)
dashboard_data/         # snapshots the dashboard reads (starts empty)
tests/                  # unit tests for dashboard_export.py
.github/workflows/      # scheduled GitHub Action that runs the bot
dashboard/              # Next.js dashboard app
```

## Quick start (bot only, no dashboard)

1. Clone this repo.
2. `pip install -r requirements.txt`
3. Edit `config.json` — see "Configuring sources and filters" below. It ships with 3 working sample sources (GitLab/Greenhouse, Notion/Ashby, Salesforce/Workday) so you can run it immediately and see how the format works, but you'll want to replace them with companies you actually care about.
4. (Optional) Set `DISCORD_WEBHOOK_URL` as an environment variable if you want Discord alerts. Without it, the bot still runs, prints results, and updates `state_seen.json` / `dashboard_data/` — it just skips sending anything.
5. Run it: `python watcher.py`

Run it again and you'll see `New jobs: 0` for anything already recorded in `state_seen.json` — that's the dedupe working.

## Configuring sources and filters

`config.json` has two sections:

**`filters`** — applied to every source:
* `title_keywords_any` — a job matches if its title contains at least one of these (case-insensitive substring match)
* `locations_any` — a job matches if its location contains at least one of these
* `excluded_keywords_any` — a job is dropped if its title, location, or department contains any of these
* Leave a list empty (`[]`) to skip that filter entirely

**`sources`** — an array of career sites to watch. Each entry needs `name`, `type`, and type-specific fields:

| type | required fields | notes |
|---|---|---|
| `greenhouse` | `board_token` | Find it in the company's Greenhouse job board URL: `boards.greenhouse.io/<board_token>` |
| `lever` | `company` | From `jobs.lever.co/<company>` |
| `ashby` | `organization_key` | From `jobs.ashbyhq.com/<organization_key>` |
| `workday` | `url` (or `tenant`+`site`+`base_url`), optional `search_text`, `limit` | `url` is the public careers URL, e.g. `https://<tenant>.wd#.myworkdayjobs.com/<site>` |
| `phenom_embedded` | `url` | URL of the embedded search-results page |
| `entertime` | `base_url`, `company_id`, optional `lang` | |
| `governmentjobs` | optional `agency`, `search_text`, `max_pages` | |
| `roberthalf` | optional `keywords`, `location`, `max_pages` | |
| `adp` | `domain` | ADP career site subdomain |
| `kpmg` | optional `keyword`, `max_pages` | |
| `custom_html` | `url`, `site` | Only `site: "petco"` is implemented; add a new `fetch_<site>_html` function in `watcher.py` for others |

To find the right identifiers for a company, open their careers page, watch the Network tab for the API calls their job board makes (Greenhouse/Lever/Ashby/Workday all expose their own JSON APIs), or just look at the URL structure — most of the fields above come straight from the public careers URL.

## Deploying the scheduled bot (GitHub Actions)

The included workflow (`.github/workflows/watch-jobs.yml`) runs the bot every 10 minutes, commits `state_seen.json`, `dashboard_data/`, and `config.json` back to the repo, and requires no server of your own.

1. Push this repo to your own GitHub repository.
2. In the repo, go to **Settings → Secrets and variables → Actions** and add a secret named `DISCORD_WEBHOOK_URL` (skip this if you don't want Discord alerts — the workflow runs fine without it, it just won't deliver anything).
3. Make sure **Settings → Actions → General → Workflow permissions** is set to "Read and write permissions" — the workflow commits state back to the repo.
4. That's it. The workflow will start running on its schedule, or trigger it manually from the Actions tab (`workflow_dispatch`).

To get a Discord webhook URL: in Discord, go to the target channel's **Settings → Integrations → Webhooks → New Webhook**, then copy its URL.

## Deploying the dashboard (optional)

The dashboard is a Next.js app that reads `config.json` and `dashboard_data/*.json` directly from your GitHub repo at runtime (via the GitHub raw content / API — not bundled at build time), so it stays in sync with whatever the bot last committed without needing a rebuild.

1. `cd dashboard && npm install` to develop locally, or deploy to Netlify/Vercel/etc.
2. Copy `dashboard/.env.example` to `dashboard/.env` (for local dev) or set the same variables in your host's environment settings:
   * `GITHUB_OWNER` — your GitHub username or org
   * `GITHUB_REPO` — this repo's name
   * `GITHUB_BRANCH` — usually `main`
   * `GITHUB_TOKEN` — **required** if the repo is private (server-side only, never exposed to the browser). Also required if you want the dashboard's "Scan now" button to work, since that triggers the GitHub Action via the API — it needs a token with `Actions: write` (fine-grained) or the `repo` scope (classic). Leave blank for a public repo if you don't need the scan button.
3. `netlify.toml` is included and pre-configured for Netlify (`@netlify/plugin-nextjs`, and a build-skip rule so the bot's own commits to `state_seen.json`/`dashboard_data/`/`config.json` don't trigger unnecessary rebuilds — the dashboard picks those up live anyway).

**Never commit a real `dashboard/.env` file with a live token.** It's already gitignored; keep it that way.

## Security notes

* Treat `GITHUB_TOKEN` like any other credential — scope it as narrowly as possible (fine-grained token limited to this repo, `Contents: read` + `Actions: write` only if you want the scan button).
* `DISCORD_WEBHOOK_URL` should live only in GitHub Actions secrets, never in `config.json` or committed anywhere.
* `state_seen.json` and `dashboard_data/*.json` will fill up with real job posting data (titles, companies, URLs) as the bot runs — that's expected and is what the dashboard displays. It's not sensitive on its own, but it is your data, so keep the repo private if you'd rather not share which jobs you're tracking.

## Tests

```
python -m unittest discover tests
```

## License

Use and modify freely for personal job tracking.
