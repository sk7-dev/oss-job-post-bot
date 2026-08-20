import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlparse

import requests

CONFIG_PATH = "config.json"
STATE_PATH = "state_seen.json"
REQUEST_TIMEOUT = 30
CONCURRENCY = 5
SEEN_KEY_TTL_DAYS = 90


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def matches_filters(job: dict, filters: dict) -> bool:
    title = normalize_text(job.get("title"))
    location = normalize_text(job.get("location"))
    department = normalize_text(job.get("department"))
    combined = " | ".join([title, location, department])

    title_keywords_any = [
        normalize_text(x) for x in filters.get("title_keywords_any", []) if str(x).strip()
    ]
    locations_any = [
        normalize_text(x) for x in filters.get("locations_any", []) if str(x).strip()
    ]
    excluded_keywords_any = [
        normalize_text(x) for x in filters.get("excluded_keywords_any", []) if str(x).strip()
    ]

    title_ok = True
    if title_keywords_any:
        title_ok = any(k in title for k in title_keywords_any)

    location_ok = True
    if locations_any:
        location_ok = any(k in location for k in locations_any)

    excluded_ok = True
    if excluded_keywords_any:
        excluded_ok = not any(k in combined for k in excluded_keywords_any)

    return title_ok and location_ok and excluded_ok


def matched_title_keywords(job: dict, filters: dict) -> List[str]:
    title = normalize_text(job.get("title"))
    keywords = [
        normalize_text(x) for x in filters.get("title_keywords_any", []) if str(x).strip()
    ]
    return [k for k in keywords if k in title]


def safe_get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None):
    resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def safe_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None):
    return safe_get(url, params=params, headers=headers).json()


def safe_post_json(url: str, json_body: dict, headers: Optional[dict] = None):
    resp = requests.post(url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_greenhouse(source: dict) -> List[dict]:
    token = source["board_token"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    data = safe_get_json(url)

    jobs = []
    for item in data.get("jobs", []):
        location = ""
        if isinstance(item.get("location"), dict):
            location = item["location"].get("name", "")

        departments = item.get("departments") or []
        department = ", ".join(d.get("name", "") for d in departments if d.get("name"))

        offices = item.get("offices") or []
        office = ", ".join(o.get("name", "") for o in offices if o.get("name"))

        jobs.append({
            "source_name": source["name"],
            "source_type": "greenhouse",
            "external_id": str(item.get("id")),
            "title": item.get("title", ""),
            "location": location or office,
            "department": department,
            "url": item.get("absolute_url", ""),
            "posted_at": "",
        })
    return jobs


def fetch_lever(source: dict) -> List[dict]:
    company = source["company"]
    url = f"https://api.lever.co/v0/postings/{company}"
    data = safe_get_json(url, params={"mode": "json"})

    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        jobs.append({
            "source_name": source["name"],
            "source_type": "lever",
            "external_id": str(item.get("id")),
            "title": item.get("text", ""),
            "location": categories.get("location", ""),
            "department": categories.get("team", ""),
            "url": item.get("hostedUrl") or item.get("applyUrl") or "",
            "posted_at": str(item.get("createdAt", "")),
        })
    return jobs


_ASHBY_GQL_QUERY = (
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {"
    " jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {"
    " teams { id name } jobPostings { id title teamId locationName employmentType } } }"
)


def _fetch_ashby_graphql(org_key: str, source_name: str) -> List[dict]:
    url = "https://jobs.ashbyhq.com/api/non-user-graphql"
    headers = {"apollographql-client-name": "@ashby/jobs-board-ui"}
    body = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": org_key},
        "query": _ASHBY_GQL_QUERY,
    }
    data = safe_post_json(url, body, headers=headers)
    board = (data.get("data") or {}).get("jobBoard") or {}
    team_map = {t["id"]: t["name"] for t in board.get("teams", [])}
    jobs = []
    for item in board.get("jobPostings", []):
        jobs.append({
            "source_name": source_name,
            "source_type": "ashby",
            "external_id": str(item.get("id", "")),
            "title": item.get("title", ""),
            "location": item.get("locationName", ""),
            "department": team_map.get(item.get("teamId", ""), ""),
            "url": f"https://jobs.ashbyhq.com/{org_key}/{item.get('id', '')}",
            "posted_at": "",
        })
    return jobs


def fetch_ashby(source: dict) -> List[dict]:
    org_key = source["organization_key"]
    url = source.get("api_url", "https://api.ashbyhq.com/jobPosting.list")
    body = {
        "organizationHostedJobsPageName": org_key,
        "listedOnly": True,
    }
    try:
        data = safe_post_json(url, body)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            return _fetch_ashby_graphql(org_key, source["name"])
        raise

    jobs = []
    for item in data.get("results", []):
        location = ""
        loc = item.get("location")
        if isinstance(loc, dict):
            location = (
                loc.get("locationSummary")
                or loc.get("city")
                or loc.get("region")
                or loc.get("country")
                or ""
            )

        department = ""
        departments = item.get("department") or item.get("departments") or []
        if isinstance(departments, list):
            if departments and isinstance(departments[0], dict):
                department = ", ".join(d.get("name", "") for d in departments if d.get("name"))
            else:
                department = ", ".join(str(x) for x in departments if x)
        elif isinstance(departments, dict):
            department = departments.get("name", "")

        jobs.append({
            "source_name": source["name"],
            "source_type": "ashby",
            "external_id": str(item.get("id") or item.get("jobPostingId") or item.get("title", "")),
            "title": item.get("title", ""),
            "location": location,
            "department": department,
            "url": item.get("jobUrl") or item.get("applicationUrl") or "",
            "posted_at": item.get("publishedAt", ""),
        })
    return jobs


def parse_workday_source(source: dict) -> tuple[str, str, str]:
    if source.get("tenant") and source.get("site") and source.get("base_url"):
        return source["base_url"].rstrip("/"), source["tenant"], source["site"]

    url = source["url"]
    parsed = urlparse(url)
    host = parsed.netloc
    path_parts = [p for p in parsed.path.split("/") if p]

    if not path_parts:
        raise ValueError("Could not determine Workday site from URL")

    if len(path_parts) >= 2 and "-" in path_parts[0]:
        site = path_parts[1]
    else:
        site = path_parts[0]

    tenant = host.split(".")[0]
    base_url = f"{parsed.scheme}://{host}"
    return base_url, tenant, site


def workday_extract_location(item: dict) -> str:
    locations = item.get("locationsText")
    if locations:
        return locations

    bullet_fields = item.get("bulletFields") or []
    if bullet_fields:
        return " | ".join(str(x) for x in bullet_fields if x)

    locations = item.get("locations") or []
    if isinstance(locations, list) and locations:
        vals = []
        for loc in locations:
            if isinstance(loc, dict):
                text = loc.get("displayName") or loc.get("name")
                if text:
                    vals.append(text)
            elif loc:
                vals.append(str(loc))
        if vals:
            return " | ".join(vals)

    return ""


def workday_extract_posted(item: dict) -> str:
    return (
        item.get("postedOn")
        or item.get("postedDate")
        or item.get("startDate")
        or ""
    )


def fetch_workday(source: dict) -> List[dict]:
    base_url, tenant, site = parse_workday_source(source)
    endpoint = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    limit = int(source.get("limit", 20))
    offset = 0
    jobs = []

    while True:
        body = {
            "limit": limit,
            "offset": offset,
            "searchText": source.get("search_text", ""),
        }

        data = safe_post_json(endpoint, body, headers=headers)

        postings = (
            data.get("jobPostings")
            or data.get("job_postings")
            or data.get("jobs")
            or []
        )

        if not postings:
            break

        for item in postings:
            title = item.get("title", "")
            external_path = item.get("externalPath", "")
            if external_path:
                job_url = f"{base_url}/{site}/job/{external_path.lstrip('/')}"
            else:
                job_url = source.get("url", "")

            department = ""
            if item.get("jobFamily"):
                department = str(item.get("jobFamily"))
            elif item.get("jobFamilyGroup"):
                department = str(item.get("jobFamilyGroup"))

            external_id = (
                item.get("bulletFields", [None, None])[-1]
                or item.get("jobReqId")
                or item.get("id")
                or item.get("title")
            )

            jobs.append({
                "source_name": source["name"],
                "source_type": "workday",
                "external_id": str(external_id),
                "title": title,
                "location": workday_extract_location(item),
                "department": department,
                "url": job_url,
                "posted_at": workday_extract_posted(item),
            })

        total = data.get("total")
        offset += len(postings)

        if total is not None and offset >= total:
            break
        if len(postings) < limit:
            break

        time.sleep(0.2)

    return jobs


def adp_extract_location(item: dict) -> str:
    locations = []
    for loc in item.get("requisitionLocations") or []:
        address = loc.get("address") or {}
        city = address.get("cityName", "")
        state = (address.get("countrySubdivisionLevel1") or {}).get("longName", "")
        country = (address.get("country") or {}).get("longName", "")
        parts = [p for p in [city, state, country] if p]
        if parts:
            locations.append(", ".join(parts))
    return " | ".join(locations)


def fetch_adp(source: dict) -> List[dict]:
    domain = source["domain"]
    base_url = f"https://myjobs.adp.com/{domain}"

    config = safe_get_json(f"https://myjobs.adp.com/public/staffing/v1/career-site/{domain}")
    my_jobs_token = config.get("myJobsToken")
    if not my_jobs_token:
        raise ValueError(f"No myJobsToken found for ADP career site '{domain}'")

    headers = {
        "myjobstoken": my_jobs_token,
        "rolecode": "manager",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://myjobs.adp.com/",
    }

    endpoint = "https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters"
    select_fields = "reqId,jobTitle,publishedJobTitle,workLevelCode,postingDate,requisitionLocations"
    top = 100
    skip = 0
    jobs = []

    while True:
        params = {
            "$select": select_fields,
            "$top": top,
            "$skip": skip,
            "$filter": "",
            "tz": "America/New_York",
        }
        data = safe_get_json(endpoint, params=params, headers=headers)
        postings = data.get("jobRequisitions") or []
        if not postings:
            break

        for item in postings:
            req_id = str(item.get("reqId", ""))
            jobs.append({
                "source_name": source["name"],
                "source_type": "adp",
                "external_id": req_id,
                "title": item.get("publishedJobTitle") or item.get("jobTitle", ""),
                "location": adp_extract_location(item),
                "department": "",
                "url": f"{base_url}/cx/job-details?reqId={req_id}",
                "posted_at": item.get("postingDate", ""),
            })

        total = data.get("count")
        skip += len(postings)

        if total is not None and skip >= total:
            break
        if len(postings) < top:
            break

    return jobs


_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "adp": fetch_adp,
}


def fetch_jobs_for_source(source: dict) -> List[dict]:
    stype = source["type"].lower()
    fetcher = _FETCHERS.get(stype)
    if not fetcher:
        raise ValueError(f"Unsupported source type: {stype}")
    return fetcher(source)


def _fetch_source_timed(source: dict):
    start = time.perf_counter()
    try:
        jobs = fetch_jobs_for_source(source)
        return jobs, None, time.perf_counter() - start
    except Exception as e:
        return None, e, time.perf_counter() - start


def stable_job_key(job: dict) -> str:
    return "||".join([
        job.get("source_type", ""),
        job.get("source_name", ""),
        job.get("external_id", ""),
        job.get("url", ""),
    ])


def format_discord_text(new_jobs: List[dict]) -> str:
    lines = [f"{len(new_jobs)} new matching job(s) found:"]
    for job in new_jobs:
        lines.append(
            f"- {job['title']} | {job['source_name']} | {job.get('location', '')} | {job['url']}"
        )

    text = "\n".join(lines)
    if len(text) <= 1900:
        return text

    trimmed = [f"{len(new_jobs)} new matching job(s) found:"]
    count = 0

    for job in new_jobs:
        line = f"- {job['title']} | {job['source_name']} | {job.get('location', '')} | {job['url']}"
        candidate = "\n".join(trimmed + [line])
        if len(candidate) > 1900:
            break
        trimmed.append(line)
        count += 1

    remaining = len(new_jobs) - count
    if remaining > 0:
        trimmed.append(f"...and {remaining} more.")

    return "\n".join(trimmed)


def send_discord(webhook_url: str, text: str) -> bool:
    last_error = None

    for attempt in range(3):
        try:
            resp = requests.post(
                webhook_url,
                json={"content": text},
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )

            if 200 <= resp.status_code < 300:
                return True

            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"{resp.status_code} {resp.text[:200]}"
                time.sleep(2 * (attempt + 1))
                continue

            resp.raise_for_status()

        except requests.RequestException as e:
            last_error = str(e)
            time.sleep(2 * (attempt + 1))

    print(f"ERROR - Discord webhook failed: {last_error}", file=sys.stderr)
    return False


def load_seen_keys(state: dict) -> dict:
    raw = state.get("seen_keys", {})
    if isinstance(raw, list):
        now = datetime.now(timezone.utc).isoformat()
        return {k: now for k in raw}
    return raw


def prune_seen_keys(seen: dict) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_KEY_TTL_DAYS)
    pruned = {}
    for key, ts in seen.items():
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                pruned[key] = ts
        except (ValueError, TypeError):
            pruned[key] = ts
    return pruned


def main() -> int:
    config = load_json(CONFIG_PATH)
    state = load_json(STATE_PATH)

    filters = config.get("filters", {})
    sources = config.get("sources", [])
    seen_keys_dict = load_seen_keys(state)
    seen_keys = set(seen_keys_dict)

    all_jobs = []
    errors = []

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {executor.submit(_fetch_source_timed, s): s for s in sources}
        fetch_results = {}
        for future in as_completed(futures):
            source = futures[future]
            name = source.get("name", "unknown source")
            fetch_results[name] = future.result()

    for source in sources:
        name = source.get("name", "unknown source")
        result = fetch_results.get(name)
        if result is None:
            continue
        jobs, exc, _duration = result
        if exc is not None:
            err = f"{name}: {exc}"
            errors.append(err)
            print(f"ERROR - {err}", file=sys.stderr)
        else:
            all_jobs.extend(jobs)
            print(f"{name}: fetched {len(jobs)} job(s)")

    matching_jobs = [job for job in all_jobs if matches_filters(job, filters)]
    for job in matching_jobs:
        job["matched_keywords"] = matched_title_keywords(job, filters)

    new_jobs = []
    new_keys = []
    for job in matching_jobs:
        key = stable_job_key(job)
        if key not in seen_keys:
            new_jobs.append(job)
            new_keys.append(key)

    print(f"Fetched total jobs: {len(all_jobs)}")
    print(f"Matching jobs: {len(matching_jobs)}")
    print(f"New jobs: {len(new_jobs)}")

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    delivered = False

    if new_jobs and webhook:
        delivered = send_discord(webhook, format_discord_text(new_jobs))
        if delivered:
            print("Discord alert sent.")
        else:
            print("New jobs found, but Discord webhook delivery failed.", file=sys.stderr)
    elif new_jobs:
        print("New jobs found, but DISCORD_WEBHOOK_URL is not configured.")
    else:
        print("No new matching jobs to send.")

    if not new_jobs or delivered or not webhook:
        now_ts = datetime.now(timezone.utc).isoformat()
        for key in new_keys:
            seen_keys_dict[key] = now_ts

    seen_keys_dict = prune_seen_keys(seen_keys_dict)
    state["seen_keys"] = dict(sorted(seen_keys_dict.items()))
    save_json(STATE_PATH, state)

    if errors:
        print("Completed with source errors:", file=sys.stderr)
        for err in errors:
            print(f" - {err}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
