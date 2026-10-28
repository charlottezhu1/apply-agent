# Listener: fetch and hard-filter jobs

Pulls postings from public job-board APIs and drops anything that obviously fails the requirements. It returns one big list of candidate jobs.

**This stage is code only. It makes no LLM calls.** The agent just calls `run.main(run_dir)`.

- **Input:** `config.yaml` (`search:` block) and `companies.yaml`
- **Output:** `runs/<date>/01_listed.json`, a list of `Job` records with the listener fields filled in (see the [data contract](../../README.md#data-contract))

---

## Why it needs a company list

Greenhouse, Lever, Ashby, SmartRecruiters and Workday have **no global search**. Every request is for one company's job board. So the listener needs to know which companies to check and which ATS each one uses. There are two sources, and you should use both:

1. **`companies.yaml`**, a hand-curated list of target companies. You can find each ATS and board token in the company's careers URL (table below).
2. **SimplifyJobs New-Grad feed**, a community-maintained list of about 20k new-grad postings across all ATSs, with posted dates and a sponsorship field. Use it directly as a job source, and also to **discover new companies**: parse each listing's `url` with the same patterns to grow `companies.yaml` automatically. Add new companies with `auto: true` so you can review them.

```yaml
# companies.yaml
- {name: Stripe,     ats: greenhouse,      token: stripe}
- {name: Notion,     ats: ashby,           token: notion}
- {name: Palantir,   ats: lever,           token: palantir}
- {name: ServiceNow, ats: smartrecruiters, token: ServiceNow}
- {name: Salesforce, ats: workday,         host: salesforce.wd12.myworkdayjobs.com, tenant: salesforce, site: External_Career_Site}
```

| ATS | Careers URL pattern | Token |
|---|---|---|
| Greenhouse | `boards.greenhouse.io/{token}` or `job-boards.greenhouse.io/{token}`, or `?gh_jid=` on a company site | `{token}` |
| Lever | `jobs.lever.co/{token}` | `{token}` |
| Ashby | `jobs.ashbyhq.com/{token}` | `{token}` |
| SmartRecruiters | `jobs.smartrecruiters.com/{token}` | `{token}` (case-sensitive) |
| Workday | `{tenant}.wd{N}.myworkdayjobs.com/[en-US/]{site}` | `host`, `tenant`, `site` |

Seed `companies.yaml` with the companies already in `resume/customized/` (Figma, Square, Atlassian, Harvey, Notion, Scale, Snowflake, Stripe, Robinhood, Roblox, Salesforce, …). Google, Meta, Microsoft and Amazon run their own career sites, not one of these ATSs, so they are **out of scope**. Their roles reach the pipeline only through the Simplify feed.

---

## API reference (verified 2026-10-03)

All endpoints are public, need no auth, and return JSON. Send a descriptive `User-Agent`, keep at most about 5 concurrent requests per host, and retry 429/5xx with backoff.

### Greenhouse

```
GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
```

- Response: `{"jobs": [...]}`. The whole board comes back in one call (Stripe ≈ 700 jobs). There is no paging and no server-side filtering.
- Fields used: `id`, `title`, `location.name`, `absolute_url`, `first_published` (use this for `posted_at`, falling back to `updated_at`), and `content`.
- `content` is **HTML-escaped HTML**. Run `html.unescape` first, then strip tags.

### Lever

```
GET https://api.lever.co/v0/postings/{token}?mode=json
```

- Response: a bare array. Optional server-side filters: `location=`, `commitment=Full-time`, `team=`, `department=`, `skip=`, `limit=`. Use `commitment=Full-time` but **don't filter by location server-side**, because location strings vary per company.
- Fields used: `id`, `text` (title), `categories.location`, `categories.allLocations`, `categories.commitment`, `workplaceType`, `hostedUrl`, `createdAt` (**epoch milliseconds**), `descriptionPlain`, and `lists[]` (requirements, as `{text, content(html)}`). Build `description` from `descriptionPlain` + each list + `additionalPlain`.
- EU-hosted boards use `api.eu.lever.co`. Try that host if the main one returns 404.

### Ashby

```
GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
```

- Response: `{"jobs": [...]}`, the whole board, with no server-side filtering.
- Fields used: `id`, `title`, `location`, `secondaryLocations[].location`, `isRemote`, `workplaceType`, `employmentType`, `publishedAt`, `jobUrl`, `descriptionPlain`, and `isListed` (skip it if false).

### SmartRecruiters

```
GET https://api.smartrecruiters.com/v1/companies/{token}/postings?q=software%20engineer&country=us&limit=100&offset=0
GET https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}          # detail, for the JD
```

- The list response is `{totalFound, offset, limit, content: [...]}`. **Server-side filters work:** `q`, `country`, `region`, `city`, `department`. Page with `offset` until `offset >= totalFound`. Run one query per role keyword.
- List fields: `id`, `name` (title), `releasedDate`, `location.{city, region, country, remote, fullLocation}`, `experienceLevel.id` (`entry_level`, `associate`, `mid_senior_level`, … but it's often `not_applicable`, so treat it as a weak signal), and `typeOfEmployment`.
- The list **has no description**. Call the detail endpoint only for jobs that survive the title and location filters. The JD is `jobAd.sections.{jobDescription, qualifications, additionalInformation}.text` (HTML). Use `postingUrl` for `url`.
- An unknown token returns `totalFound: 0`, not a 404. tests/TESTING.md checks that every configured token returns more than 0 results.

### Workday (undocumented, so expect breakage)

```
POST https://{host}/wday/cxs/{tenant}/{site}/jobs
     {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "software engineer"}
GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}                       # detail
```

- The list response is `{total, jobPostings: [...], facets: [...]}`. **`limit` maxes out at 20**, so page with `offset`. `searchText` works. Location facets use tenant-specific ids, so skip them and filter locally.
- List fields: `title`, `externalPath`, `locationsText` (which can be `"3 Locations"`), and `postedOn` (relative text like `"Posted Today"` or `"Posted 30+ Days Ago"`).
- Detail: `jobPostingInfo.{jobDescription (HTML), startDate (ISO date, use as posted_at), location, externalUrl, timeType, remoteType}`. Fetch details only for jobs that pass the title filter, because `locationsText` like `"3 Locations"` needs the detail to resolve.

### SimplifyJobs New-Grad feed (aggregator)

```
GET https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json
```

- A bare array of about 20k items. Fields: `id`, `company_name`, `title`, `locations[]`, `url`, `date_posted` (epoch seconds), `active`, `is_visible`, `sponsorship` (`"Offers Sponsorship"` / `"Does Not Offer Sponsorship"` / `"U.S. Citizenship is Required"` / `"Other"`), `category`, and `degrees[]`.
- Keep `active && is_visible`. Map `sponsorship` to `sponsorship_signal` (`Does Not Offer…` or `Citizenship…` → `"no"`).
- There's **no JD**. If the `url` matches a supported ATS, fetch the JD from that ATS's detail endpoint. Otherwise use `description = ""` and let the matchmaker score from the title, or have the matchmaker fetch the page.
- **Dedupe:** if a Simplify item's `url` points to a posting already fetched from an ATS, keep the ATS version.

---

## Filters (applied in this order, cheapest first)

Do everything you can **before** fetching JD details. Each filter records why it dropped a job in `runs/<date>/listener_drops.jsonl` as `{id, title, reason}`, so you can audit the filters.

| # | Filter | Rule | Server-side? |
|---|---|---|---|
| 1 | **Seen** | Skip a job if its id **or** its fingerprint (company + normalized title, see the root README's "State and dedupe") is in `data/seen_jobs.json` from the last 60 days. Among same-fingerprint copies in one fetch (one role posted per city), keep only the Bay Area copy | n/a |
| 2 | **Role** | Title matches SWE or PM (below) | SmartRecruiters `q`, Workday `searchText` |
| 3 | **Seniority** | Title has no senior keyword (below) | No |
| 4 | **Internship** | Drop `intern`, `internship`, `co-op` unless `include_internships` | No |
| 5 | **Location** | Per `location_mode` (below) | SmartRecruiters `country=us` |
| 6 | **Age** | `posted_at` within `max_age_days`. If `posted_at` is null, keep the job | No |
| 7 | *(fetch JD now)* | | |
| 8 | **Years required** | Drop if the **minimum** years parsed from the JD is greater than `max_years_required` | No |
| 9 | **Sponsorship** | Drop only if `sponsorship_signal == "no"` | No |

**Role (case-insensitive):**
- SWE: `software engineer`, `software developer`, `swe`, `full[- ]stack`, `backend`, `back[- ]end`, `frontend`, `front[- ]end`, `machine learning engineer`, `ml engineer`, `ai engineer`, `applied ai`, `forward deployed engineer`, `founding engineer`, `product engineer`, `mobile engineer`, `ios engineer`, `android engineer`
- AI / ML / MLE titles count as **SWE**. There is no separate MLE track.
- PM: `product manager`, `associate product manager`, `apm`, `rotational product manager`, `technical product manager`
- Never: `product marketing`, `project manager`, `program manager`, `engineering manager`, `sales engineer`, `support engineer`, `test engineer`/`qa`, `hardware`, `firmware`, `mechanical`, `electrical`

**Seniority (drop):** `senior`, `sr`, `staff`, `principal`, `lead`, `director`, `head of`, `vp`, `distinguished`, `architect`, `manager` (only when the title isn't a PM title), `group product manager`, roman numerals or levels `II`/`III`/`IV`/`2`/`3`/`L4+`. **Keep and tag as entry:** `new grad`, `new college grad`, `university grad`, `early career`, `entry level`, `junior`, `associate`, `I`, `2026`, `2027`.

**Location** (`bay_area` and `remote_us` are set for every job regardless of mode):
- Bay Area cities: San Francisco, South San Francisco, Oakland, Berkeley, Emeryville, Alameda, San Mateo, Foster City, Burlingame, San Bruno, Redwood City, Menlo Park, Palo Alto, Stanford, Mountain View, Los Altos, Sunnyvale, Santa Clara, Cupertino, San Jose, Milpitas, Fremont, Newark, Pleasanton, Dublin, Walnut Creek, plus "Bay Area" and "SF".
- Remote US: the text contains `remote` and either has no country or says US / United States / USA / North America.
- `bay_area_only` keeps jobs where `bay_area` is true. `bay_area_or_remote` keeps jobs where `bay_area` or `remote_us` is true. `us` keeps any US location.

**Years required:** use a regex like `(\d+)\s*\+?\s*(?:-|–|to)?\s*(\d+)?\s*\+?\s*years?` near `experience`. Take the **smallest minimum** in the JD, because "3+ years, or 1+ with a Master's" means 1. Store it in `years_required_min`.

**Sponsorship regex.** These phrases set `"no"`: `unable to sponsor`, `will not sponsor`, `cannot sponsor`, `not able to sponsor`, `no visa sponsorship`, `sponsorship is not available`, `without (the need for )?(current or future )?sponsorship`, `must be a U.S. citizen`, `U.S. citizenship (is )?required`, `security clearance`, `green card holders? only`. These phrases set `"yes"`: `visa sponsorship (is )?available`, `will sponsor`, `H-1B`, `sponsorship provided`. Anything else is `"unknown"`, which is kept. The matchmaker reads the full JD and makes the final call.

---

## Code layout and interface

```
apply_agent/discover/
├── companies.yaml         # job boards to fetch (grown by discover.py)
├── discover.py            # add boards from the Simplify feed; --check verifies every token
├── sources/
│   ├── base.py            # shared HTTP client (per-host limits, retries) + Source interface
│   ├── ats.py             # Greenhouse, Lever, Ashby, SmartRecruiters, Workday
│   └── simplify.py        # SimplifyJobs feed + ATS URL parsing
├── normalize.py           # html → text, date parsing (ms, s, ISO, "Posted 3 Days Ago"), location parsing
├── filters.py             # pure functions, one per filter row above: (Job, cfg) -> str | None (drop reason)
└── run.py                 # main(run_dir) -> Path; CLI: python -m apply_agent.discover.run --run-dir ...
```

- Use `httpx.AsyncClient` with a per-host semaphore of 5, a 20s timeout, and 3 retries.
- A failing company logs to `errors.jsonl` and is skipped. It never stops the run.
- At the end, print a summary like this:

```
fetched 4,812 · role 610 · seniority 205 · location 131 · age 118 · years 74 · sponsorship 69 → 01_listed.json (69 jobs)
```
