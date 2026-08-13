"""
Quick sanity checks for the normalisers.
Run with:  python src/test_normalize.py
These are the real messy values from the three CSVs, so if this passes,
the normalisation layer handles the actual data.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import (
    normalise_phone, normalise_date, normalise_ctc, normalise_rate,
    normalise_city, normalise_name, normalise_verified, normalise_status,
)

def check(label, got, want):
    ok = "PASS" if got == want else "FAIL"
    print(f"[{ok}] {label}: got {got!r}, want {want!r}")
    return got == want

results = []

# All five phone formats must collapse to the same 10 digits.
for raw in ["9000000254", "09000000254", "+919000000254", "919000000254", "+91-9000000254"]:
    results.append(check(f"phone {raw}", normalise_phone(raw)[0], "9000000254"))

# The separator decides day/month order.
results.append(check("slash date is MM/DD", normalise_date("07/13/2026")[0], "2026-07-13"))
results.append(check("dash date is DD-MM", normalise_date("24-07-2026")[0], "2026-07-24"))
results.append(check("ISO passes through",  normalise_date("2026-08-08")[0], "2026-08-08"))
results.append(check("text month",          normalise_date("7 Jul 2026")[0], "2026-07-07"))

# Lakhs vs absolute rupees.
results.append(check("ctc lakhs",    normalise_ctc("4.2")[0], 420000))
results.append(check("ctc absolute", normalise_ctc("417964")[0], 417964))

# Rate keeps its unit rather than being converted.
results.append(check("rate hourly",  normalise_rate("1415/hr"), (1415.0, "hr", None)))
results.append(check("rate monthly", normalise_rate("15k/month"), (15000.0, "month", None)))

# City aliases.
for raw in ["GURGAON", "gurugram ", "Gurgaon"]:
    results.append(check(f"city {raw!r}", normalise_city(raw)[0], "Gurugram"))
for raw in ["bangalore", "Bengaluru"]:
    results.append(check(f"city {raw!r}", normalise_city(raw)[0], "Bengaluru"))

# The column-shifted source2 row puts a city into the status field.
# We must NOT silently accept it.
results.append(check("bad status rejected", normalise_status("Pune")[0], None))

results.append(check("verified Y",   normalise_verified("Y")[0], 1))
results.append(check("verified No",  normalise_verified("No")[0], 0))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)