"""
Task 1: ingest all three CSVs into one clean SQLite database.

Design
------
Two layers:
  source_record  - every raw row, preserved exactly, never modified
  person         - the merged "golden record", one row per real human

Matching cascade, strongest evidence first:
  1. phone_exact  (0.99)  two people rarely share a mobile number
  2. email_exact  (0.98)
  3. name_city    (0.70)  weak - used only when 1 and 2 find nothing,
                          and ONLY when there is exactly one candidate AND
                          no contradicting identifier. Otherwise the record
                          goes to review_queue for a human to decide.
  4. new_person           nothing matched, create a new golden record

File order is source1 -> source2 -> source3 and this is deliberate:
source1 is the only file with BOTH email and phone, so it is the bridge
between source2 (email only) and source3 (phone only).
"""
import csv, json, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from normalize import (
    normalise_phone, normalise_email, normalise_name, name_key,
    normalise_city, normalise_date, normalise_ctc, normalise_rate,
    normalise_status, normalise_verified, normalise_skills,
)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "consultbae.db"
DATA = ROOT / "data"


class Pipeline:
    def __init__(self, conn):
        self.conn = conn
        self.stats = {"rows_read": 0, "merged": 0, "created": 0,
                      "quarantined": 0, "review": 0}

    # ---------------------------------------------------------- logging
    def log_issue(self, source_file, row_num, issue_type, detail, action):
        self.conn.execute(
            "INSERT INTO data_issue (source_file, source_row, issue_type, detail, action_taken)"
            " VALUES (?,?,?,?,?)", (source_file, row_num, issue_type, detail, action))

    def log_review(self, source_file, row_num, desc, candidates, reason):
        self.conn.execute(
            "INSERT INTO review_queue (source_file, source_row, description, candidates, reason)"
            " VALUES (?,?,?,?,?)",
            (source_file, row_num, desc, json.dumps(candidates), reason))
        self.stats["review"] += 1

    def record_issues(self, source_file, row_num, issues, action="normalised"):
        for i in issues:
            if i:
                kind = i.split(":", 1)[0]
                detail = i.split(":", 1)[1] if ":" in i else ""
                self.log_issue(source_file, row_num, kind, detail, action)

    # ---------------------------------------------------------- matching
    def find_person(self, phone, email, nkey, city):
        """Return (person_id, method, confidence, note) or (None, ...)."""
        cur = self.conn
        if phone:
            r = cur.execute("SELECT person_id FROM person WHERE primary_phone=?",
                            (phone,)).fetchone()
            if r:
                return r[0], "phone_exact", 0.99, None
        if email:
            r = cur.execute("SELECT person_id FROM person WHERE primary_email=?",
                            (email,)).fetchone()
            if r:
                return r[0], "email_exact", 0.98, None

        # Fallback: name + city. Deliberately conservative.
        if nkey and city:
            rows = cur.execute(
                "SELECT person_id, primary_phone, primary_email FROM person"
                " WHERE lower(full_name)=? AND city=?", (nkey, city)).fetchall()
            if len(rows) > 1:
                return None, "ambiguous", 0.0, [r[0] for r in rows]
            if len(rows) == 1:
                pid, ex_phone, ex_email = rows[0]
                # Contradicting identifier disqualifies a weak match.
                if phone and ex_phone and phone != ex_phone:
                    return None, "ambiguous", 0.0, [pid]
                if email and ex_email and email != ex_email:
                    return None, "ambiguous", 0.0, [pid]
                return pid, "name_city", 0.70, None
        return None, None, None, None

    def upsert_person(self, pid, name, email, phone, city, source_file, row_num):
        """Create a person, or fill in blanks on an existing one."""
        if pid is None:
            cur = self.conn.execute(
                "INSERT INTO person (full_name, primary_email, primary_phone, city)"
                " VALUES (?,?,?,?)", (name, email, phone, city))
            self.stats["created"] += 1
            return cur.lastrowid

        self.stats["merged"] += 1
        ex = self.conn.execute(
            "SELECT full_name, primary_email, primary_phone, city FROM person"
            " WHERE person_id=?", (pid,)).fetchone()
        ex_name, ex_email, ex_phone, ex_city = ex

        # Prefer the fuller name: "Rohit Verma" beats "R. Verma".
        new_name = ex_name
        if name and len(name) > len(ex_name or ""):
            new_name = name
            self.log_issue(source_file, row_num, "name_variant",
                           f"{ex_name} vs {name}", f"kept fuller form '{name}'")
        if email and ex_email and email != ex_email:
            self.log_issue(source_file, row_num, "multiple_emails",
                           f"person {pid}: {ex_email} and {email}",
                           "kept first as primary, second logged")
        if city and ex_city and city != ex_city:
            self.log_issue(source_file, row_num, "city_conflict",
                           f"person {pid}: {ex_city} vs {city}",
                           "kept first-seen city")
        self.conn.execute(
            "UPDATE person SET full_name=?, primary_email=COALESCE(primary_email,?),"
            " primary_phone=COALESCE(primary_phone,?), city=COALESCE(city,?)"
            " WHERE person_id=?", (new_name, email, phone, city, pid))
        return pid

    def save_source_record(self, source_file, row_num, pid, raw, method, conf):
        self.conn.execute(
            "INSERT INTO source_record (source_file, source_row_num, person_id,"
            " raw_json, match_method, confidence) VALUES (?,?,?,?,?,?)",
            (source_file, row_num, pid, json.dumps(raw), method, conf))

    def add_skills(self, pid, skills):
        for s in skills:
            self.conn.execute(
                "INSERT OR IGNORE INTO person_skill (person_id, skill_name) VALUES (?,?)",
                (pid, s))

    # ---------------------------------------------------------- source 1
    def load_source1(self, path):
        """Naukri applicants: has BOTH email and phone, so it is the bridge file."""
        sf = "source1"
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for n, raw in enumerate(csv.DictReader(fh), start=2):
                self.stats["rows_read"] += 1
                phone, i1 = normalise_phone(raw.get("Phone"))
                email, i2 = normalise_email(raw.get("Email"))
                name,  i3 = normalise_name(raw.get("Full Name"))
                city,  i4 = normalise_city(raw.get("City"))
                iso, rawdate, i5 = normalise_date(raw.get("Applied Date"))
                ctc, unit, i6 = normalise_ctc(raw.get("Current CTC"))
                skills, i7 = normalise_skills(raw.get("Skills"))
                self.record_issues(sf, n, [i1, i2, i3, i4, i5, i6, i7])

                pid, method, conf, cands = self.find_person(phone, email, name_key(name), city)
                if method == "ambiguous":
                    self.log_review(sf, n, f"{name} ({city})", cands,
                                    "same name+city as existing person but identifiers conflict")
                    self.save_source_record(sf, n, None, raw, "quarantined", 0.0)
                    continue
                if pid is None:
                    method, conf = "new_person", 1.0
                pid = self.upsert_person(pid, name, email, phone, city, sf, n)
                self.save_source_record(sf, n, pid, raw, method, conf)
                self.add_skills(pid, skills)
                try:
                    exp = float(raw.get("Experience (Years)") or 0)
                except ValueError:
                    exp = None
                self.conn.execute(
                    "INSERT OR REPLACE INTO applicant_profile (person_id, experience_years,"
                    " ctc_annual_inr, ctc_unit_inferred, applied_date, applied_date_raw)"
                    " VALUES (?,?,?,?,?,?)", (pid, exp, ctc, unit, iso, rawdate))

    # ---------------------------------------------------------- source 2
    def load_source2(self, path):
        """
        Gig workers: email only, NO phone column.
        Two structural defects handled here:
          - one completely blank row
          - one row whose columns are rotated right by exactly one position
        """
        sf = "source2"
        cols = ["email_id", "worker_name", "rate", "location", "status", "skill_tags"]
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for n, raw in enumerate(csv.DictReader(fh), start=2):
                self.stats["rows_read"] += 1

                if all((v or "").strip() == "" for v in raw.values()):
                    self.log_issue(sf, n, "blank_row", "all fields empty", "skipped")
                    self.save_source_record(sf, n, None, raw, "quarantined", 0.0)
                    self.stats["quarantined"] += 1
                    continue

                # Column-shift repair: the email landed one column to the right.
                if "@" not in (raw.get("email_id") or "") and "@" in (raw.get("worker_name") or ""):
                    vals = [raw[c] for c in cols]
                    repaired = vals[1:] + vals[:1]        # rotate left by one
                    self.log_issue(sf, n, "column_shift",
                                   f"fields rotated by one: {vals[0][:40]!r} in email column",
                                   "rotated left by one and re-parsed")
                    raw = dict(zip(cols, repaired))

                email, i1 = normalise_email(raw.get("email_id"))
                name,  i2 = normalise_name(raw.get("worker_name"))
                city,  i3 = normalise_city(raw.get("location"))
                rate, runit, i4 = normalise_rate(raw.get("rate"))
                status, i5 = normalise_status(raw.get("status"))
                skills, i6 = normalise_skills(raw.get("skill_tags"))
                self.record_issues(sf, n, [i1, i2, i3, i4, i5, i6])

                pid, method, conf, cands = self.find_person(None, email, name_key(name), city)
                if method == "ambiguous":
                    self.log_review(sf, n, f"{name} ({city})", cands,
                                    "same name+city as existing person but email differs")
                    self.save_source_record(sf, n, None, raw, "quarantined", 0.0)
                    continue
                if pid is None:
                    method, conf = "new_person", 1.0
                pid = self.upsert_person(pid, name, email, None, city, sf, n)
                self.save_source_record(sf, n, pid, raw, method, conf)
                self.add_skills(pid, skills)
                self.conn.execute(
                    "INSERT OR REPLACE INTO gig_profile (person_id, rate_value, rate_unit, status)"
                    " VALUES (?,?,?,?)", (pid, rate, runit, status))

    # ---------------------------------------------------------- source 3
    def load_source3(self, path):
        """CBNexus contacts: phone only, NO email. Contains a repeated header row."""
        sf = "source3"
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for n, raw in enumerate(csv.DictReader(fh), start=2):
                self.stats["rows_read"] += 1

                if (raw.get("Name") or "").strip() == "Name":
                    self.log_issue(sf, n, "repeated_header",
                                   "header row repeated mid-file", "skipped")
                    self.save_source_record(sf, n, None, raw, "quarantined", 0.0)
                    self.stats["quarantined"] += 1
                    continue

                phone, i1 = normalise_phone(raw.get("Phone Number"))
                name,  i2 = normalise_name(raw.get("Name"))
                city,  i3 = normalise_city(raw.get("City"))
                ver,   i4 = normalise_verified(raw.get("Verified"))
                self.record_issues(sf, n, [i1, i2, i3, i4])
                try:
                    projects = int(raw.get("Projects Completed") or 0)
                except ValueError:
                    projects = None

                pid, method, conf, cands = self.find_person(phone, None, name_key(name), city)
                if method == "ambiguous":
                    self.log_review(sf, n, f"{name} ({city}) phone {phone}", cands,
                                    "same name+city as existing person but phone differs "
                                    "- cannot tell if same human or a namesake")
                    self.save_source_record(sf, n, None, raw, "quarantined", 0.0)
                    continue
                if pid is None:
                    method, conf = "new_person", 1.0
                pid = self.upsert_person(pid, name, None, phone, city, sf, n)
                self.save_source_record(sf, n, pid, raw, method, conf)
                self.conn.execute(
                    "INSERT OR REPLACE INTO contact_profile (person_id, verified, projects_completed)"
                    " VALUES (?,?,?)", (pid, ver, projects))


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    p = Pipeline(conn)
    p.load_source1(DATA / "source1_naukri_applicants.csv")
    p.load_source2(DATA / "source2_gig_workers.csv")
    p.load_source3(DATA / "source3_cbnexus_contacts.csv")
    conn.commit()

    print("=" * 55)
    for k, v in p.stats.items():
        print(f"  {k:14} {v}")
    n_people = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    print(f"  {'unique people':14} {n_people}")
    print("=" * 55)
    print("\nMatch methods:")
    for m, c in conn.execute(
        "SELECT match_method, COUNT(*) FROM source_record GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {m:14} {c}")
    print("\nTop data issues:")
    for t, c in conn.execute(
        "SELECT issue_type, COUNT(*) FROM data_issue GROUP BY 1 ORDER BY 2 DESC LIMIT 12"):
        print(f"  {t:28} {c}")
    print("\nReview queue (needs a human):")
    for r in conn.execute("SELECT source_file, source_row, description, reason FROM review_queue"):
        print(f"  [{r[0]} row {r[1]}] {r[2]}\n      -> {r[3]}")
    conn.close()

if __name__ == "__main__":
    main()