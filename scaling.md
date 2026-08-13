# Task 5 — Launching to 5,000 gig workers over one weekend

## What breaks first

**SQLite, within the first hour.** SQLite allows one writer at a time and
locks the whole database file. Every audio submission is a write, and the
merge pipeline and n8n tagging flow write to the same file. At a few
concurrent uploads this is fine; at fifty it produces `database is locked`
errors that surface to workers as failed submissions. This is the first thing
to break and the one most likely to be misread as an app bug.

**The request thread, immediately after.** `extract()` shells out to ffprobe
and decodes the whole file before the HTTP response is sent. A 3-minute
recording on a slow disk can take several seconds. Flask's development server
is single-threaded, so one slow upload blocks every other request behind it.
Workers see timeouts and retry, which makes it worse.

**Disk, by Sunday.** 5,000 submissions at roughly 2 MB averages 10 GB. That
alone is manageable, but it is on the same volume as the database and the
application, and a full disk corrupts SQLite rather than politely refusing
writes.

**Duplicates, throughout.** A worker on a slow connection taps Submit twice.
Nothing in the current design prevents two identical rows — the UUID filename
guarantees they will not overwrite each other, which here works against us.

## What I would change before launch

**Storage: move audio off the app server.** Upload directly to S3 or
Cloudflare R2 using presigned URLs, so the file never transits the
application. This removes the disk ceiling, removes upload bandwidth from the
app, and makes the app horizontally scalable. Store only the object key in the
database.

**Database: Postgres.** Not for raw speed but for concurrent writes. The
schema ports essentially unchanged; `AUTOINCREMENT` becomes `SERIAL` and
`datetime('now')` becomes `now()`. This also lets the merge pipeline and the
audio app run simultaneously, which they currently cannot do safely.

**Processing: move extraction off the request path.** The upload endpoint
should store the file, insert a row with `status = 'pending'`, and return
immediately. A worker process pulls pending rows, runs ffprobe, and fills in
the metadata. Workers get a fast confirmation, the app stops blocking, and a
failed extraction becomes a retryable job rather than a lost recording.

**Duplicates: an idempotency key.** The client generates a UUID per submission
attempt and sends it with the request; a unique constraint on that column
makes a double-tap a no-op rather than a second row. Additionally, a soft check
on (phone, duration, file size) to flag likely re-submissions for review rather
than blocking them — a worker legitimately re-recording after a bad take
should not be refused.

**Limits, enforced server-side.** Maximum duration (say 5 minutes), maximum
size, and an allowlist of formats. The current 25 MB cap is a start but
duration is the metric that actually drives processing cost.

**Failures: make uploads resumable.** These are gig workers on mobile
connections. A dropped connection at 90% currently loses everything. Chunked or
resumable upload matters more here than any server-side optimisation.

**Observability.** At minimum: submissions per hour, extraction failure rate,
p95 upload duration, and the proportion of submissions with `noise_estimate =
noisy`. That last one is the early warning that instructions are unclear —
if a quarter of recordings are unusable, that is a content problem discovered
on Saturday morning rather than the following week.

## Cost, rough order of magnitude

| Item | Estimate |
|---|---|
| Storage, 5,000 × 2 MB | ~10 GB, a few dollars a month on S3 |
| Egress on playback | Depends entirely on review volume; likely exceeds storage |
| Compute | One small instance plus one worker; tens of dollars |
| LLM tagging, 5,000 people | Cents at flash-tier pricing, but rate limits bind before cost does |

Egress is the line item that surprises people. Storage is cheap; a reviewer
streaming every recording twice is not.

## What I would not change

The metadata extraction itself is already correct and cheap — one ffprobe call
plus one decode pass. It does not need optimising, it needs moving off the
request path.

The person-matching logic also stays. It runs once at ingest, not per request,
so its cost is irrelevant at this scale. The `review_queue` design matters more
at 5,000 people than at 54: ambiguous matches should accumulate for a human
rather than being resolved by a threshold nobody chose deliberately.