# User Review Dashboard: PRD

Turns public App Store and Google Play reviews of SuperKalam (a UPSC exam prep app) into what to fix, build and protect. An independent project with no access to the developer console.

## Why

- Ratings hide the problems: the average is 4.56 and 82% of reviews are 5 stars, yet users report crashes, paywall frustration and missing Hindi-medium content.
- 40% of reviews say nothing specific ("best app", "bad"), which buries the useful ones.
- Volume is low (about 2 reviews a day), so single reports get lost instead of adding up.
- Without owner access there are no webhooks or reply data from Apple, so only public data can be used.

## Users

- Product manager (primary): decides what to fix, build and protect.
- Customer support team: handles critical reviews that have no reply.

## Goals

- Show every problem, request and praise, with the review evidence behind it.
- Count each theme once, whatever the star rating.
- Flag critical reviews and alert support when they have no developer reply.
- Refresh automatically every 2 weeks, at no cost.
- Make every number traceable to the reviews behind it.

## Non-goals

- Real-time sync or webhooks, which need owner access.
- Replying to reviews from the dashboard.
- App Store reply status, which Apple doesn't publish.
- Alerts from support channels, which a separate CS process handles.
- Tracking what the team shipped; that belongs in Jira or ClickUp.
- Other sources such as social media or support tickets (V2).
- Logins and roles.

## Functional requirements

**Home: a glance, no tables**
- Since-last-update line: new reviews, rating change and critical count, with the full update and past updates in a panel.
- Four cards: To fix, To build, To protect, Critical. Each shows a count, what's new, a trend line and the largest item.
- One chart that switches between average rating and reviews per month.
- Where problems come from (by product area), and reviews worth reading.

**Feedback: Fix, Build and Protect tabs**
- One row per theme: product area, reviews, last-90-day change, and severity (Fix) or priority (Build).
- Visible Period filter with a custom range. Search, plus Filters for area, severity and priority.

**Breakdowns: reporting**
- Date range with a custom option, grouped by product area, app version or user segment.
- Problems, requests and praise shown as bars and a table. Competitor mentions with their context.
- Clicking a bar or row opens the statements behind that number, with a link to the matching reviews.
- CSV export.

**Support: critical reviews**
- Critical reviews, longest wait first: category, trigger phrase, store, reply status, alert status.
- Reply rate and median reply time.
- Alert email preview.

**Reviews: every review**
- Search, filters (period, store, rating, area, feedback type) and pages of 25.
- Saved views: says nothing specific, posted on a spike day, rating doesn't match text.
- Switch to include or exclude spike-day reviews in every number.
- CSV export.

**Detail panels (every row opens one)**
- Theme: counts, first and last seen, average rating, share from 1 to 3 star reviews, quotes, other phrasings, definition.
- Review: full text with evidence highlighted, what it mentions, the reviewer's segment, developer reply.
- Back button when one panel opens another. "Save as ticket" is shown as coming soon.

**Alerts**
- After each refresh, email Google Play critical reviews that have no reply.
- Recipients come from configuration, not code. Unanswered reviews are included again in each refresh's alert until they get a reply.

**Data pipeline**
- Full pull from both stores into one master store, updated in place rather than duplicated.
- Log of new reviews, edits, rating changes, new replies and deletions.
- Build step computes every number. Tests run before publishing.

## Design: how it works

**Flow**
```
ingest.py      ->  data/reviews.json      master store + changes.json log
labelling      ->  data/labels.json       themes and flags per review
build.py       ->  web/data/dashboard.json  every number, computed in code
web/           ->  static dashboard, hosted on Netlify
```

**Classification**
- Each review is split into mentions. Each mention gets a product area (12 areas), a kind (problem, request or praise), and a type and sub-type.
- Each mention stores the exact phrase from the review as evidence.
- Review-level flags: critical (7 categories), says nothing specific, rating mismatch, user segment, competitor and context.
- 57 themes, each with a definition and exclusions, live in `backend/taxonomy.json`.

**Rules**
- A complaint that users also request as a feature counts once, under Fix.
- Priority: Deal-breaker when 3+ reviews and 50%+ are 1 to 3 stars; Nice-to-have otherwise; Too few to tell under 3 reviews.
- Severity is Blocking or Annoying, set per theme.
- Spike day: 8 or more reviews in a day, against about 2 normally.
- Trend compares the last 90 days with the 90 before. An update covers 14 days.
- Critical is decided by what the review says, not its rating.

**Labelling**
- V1: all 475 reviews labelled by hand.
- Next: an LLM labels only new reviews, in the same format, checked against the V1 labels.
- Every label must use a known theme, and its evidence must appear word for word in the review. Otherwise it is rejected.

**Stack**
- Python, static HTML/CSS/JS and JSON files in the GitHub repo. No database, no server, $0.

**Interface**
- 5 pages, each following one standard page pattern: dashboard, list or reporting.
- Blue marks everything you can click. Red is used only for critical.
- Works from 375px to desktop; tables and charts scroll inside their own frames.

## Edge cases and handling

| Case | Handling |
|---|---|
| The Play scraper hides request errors as "no more pages" | Its page fetcher is called directly, with retries and backoff |
| Apple shows at most 500 reviews per country store | 15 country stores are pulled; the master store keeps reviews after Apple drops them; deletions aren't inferred from capped feeds |
| App Store replies aren't public | Reply status shown as "Unknown"; never alerted |
| A review is edited, re-rated, deleted or gets a reply | Logged in `changes.json`; deleted reviews are flagged, not removed |
| Review dates are last-edited dates | Shown as they are |
| A review says nothing specific | Counts in the rating, left out of theme counts, has its own view |
| The star rating contradicts the text | Flagged; the text decides sentiment and themes |
| A sudden burst of reviews | Flagged as a spike day; the viewer decides whether to count it |
| Hinglish or Hindi text | Labelled like English; the font supports Devanagari |
| Very few reviews behind a number | Counts are shown alongside percentages; priority reads "Too few to tell" |
| New reviews not labelled yet | Counted in the rating; Home shows how many are waiting |
| The LLM invents a theme or a quote (next phase) | Rejected by the evidence and taxonomy checks before publishing |
| A critical review stays unanswered | Included in every refresh's alert until it gets a reply |
| No results for a filter | Empty state with a Clear filters action |
| Invalid custom date range | Message asking for both dates, with the start date on or before the end date |
| The dashboard data fails to load | Error message with a Reload button |


