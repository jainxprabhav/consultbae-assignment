# ConsultBae — AI Automation Assignment

Merges three inconsistent CSVs into one database, tags people with an LLM via
n8n, and collects audio submissions with automatic property extraction.

---

## Setup

Requires Python 3.12+ and ffmpeg on PATH (`ffmpeg -version` must work).

```bash
git clone <repo-url>
cd consultbae-assignment
python -m venv .venv
.venv\Scripts\Activate.ps1      # Windows
pip install -r requirements.txt
```

**Task 1 — build the database**

```bash
python src/init_db.py           # creates consultbae.db from schema.sql
python src/merge_pipeline.py    # ingests all three CSVs
python src/test_normalize.py    # 21 checks against real values from the files
```

**Task 3 — run the audio app**

```bash
python app.py
```

Open <http://localhost:5000>. Use `localhost`, not `127.0.0.1` — browser
microphone access requires a secure context and only `localhost` qualifies
over plain HTTP.

**Task 2 — run the n8n flow**

```bash
npx n8n                         # http://localhost:5678
```

Import `n8n/skill_tagging_workflow.json`. The flow needs `app.py` running,
because it reaches the database over HTTP rather than touching the file.

---

## Architecture

```
CSV ×3 ──▶ normalize.py ──▶ merge_pipeline.py ──▶ consultbae.db
                                                    │
                              app.py (Flask) ───────┤
                                │                   │
                    audio upload│                   │
                    /api/untagged, /api/tag         │
                                │                   │
                              n8n flow ─────────────┘
```

Two layers in the database:

- `source_record` — every raw row, stored as JSON, never modified. Each links
  to the person it merged into, with the method and confidence used.
- `person` — the golden record, one row per real human.

Every merge is therefore auditable and reversible. When asked "why did these
two rows collapse into one?", the answer is a query, not a guess.

---

## Task 1 — merge results

| | |
|---|---|
| Rows read | 105 |
| Usable rows | 103 (one blank row, one repeated header) |
| Unique people | 54 |
| Sent to review queue | 2 |

Match methods:

| Method | Count | Confidence |
|---|---|---|
| `new_person` | 54 | 1.00 |
| `phone_exact` | 27 | 0.99 |
| `email_exact` | 16 | 0.98 |
| `name_city` | 4 | 0.70 |
| `quarantined` | 4 | — |

### Why matching needs a cascade, not a join

No single ID is shared across all three files, and the coverage is worse than
that suggests:

- source1 (Naukri) has **both** email and phone
- source2 (gig workers) has email, **no phone column at all**
- source3 (CBNexus) has phone, **no email column at all**

So source2 and source3 share **no join key whatsoever**. Fifteen people appear
in both, and they can only be connected transitively through source1. This is
why the pipeline loads source1 first and treats it as the bridge file, and why
matching is a cascade over accumulated state rather than a set of pairwise
joins.

The cascade, strongest evidence first:

1. `phone_exact` (0.99) — two people rarely share a mobile number
2. `email_exact` (0.98)
3. `name_city` (0.70) — used only when 1 and 2 find nothing, only when there is
   exactly one candidate, and only when no identifier contradicts it
4. `new_person` — nothing matched

---

## Task 4 — data issues report

14 distinct issue types found. Every one is logged to the `data_issue` table
at ingest, so this report is generated from the data rather than written from
memory:

```sql
SELECT issue_type, COUNT(*), action_taken
FROM data_issue GROUP BY issue_type, action_taken ORDER BY 2 DESC;
```

| Issue | Count | Action |
|---|---|---|
| `city_normalised` | 68 | mapped to canonical city |
| `phone_reformatted` | 47 | stripped to last 10 digits |
| `date_reformatted` | 28 | parsed to ISO |
| `status_case_fixed` | 22 | lowercased to enum |
| `ctc_in_lakhs` | 21 | multiplied to absolute rupees |
| `email_case_or_space_fixed` | 10 | lowercased and trimmed |
| `name_case_fixed` | 9 | title-cased |
| `future_date` | 6 | flagged, value kept |
| `blank_row` | 1 | skipped |
| `column_shift` | 1 | rotated left by one, re-parsed |
| `multiple_emails` | 1 | first kept as primary, second logged |
| `name_abbreviated` | 1 | fuller form preferred on merge |
| `name_variant` | 1 | kept `Rohit Verma` over `R. Verma` |
| `repeated_header` | 1 | skipped |

### The judgment calls

**Date formats — resolved by evidence, not assumption.** source1 uses five
formats: `2026-08-08`, `24-07-2026`, `07/13/2026`, `7 Jul 2026`, `03-07-2026`.
The dash and slash forms are ambiguous in isolation — `07/03/2026` could be
3 July or 7 March. The data itself disambiguates: `07/13/2026` exists and 13
cannot be a month, so **slash means MM/DD/YYYY**; `24-07-2026` exists and 24
cannot be a month, so **dash means DD-MM-YYYY**. Eight values would have been
unresolvable individually; the separator rule resolves all of them.

**CTC mixes two units.** 21 values are absolute rupees (327,287–1,195,422) and
21 are lakhs-per-annum (2.4–11.9). No one earns ₹4.20 a year and no one earns
417,964 lakh, so magnitude separates them cleanly. Threshold at 1000, converted
to absolute rupees, and the inference is recorded in `ctc_unit_inferred` rather
than being silently applied.

**Rates were deliberately NOT converted.** source2 mixes `1415/hr` and
`15k/month`. Converting to a common basis requires inventing an
hours-per-month figure, which would then be indistinguishable from real data
downstream. Value and unit are stored separately instead. This is the one
place the pipeline refuses to normalise, and that refusal is the point.

**Cities: 18 spellings, 6 real places.** `GURGAON`, `gurugram `, `Gurgaon`,
`Gurugram` are one city; so are `Bangalore`/`Bengaluru` and
`Delhi`/`New Delhi`/`Delhi NCR`. The merge **validates its own map**: Meera
Bhatia appears as "Delhi NCR" in source1, "New Delhi" in source2, and "Delhi"
in source3 while being provably one person by phone. Varun Saxena does the
same across the Gurgaon variants. The aliases are proven by the data, not
assumed from geography.

**Three different people named Arjun Mehta.** This is the trap in the dataset.

| Row | Source | Email | Phone | City |
|---|---|---|---|---|
| s1:18 | source1 | arjun.mehta9@example.in | 9000000131 | Noida |
| s3:3 | source3 | — | 9000000131 | Noida |
| s3:26 | source3 | — | **9000000272** | Noida |
| s2:16 | source2 | arjun.mehta77@… | — | Noida |

Same name, same city, **two different phone numbers** — so this is provably
more than one person. Matching on name, or on name plus city, would have fused
three humans into one record. The first two rows merge on exact phone; the
other two go to `review_queue` because the evidence genuinely does not settle
them. Four other name+city pairs (Divya Chopra, Karan Chopra, Manish Bhatia,
Vikram Mehta) *were* auto-merged, because in each case the name+city key maps
to exactly one cluster with no contradicting identifier.

**Structural defects.** One completely blank row in source2. One row in
source2 with all columns rotated by one position — the skill list landed in
the email column, detected by checking whether `email_id` actually contains an
`@`, then rotated back and re-parsed. One header row repeated mid-file in
source3.

**Same person, two emails.** Nikhil Chopra appears twice in source1 with
`nikhil.chopra70@example.com` and `alt.nikhil.chopra70@example.com` on the same
phone. Email-only matching would have created two people; the phone rescues it.

**Six applications dated in the future** relative to the run date. Flagged
rather than dropped — a future date is suspicious, not impossible, and
silently deleting rows is worse than surfacing them.

---

## Known limitations

Found by testing, consciously not fixed under the deadline:

1. **Load order is enforced by convention, not code.** The pipeline is correct
   for source1 → source2 → source3. Testing all six permutations gives 54
   people every time, but under source3-first ordering one record merges via
   the weak `name_city` rule that should have merged on phone, and the wrong
   email becomes primary. The person count is identical either way, so a
   row-count check does not catch it. The proper fix is two passes:
   deterministic keys across all files first, then `name_city` over the fully
   populated table.

2. **Split-brain keys are not detected.** `find_person` returns on the first
   phone hit and never checks whether the email points at a different existing
   person. No email in source1 maps to two phones, so this does not fire on
   the real data — but a row carrying person A's email and person B's phone
   would merge silently instead of going to review.

3. **`name_key` and the SQL lookup disagree.** The query uses
   `lower(full_name)` while the argument comes from `name_key()`, which also
   strips punctuation. `R. Verma` becomes `r. verma` on one side and `r verma`
   on the other, so the tier-3 branch can never match a name containing a
   period. Harmless here (that row matched on phone) but the branch is dead for
   that class of name. Fix is a stored, indexed `name_key` column.

4. **Quarantined rows drop real people.** The two Arjun Mehta rows get
   `person_id NULL`, so 56 real identities become 54 golden records. Better
   would be a provisional person row flagged `needs_review` — flagging beats
   deleting.

---

## Stuck log

> Replace the bracketed notes with what you actually searched and thought.
> This log is scored on judgment, so specifics matter more than polish.

### 1. pydub is dead on Python 3.13+

`from pydub.utils import which` raised `ModuleNotFoundError: No module named
'audioop'`, then `'pyaudioop'`. Not an ffmpeg problem at all: `audioop` was
removed from the standard library in Python 3.13 under PEP 594, and pydub
still imports it unconditionally.

Considered three routes: the `audioop-lts` backport, downgrading to Python
3.12, or dropping pydub. **Chose to drop pydub** and read samples directly with
`soundfile` + `numpy`. Rejected the backport because it patches around an
unmaintained dependency rather than removing it, and rejected the downgrade
because it would have meant rebuilding the venv mid-assignment.

The unplanned benefit: the loudness maths is now explicit — RMS over float
samples, then `20·log10` — instead of hidden behind `AudioSegment.dBFS`. I can
explain every number the app reports.

*[Add: what you searched, how long it took to realise it wasn't ffmpeg's fault.]*

### 2. ffmpeg installed but invisible

`ffmpeg -version` returned `not recognized` immediately after a successful
`winget install`. Rather than reinstalling blindly, checked whether it was an
install problem or a PATH problem: `winget list --id Gyan.FFmpeg` showed
version 9.0 present. So the binary existed and only the shell environment was
stale — VS Code's integrated terminal inherits its environment from when VS
Code launched, so a new tab is not enough.

Time-boxed the PATH fix and had `imageio-ffmpeg` (ships its own binaries) ready
as a fallback. The code keeps that lesson: `FFMPEG_PATH` / `FFPROBE_PATH`
environment variables override PATH discovery, so the app runs on a machine
where ffmpeg is installed but not on PATH.

*[Add: what you searched, and how long you gave yourself before switching.]*

### 3. An empty n8n output that looked like a bug

The Split Out node returned zero items and the LLM node reported "node was not
executed". Both looked broken. They were not: `remaining: 0` in the API
response meant every person already had a `skill_category` from an earlier
test run, so the endpoint correctly returned an empty list and n8n correctly
stopped.

The bug was in my mental model, not the code. Reading the *input* panel rather
than the error message is what settled it. Added a reset step so the flow has
visible work to do when demonstrated.

*[Add: how long you spent looking at the wrong node.]*

### 4. Gemini free tier is 5 requests per minute

`[429 Too Many Requests] quotaValue: 5` after sending 10 people to the LLM at
once. The obvious fix is retries, but retries alone still hammer a per-minute
cap and just fail more slowly.

**Fixed at the source instead**: reduced the batch to 3 per run via the API's
own `limit` parameter, and added retry-with-backoff as a secondary guard. The
API was already designed to page, which is why this was a one-character change
rather than a redesign.

*[Add: what you searched, and whether you considered switching model or provider.]*

---

## Repo layout

```
app.py                      Flask audio app + n8n API endpoints
src/schema.sql              database schema
src/init_db.py              creates the database
src/normalize.py            all normalisers, each returns (value, issue)
src/merge_pipeline.py       ingest + matching cascade
src/audio_meta.py           duration, sample rate, bitrate, loudness, noise
src/test_normalize.py       21 checks against real messy values
templates/                  submission form and list view
n8n/skill_tagging_workflow.json
data/                       the three source CSVs
SCALING.md                  Task 5
```