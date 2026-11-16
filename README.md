# Google Play Reviews Scraper — App Data & Ratings, No Personal Data

Scrape Google Play Store reviews, ratings, and full app metadata from any Android app.
This Google Play scraper extracts review text, star scores, dates, app versions, and
developer replies, plus complete app store listing data: installs, price, category,
release date, and rating histogram.

**Author names and profile images are never collected.** Every review is returned with
an irreversible pseudonymous hash instead of a display name, so you get the analysis you
need without holding personal data. See [Privacy and GDPR](#privacy-and-gdpr) below.

> **Unofficial tool.** This Actor is not affiliated with, sponsored by, or endorsed by
> Google LLC. "Google Play" and "Android" are trademarks of Google LLC. This Actor reads
> only publicly visible Google Play listing and review content.

## Pricing

Currently **free** — you only pay Apify platform usage for the compute a run consumes.
No per-result fee on top.

## What this Google Play scraper extracts

**App data** — title, developer, description, summary, price, currency, in-app purchase
range, install count, exact install number, average score, rating count, rating
histogram, content rating, genre and category, app version, release date, last update
date, screenshots, icon, privacy policy URL, ads and IAP flags.

**Review data** — review text, star rating (1–5), review date, app version the review was
written on, helpful votes, developer reply text and reply date, pseudonymous author hash
with duplicate-name indicators.

## Why choose this Android app scraper

- **No personal data.** No user names, no avatars, no profile links, no developer email
  or postal address. Nothing in the output identifies a person.
- **Reliable dates.** Google Play returns release dates in the language you request:
  `Sep 6, 2013`, `06.09.2013`, `6 сент. 2013 г.` This scraper normalises every date to
  ISO 8601 so the column is always sortable, and keeps the original string in
  `releasedText`. Most scrapers hand you a mixed-format column you only discover is
  broken halfway through the analysis.
- **Handles messy input.** Paste a full Google Play URL, a `market://` link, or a bare
  package name. Duplicate entries are removed automatically, so you never pay twice for
  the same app.
- **Retries built in.** Four attempts with exponential backoff on every request. One
  unavailable app never kills the whole run — you get the rest of your data plus a clear
  warning in the log.
- **Incremental scraping.** Set `reviewsSince` to a date and the scraper stops paging as
  soon as it reaches older reviews. Ideal for daily review monitoring without re-reading
  the same rows every day.
- **Fast.** 5,000 reviews in under 30 seconds in testing. Ten apps in parallel with no
  rate limiting.

## Use cases

**App Store Optimization (ASO)** — track rating trends, install growth, and metadata
changes for your app and competitor apps.

**Review analysis and sentiment analysis** — export review text to your own pipeline,
LLM, or spreadsheet to find recurring complaints, feature requests, and bug reports.

**Competitor research** — monitor competitor app updates, pricing changes, and how users
react to each new version.

**Release monitoring** — filter reviews by `appVersion` to see exactly how sentiment
changed after a release.

**Support benchmarking** — developer replies carry their own timestamp, so you can
measure how fast a team answers negative reviews, yours or a competitor's.

**Fake review detection** — `authorHashCount` and `authorHashApps` reveal display names
appearing across multiple apps, without exposing who wrote what.

**Market research** — use `searchQuery` to pull an entire app category and compare
install counts, prices, and ratings across the market.

## Input

Give the scraper app IDs, a search query, or both.

```json
{
  "appIds": [
    "com.whatsapp",
    "https://play.google.com/store/apps/details?id=com.spotify.music"
  ],
  "searchQuery": "habit tracker",
  "maxAppsFromSearch": 10,
  "scrapeAppDetails": true,
  "scrapeReviews": true,
  "maxReviewsPerApp": 500,
  "reviewsSort": "newest",
  "filterByScore": 1,
  "reviewsSince": "2026-01-01",
  "language": "en",
  "country": "us"
}
```

| Field | Description |
|---|---|
| `appIds` | Package names or Google Play URLs. Duplicates removed automatically. |
| `searchQuery` | Search the Play Store instead of listing IDs. |
| `maxAppsFromSearch` | How many search results to scrape. |
| `scrapeAppDetails` | Return the app listing data. |
| `scrapeReviews` | Return reviews. Set `maxReviewsPerApp` to 0 to skip. |
| `maxReviewsPerApp` | Reviews per app, up to 20,000. |
| `reviewsSort` | `newest`, `rating`, or `helpfulness`. |
| `filterByScore` | Only reviews with this star rating (1–5). |
| `reviewsSince` | `YYYY-MM-DD`. Stops early once older reviews are reached. |
| `language` / `country` | Two-letter codes. Prices and reviews are country specific. |
| `includeDescription` | Turn off for a lighter dataset without description and screenshots. |
| `annotateAuthorCollisions` | Adds duplicate-name indicators to reviews. |
| `maxConcurrency` | Apps scraped in parallel, 1–10. |

## Output

Results go to a single dataset. App rows have `"type": "app"`, review rows have
`"type": "review"`, so you can filter or split them after export to JSON, CSV, Excel, or
Google Sheets.

App record, shortened:

```json
{
  "type": "app",
  "appId": "com.whatsapp",
  "title": "WhatsApp Messenger",
  "developer": "WhatsApp LLC",
  "installs": "10,000,000,000+",
  "realInstalls": 12279811078,
  "score": 4.6172466,
  "ratings": 242764611,
  "histogram": [10822617, 3393738, 8824605, 21797901, 197925672],
  "free": true,
  "price": 0,
  "genre": "Communication",
  "released": "2010-10-18T00:00:00+00:00",
  "releasedText": "Oct 18, 2010",
  "updated": "2026-09-02T23:17:08+00:00",
  "version": "Varies with device",
  "scrapedAt": "2026-09-05T00:53:24+00:00"
}
```

Review record:

```json
{
  "type": "review",
  "appId": "com.spotify.music",
  "reviewId": "b0b0106e-f2e4-49ac-9d4d-91abcd378b31",
  "content": "current frustration with spotify is that often when I like a song it doesn't stick, it looks like it added to liked songs but it really didn't.",
  "score": 2,
  "thumbsUpCount": 44,
  "appVersion": "9.1.78.2218",
  "at": "2026-09-02T04:02:06+00:00",
  "replyContent": "Hi, thanks for your feedback. When you Like or Save an album, we add it to Your Library for fast access but we don't automatically add all songs to your Liked songs.",
  "repliedAt": "2026-09-02T17:48:19+00:00",
  "authorHash": "667312a54c6ae870",
  "authorHashCount": 1,
  "authorHashApps": 1,
  "scrapedAt": "2026-09-05T01:13:49+00:00"
}
```

## Privacy and GDPR

Google Play review author names are personal data under the GDPR. This scraper does not
return them.

Instead, each review carries `authorHash` — a salted SHA-256 hash of the display name,
truncated to 16 characters. The same display name always produces the same hash, so you
can still group reviews by author, but the name cannot be recovered from the hash.

Google Play does not expose a public user ID, so the hash is derived from the display
name. Two fields tell you how much to trust it:

- `authorHashCount` — how many reviews in this run share the hash. A large number means
  a very common name that should not be treated as one person.
- `authorHashApps` — how many different apps the hash appeared in.

One Google account can leave only one review per app. A repeated hash **within a single
app** therefore always means different people with the same display name. A repeat
**across different apps** may be the same reviewer, and is worth a closer look.

Developer email and postal address are excluded from app records for the same reason.

## Integrations

Run this Google Play scraper from the Apify API, schedule it to monitor app reviews
daily, or connect it to Make, Zapier, Google Sheets, Slack, or an MCP client. Results
export as JSON, CSV, Excel, XML, or RSS.

## FAQ

**How many reviews can I scrape from a Google Play app?**
Up to 20,000 per app per run. Google Play limits how far back its review feed goes, so
very old reviews may not be reachable for any scraper.

**Can I scrape reviews for a specific country or language?**
Yes. Set `country` and `language`. Reviews, prices, and availability differ by country,
and the same app may not exist in every store.

**Can I scrape only negative reviews?**
Set `filterByScore` to 1 or 2 to collect only low-rated reviews.

**How do I monitor new reviews daily?**
Schedule the actor and set `reviewsSince` to yesterday's date with `reviewsSort` set to
`newest`. The run stops as soon as it reaches older reviews.

**Does it work with Apple App Store apps?**
No, but our [App Store Reviews Scraper](https://apify.com/datamill.top/app-store-reviews-scraper)
does, and it returns the same field names so the two datasets merge without renaming
columns.

**Is this an official Google tool?**
No. It is an independent, unofficial Actor with no affiliation to Google LLC. It reads
only publicly visible listing and review content, and it holds no special access or
partnership.

**Why is the author name missing?**
By design — see [Privacy and GDPR](#privacy-and-gdpr). Use `authorHash` to group reviews
by author without storing personal data.

**Is scraping Google Play legal?**
This actor collects publicly visible listing and review content and does not access
private data or bypass authentication. You remain responsible for how you use the data
under the laws that apply to you. This is not legal advice.

**Is this an official Google tool?**
No. It is an independent, unofficial Actor with no affiliation to Google LLC. It reads
only publicly visible listing and review content, and it holds no special access or
partnership.

## About data(mill)

These actors are built and maintained by [data(mill)](https://datamill.top/?utm_source=github&utm_medium=readme&utm_campaign=gplay-scraper),
a data collection studio. We publish the tools we use ourselves — review data comes
without reviewer identities by design.

- Case study with real numbers: [Google Play review scraper case](https://datamill.top/keysy/google-play-reviews-scraper/?utm_source=github&utm_medium=readme&utm_campaign=gplay-scraper)
- The ethics behind the hashed author names: [two app stores, one table](https://datamill.top/keysy/otzyvy-dvuh-storov/?utm_source=github&utm_medium=readme&utm_campaign=gplay-scraper)
- Run it in the cloud without code: [Apify actor page](https://apify.com/datamill.top/google-play-reviews-scraper)
