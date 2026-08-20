# Job Watcher Bot

A lightweight Python bot that watches multiple career sites, filters matching roles, and sends new job alerts straight to Discord, running for free on a schedule via GitHub Actions.

This is a clean template: no one's personal job search data, filters, or secrets are baked in. Fork it, add the companies and filters you care about, and set it running.

## What it does

* Fetches jobs from supported source types:
  * Greenhouse
  * Lever
  * Ashby
  * Workday
  * ADP
* Filters roles by title keywords, locations, and excluded keywords
* Tracks previously seen jobs in `state_seen.json` so you're only alerted once per job
* Sends alerts for new matching roles to a Discord webhook
* Runs on a schedule via GitHub Actions and commits its own state back to the repo (no server required)

## Repo layout

```
watcher.py               # main bot logic: fetch, filter, dedupe, alert
config.json               # YOUR sources + filters (sample data, replace)
state_seen.json           # job IDs already seen (starts empty)
.github/workflows/        # scheduled GitHub Action that runs the bot
```

## Quick start

1. Fork or clone this repo.
2. `pip install -r requirements.txt`
3. Edit `config.json`; see "Configuring sources and filters" below. It ships with 3 working sample sources (GitLab/Greenhouse, Notion/Ashby, Salesforce/Workday) so you can run it immediately and see how the format works, but you'll want to replace them with companies you actually care about.
4. (Optional) Set `DISCORD_WEBHOOK_URL` as an environment variable if you want Discord alerts. Without it, the bot still runs, prints results, and updates `state_seen.json`; it just skips sending anything.
5. Run it: `python watcher.py`

Run it again and you'll see `New jobs: 0` for anything already recorded in `state_seen.json`; that's the dedupe working.

## Configuring sources and filters

`config.json` has two sections:

**`filters`**: applied to every source:
* `title_keywords_any`: a job matches if its title contains at least one of these (case-insensitive substring match)
* `locations_any`: a job matches if its location contains at least one of these
* `excluded_keywords_any`: a job is dropped if its title, location, or department contains any of these
* Leave a list empty (`[]`) to skip that filter entirely

**`sources`**: an array of career sites to watch. Each entry needs `name`, `type`, and type-specific fields:

| type | required fields | notes |
|---|---|---|
| `greenhouse` | `board_token` | Find it in the company's Greenhouse job board URL: `boards.greenhouse.io/<board_token>` |
| `lever` | `company` | From `jobs.lever.co/<company>` |
| `ashby` | `organization_key` | From `jobs.ashbyhq.com/<organization_key>` |
| `workday` | `url` (or `tenant`+`site`+`base_url`), optional `search_text`, `limit` | `url` is the public careers URL, e.g. `https://<tenant>.wd#.myworkdayjobs.com/<site>` |
| `adp` | `domain` | ADP career site subdomain |

To find the right identifiers for a company, open their careers page, watch the Network tab for the API calls their job board makes (Greenhouse/Lever/Ashby/Workday all expose their own JSON APIs), or just look at the URL structure; most of the fields above come straight from the public careers URL.

## Setting up Discord alerts

1. In Discord, go to the target channel's **Settings → Integrations → Webhooks → New Webhook**, then copy its URL.
2. Set it as `DISCORD_WEBHOOK_URL`, either as a local environment variable for testing, or as a GitHub Actions secret for the deployed bot (see below).

## Deploying the scheduled bot (GitHub Actions)

The included workflow (`.github/workflows/watch-jobs.yml`) runs the bot every 10 minutes, commits `state_seen.json` and `config.json` back to the repo, and requires no server of your own.

1. Push this repo to your own GitHub repository.
2. In the repo, go to **Settings → Secrets and variables → Actions** and add a secret named `DISCORD_WEBHOOK_URL` (skip this if you don't want Discord alerts; the workflow runs fine without it, it just won't deliver anything).
3. Make sure **Settings → Actions → General → Workflow permissions** is set to "Read and write permissions"; the workflow commits state back to the repo.
4. That's it. The workflow will start running on its schedule, or trigger it manually from the Actions tab (`workflow_dispatch`).

## Security notes

* `DISCORD_WEBHOOK_URL` should live only in GitHub Actions secrets, never in `config.json` or committed anywhere.
* `state_seen.json` will fill up with real job posting data (titles, companies, URLs) as the bot runs. It's not sensitive on its own, but it is your data, so keep the repo private if you'd rather not share which jobs you're tracking.

## License

Use and modify freely for personal job tracking.
