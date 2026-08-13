"""
Normalisation helpers for the ConsultBae merge pipeline.

Every function here takes one messy value from a CSV and returns
(clean_value, issue_or_None). The issue is a short string describing what was
wrong, so the caller can log it to the data_issue table. That is why these
return tuples instead of plain values: detecting a problem and recording it
are the same operation.
"""
import re
from datetime import datetime

# --------------------------------------------------------------- phone
def normalise_phone(raw):
    """
    Indian mobile numbers arrive as: 9000000268, 09000000287, +919000000254,
    919000000231, +91-9000000131.
    Strategy: discard every non-digit, then keep the last 10 digits. The
    country code (91) and trunk prefix (0) are both prefixes, so trimming from
    the right is safe.
    """
    if raw is None or str(raw).strip() == "":
        return None, "missing_phone"
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 10:
        return None, f"phone_too_short:{raw}"
    if len(digits) > 13:
        return None, f"phone_too_long:{raw}"
    ten = digits[-10:]
    if ten[0] not in "6789":
        return ten, f"phone_implausible_start:{raw}"
    issue = None if digits == ten else f"phone_reformatted:{raw}"
    return ten, issue

# --------------------------------------------------------------- email
def normalise_email(raw):
    if raw is None or str(raw).strip() == "":
        return None, "missing_email"
    e = str(raw).strip().lower()
    if "@" not in e or "." not in e.split("@")[-1]:
        return None, f"malformed_email:{raw}"
    issue = None if e == str(raw) else f"email_case_or_space_fixed:{raw}"
    return e, issue

# --------------------------------------------------------------- name
def normalise_name(raw):
    if raw is None or str(raw).strip() == "":
        return None, "missing_name"
    n = re.sub(r"\s+", " ", str(raw)).strip()
    issue = None
    if n.isupper() or n.islower():
        issue = f"name_case_fixed:{raw}"
    # "R. Verma" - single-letter initial instead of a given name
    if re.match(r"^[A-Za-z]\.?\s", n):
        issue = f"name_abbreviated:{raw}"
    return n.title(), issue

def name_key(name):
    """Aggressive key for fuzzy matching: lowercase, no punctuation."""
    if not name:
        return None
    return re.sub(r"[^a-z ]", "", str(name).lower()).strip()

# --------------------------------------------------------------- city
CITY_CANON = {
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "new delhi": "Delhi", "delhi": "Delhi", "delhi ncr": "Delhi",
    "noida": "Noida", "pune": "Pune",
}

def normalise_city(raw):
    if raw is None or str(raw).strip() == "":
        return None, "missing_city"
    c = re.sub(r"\s+", " ", str(raw)).strip().lower()
    if c in CITY_CANON:
        canon = CITY_CANON[c]
        issue = None if str(raw) == canon else f"city_normalised:{raw}->{canon}"
        return canon, issue
    return str(raw).strip().title(), f"city_unrecognised:{raw}"

# --------------------------------------------------------------- date
def normalise_date(raw):
    """
    Five formats appear in source1. The ambiguous pair is 07/03/2026 vs
    03-07-2026. Resolved by evidence in the data itself:
      - 07/13/2026 exists; 13 cannot be a month, so SLASH = MM/DD/YYYY
      - 24-07-2026 exists; 24 cannot be a month, so DASH  = DD-MM-YYYY
    So the separator determines the order. This is a real inference from the
    dataset, not a guess.
    """
    if raw is None or str(raw).strip() == "":
        return None, None, "missing_date"
    s = str(raw).strip()
    if re.match(r"^\d{4}-", s):
        fmt = "%Y-%m-%d"
    elif "/" in s:
        fmt = "%m/%d/%Y"
    elif re.search(r"[A-Za-z]", s):
        fmt = "%d %b %Y"
    elif "-" in s:
        fmt = "%d-%m-%Y"
    else:
        return None, s, f"unparseable_date:{raw}"
    try:
        d = datetime.strptime(s, fmt)
    except ValueError:
        return None, s, f"unparseable_date:{raw}"
    iso = d.strftime("%Y-%m-%d")
    issue = None if iso == s else f"date_reformatted:{raw}->{iso}"
    if d > datetime.now():
        issue = f"future_date:{raw}"
    return iso, s, issue

# --------------------------------------------------------------- money
def normalise_ctc(raw):
    """
    source1 mixes absolute rupees (417964) with lakhs-per-annum (4.2).
    A real salary is never Rs 4.2, and never 417964 lakh, so the magnitude
    disambiguates cleanly. Threshold at 1000.
    Returns (annual_inr, unit_inferred, issue).
    """
    if raw is None or str(raw).strip() == "":
        return None, None, "missing_ctc"
    try:
        v = float(str(raw).strip())
    except ValueError:
        return None, None, f"unparseable_ctc:{raw}"
    if v < 1000:
        return int(round(v * 100_000)), "lakhs", f"ctc_in_lakhs:{raw}"
    return int(round(v)), "absolute", None

def normalise_rate(raw):
    """
    source2 mixes '1415/hr' and '15k/month'. We deliberately do NOT convert
    to a common basis: that needs an invented hours-per-month figure which
    would then look like real data downstream. Store value and unit.
    """
    if raw is None or str(raw).strip() == "":
        return None, None, "missing_rate"
    s = str(raw).strip().lower()
    m = re.match(r"^([\d.]+)\s*(k?)\s*/\s*(hr|hour|month|mo)$", s)
    if not m:
        return None, None, f"unparseable_rate:{raw}"
    val = float(m.group(1)) * (1000 if m.group(2) == "k" else 1)
    unit = "hr" if m.group(3).startswith(("hr", "hour")) else "month"
    return val, unit, None

# --------------------------------------------------------------- enums
def normalise_status(raw):
    if raw is None or str(raw).strip() == "":
        return None, "missing_status"
    s = str(raw).strip().lower()
    if s in ("active", "inactive", "paused"):
        return s, (None if s == str(raw) else f"status_case_fixed:{raw}")
    return None, f"unknown_status:{raw}"

def normalise_verified(raw):
    if raw is None or str(raw).strip() == "":
        return None, "missing_verified"
    s = str(raw).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return 1, None
    if s in ("n", "no", "false", "0"):
        return 0, None
    return None, f"unknown_verified:{raw}"

def normalise_skills(raw):
    if raw is None or str(raw).strip() == "":
        return [], "missing_skills"
    parts = [re.sub(r"\s+", " ", p).strip().lower() for p in str(raw).split(",")]
    return sorted({p for p in parts if p}), None