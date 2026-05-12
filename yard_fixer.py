"""
Yard Fixer - v3
===============
Fixes closed-yard arrival errors in origin.csv using opening hours
and transit times.

Column mapping per row:
  Scheduled Truck Arrival - 1 date/time  = arrival at ORIGIN facility
  Pull time 1                             = departure from ORIGIN (Arrival-1 + 30min)
  Scheduled Truck Arrival - 2 date/time  = arrival at DESTINATION facility
  stop_gap = 30min (always fixed)
  TT       = from TTH.csv (authoritative)

Run types:
  3-leg rail chain (shared Run Structure ID):
    Leg1: FC -> Terminal   (adjustable, constrained by Leg2 rail departure)
    Leg2: Terminal -> Terminal  (FIXED - never touched)
    Leg3: Terminal -> FC   (adjustable)
  E2E (no Run Structure ID): FC -> FC, simple fix

Fix direction: always snap FORWARD to next open window.
  If forward snap breaches Leg2 rail departure -> shift Leg1 earlier.

Files required (same folder as this script):
  origin.csv        your upload file
  dry_run.csv       system dry run output
  fc_hours.txt      FC opening hours
  TerminalHours.csv Terminal opening hours
  TTH.csv           Transit times (Lane, TTH in decimal hours)
  origin_fixed.csv  output (auto-generated)
"""

import csv, os, re, sys
from datetime import datetime, timedelta, date as date_type
from copy import deepcopy

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
FLEET_FILE    = os.path.join(SCRIPT_DIR, "origin.csv")
DRYRUN_FILE   = os.path.join(SCRIPT_DIR, "dry_run.csv")
FC_HOURS_FILE = os.path.join(SCRIPT_DIR, "fc_hours.txt")
TERM_FILE     = os.path.join(SCRIPT_DIR, "TerminalHours.csv")
TTH_FILE      = os.path.join(SCRIPT_DIR, "TTH.csv")
OUTPUT_FILE   = os.path.join(SCRIPT_DIR, "origin_fixed.csv")

DAY_ABBR = {
    "MON":"monday","TUE":"tuesday","WED":"wednesday",
    "THU":"thursday","FRI":"friday","SAT":"saturday","SUN":"sunday",
    "MONDAY":"monday","TUESDAY":"tuesday","WEDNESDAY":"wednesday",
    "THURSDAY":"thursday","FRIDAY":"friday","SATURDAY":"saturday","SUNDAY":"sunday",
}
WEEKDAY_NAMES = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

# ── CSV helpers ───────────────────────────────────────────────────────────────

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames), [dict(r) for r in reader]

def write_csv(path, headers, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

# ── Time / date helpers ───────────────────────────────────────────────────────

def parse_time(t):
    m = re.match(r"^(\d{1,2}):(\d{2})$", (t or "").strip())
    return int(m.group(1))*60 + int(m.group(2)) if m else None

def fmt_time(mins):
    return f"{(mins//60)%24}:{mins%60:02d}"

def parse_date(s):
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try: return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError: pass
    return None

def fmt_date(d):
    return f"{d.month}/{d.day}/{d.year}"

def dt_add(d, mins, delta_mins):
    """Add delta_mins to (date d, time mins). Returns (new_date, new_mins)."""
    total = mins + delta_mins
    new_date = d + timedelta(days=total // 1440)
    return new_date, total % 1440

def dt_sub(d, mins, delta_mins):
    """Subtract delta_mins from (date d, time mins). Returns (new_date, new_mins)."""
    total = mins - delta_mins
    if total < 0:
        days_back = (-total - 1) // 1440 + 1
        total += days_back * 1440
        d = d - timedelta(days=days_back)
    return d, total % 1440

def dt_diff_mins(d1, m1, d2, m2):
    """Minutes from (d1, m1) to (d2, m2). Positive if d2/m2 is later."""
    dt1 = datetime.combine(d1, datetime.min.time()) + timedelta(minutes=m1)
    dt2 = datetime.combine(d2, datetime.min.time()) + timedelta(minutes=m2)
    return int((dt2 - dt1).total_seconds() / 60)

# ── Window parsing ────────────────────────────────────────────────────────────

def parse_windows(raw):
    raw = (raw or "").strip()
    if not raw or raw.lower() == "closed":
        return []
    return [{"open": parse_time(m.group(1)), "close": parse_time(m.group(2))}
            for m in re.finditer(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", raw)
            if parse_time(m.group(1)) is not None and parse_time(m.group(2)) is not None]

# ── Load FC hours (line-by-line, blank-line safe) ────────────────────────────

def load_fc_hours(path):
    regular, special = {}, {}
    if not os.path.exists(path):
        print(f"  [WARN] fc_hours.txt not found: {path}")
        return regular, special
    FC_HDR  = re.compile(r"^([A-Z0-9]{2,10})$")
    DATE_LN = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.*)")
    DAY_LN  = re.compile(r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s*(.*)", re.IGNORECASE)
    current = None
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"): continue
            upper = line.upper()
            if FC_HDR.match(upper) and upper not in DAY_ABBR:
                current = upper
                regular.setdefault(current, {}); special.setdefault(current, {})
                continue
            if current is None: continue
            dm = DATE_LN.match(line)
            if dm:
                try: d = datetime.strptime(dm.group(1), "%Y-%m-%d").date()
                except ValueError: continue
                special[current][d] = parse_windows(dm.group(2)); continue
            day_m = DAY_LN.match(line)
            if day_m:
                regular[current][DAY_ABBR[day_m.group(1).upper()]] = parse_windows(day_m.group(2))
    return regular, special

# ── Load Terminal hours ───────────────────────────────────────────────────────

def load_terminal_hours(path):
    hours = {}
    if not os.path.exists(path):
        print(f"  [WARN] TerminalHours.csv not found: {path}")
        return hours, set()
    _, rows = read_csv(path)
    for row in rows:
        term     = (row.get("Terminal") or "").strip().upper()
        day_raw  = (row.get("Day") or "").strip().upper()
        day_name = DAY_ABBR.get(day_raw)
        if not term or not day_name: continue
        hours.setdefault(term, {})
        windows = []
        for col in ["Open-window1", "Open-window2", "Open-window3"]:
            val = (row.get(col) or "").strip()
            if val and val.lower() not in ("closed", ""):
                windows.extend(parse_windows(val))
        if (row.get("Open-window1") or "").strip().lower() == "closed":
            windows = []
        hours[term][day_name] = windows
    return hours, set(k for k in hours if k)

# ── Load TTH ─────────────────────────────────────────────────────────────────

def load_tth(path):
    tth = {}
    if not os.path.exists(path):
        print(f"  [WARN] TTH.csv not found: {path}")
        return tth
    _, rows = read_csv(path)
    for row in rows:
        lane = (row.get("Lane") or "").strip()
        try: tth[lane] = int(round(float(row["TTH"]) * 60))
        except: pass
    return tth

# ── Hours lookup (FC or Terminal) ─────────────────────────────────────────────

def get_windows(code, check_date, regular, special=None):
    code = code.upper()
    if special and code in special and check_date in special[code]:
        return special[code][check_date]
    day_name = WEEKDAY_NAMES[check_date.weekday()]
    return (regular.get(code) or {}).get(day_name, [])

def find_next_open(code, from_date, from_mins, regular, special=None):
    """
    Find next open slot at or after from_date/from_mins.
    Returns (date, mins, changed, note).
    """
    for offset in range(8):
        chk = from_date + timedelta(days=offset)
        for w in get_windows(code, chk, regular, special):
            o, c = w["open"], w["close"]
            if offset == 0:
                if o <= from_mins <= c:
                    return chk, from_mins, False, "Already in window"
                if from_mins < o:
                    return chk, o, True, f"-> {fmt_date(chk)} {fmt_time(o)}"
                # past close, try next window / next day
            else:
                return chk, o, True, f"-> {fmt_date(chk)} {fmt_time(o)} (next open day)"
    return None, None, False, f"No open window in 7 days for {code}"

def is_open_at(code, chk_date, mins, regular, special=None):
    return any(w["open"] <= mins <= w["close"]
               for w in get_windows(code, chk_date, regular, special))

# ── Row field helpers ─────────────────────────────────────────────────────────

def get_stop(row, n, field):
    """field: 'date' or 'time'. n: 1 or 2."""
    return row.get(f"Scheduled Truck Arrival - {n} {field}", "")

def set_stop(row, n, field, val):
    row[f"Scheduled Truck Arrival - {n} {field}"] = val

def get_pull(row):
    return row.get("Pull time 1", "")

def set_pull(row, val):
    row["Pull time 1"] = val

# ── Leg classification ────────────────────────────────────────────────────────

def is_terminal(code, terminal_set):
    return code.upper() in terminal_set

def classify_leg(row, terminal_set):
    parts = [p.strip() for p in row.get("Lane","").split("->")]
    if len(parts) < 2: return "unknown"
    o, d = parts[0].upper(), parts[-1].upper()
    t_o, t_d = is_terminal(o, terminal_set), is_terminal(d, terminal_set)
    if t_o and t_d:   return "leg2"
    if not t_o and t_d: return "leg1"
    if t_o and not t_d: return "leg3"
    return "e2e"

# ── Error detection ───────────────────────────────────────────────────────────

CLOSED_RE = re.compile(r"The YARD for stop (\w+) is closed at arrival", re.IGNORECASE)

def find_errors(dryrun_rows):
    out = []
    for row in dryrun_rows:
        for m in CLOSED_RE.finditer(row.get("Message", "")):
            out.append({
                "fc":     m.group(1).upper(),
                "lane":   row.get("Lane", ""),
                "run_id": row.get("Run Structure ID", "").strip(),
            })
    return out

# ── Core fix: shift Arrival-2 and back-propagate ──────────────────────────────

def fix_arrival2(fleet_idx, fixed_rows, fc_code, regular, special, tth, log,
                 rail_constraint=None):
    """
    Fix Arrival-2 for the given fleet row.
    Back-propagates to Pull-1 and Arrival-1 using TT and the existing stop gap.
    rail_constraint: (date, mins) of Leg2 rail departure — Arrival-2 must be before this.
    Returns True if a fix was applied.
    """
    row   = fixed_rows[fleet_idx]
    lane  = row.get("Lane","")

    # Read existing times
    a1_date = parse_date(get_stop(row, 1, "date"))
    a1_mins = parse_time(get_stop(row, 1, "time"))
    pt_mins = parse_time(get_pull(row))
    a2_date = parse_date(get_stop(row, 2, "date"))
    a2_mins = parse_time(get_stop(row, 2, "time"))

    if None in (a1_date, a1_mins, pt_mins, a2_date, a2_mins):
        log(f"    [SKIP] {fc_code} ({lane}): missing date/time fields")
        return False

    # Stop gap is always 30min (standard rule)
    stop_gap = 30

    # TT from TTH.csv
    tt_mins = tth.get(lane)
    if tt_mins is None:
        log(f"    [SKIP] {fc_code} ({lane}): no TT in TTH.csv for this lane")
        return False

    # Snap Arrival-2 forward
    new_a2_date, new_a2_mins, changed, note = find_next_open(
        fc_code, a2_date, a2_mins, regular, special)

    if not changed:
        log(f"    [SKIP] {fc_code} ({lane}): {note}")
        return False

    # Check rail constraint (Arrival-2 must be before rail departure)
    if rail_constraint:
        rc_date, rc_mins = rail_constraint
        if new_a2_date > rc_date or (new_a2_date == rc_date and new_a2_mins >= rc_mins):
            log(f"    [WARN] {fc_code} ({lane}): forward snap {fmt_date(new_a2_date)} "
                f"{fmt_time(new_a2_mins)} breaches rail departure "
                f"{fmt_date(rc_date)} {fmt_time(rc_mins)} — cannot fix automatically")
            return False

    # Back-propagate: new Pull-1 = new Arrival-2 - TT
    new_pt_date, new_pt_mins = dt_sub(new_a2_date, new_a2_mins, tt_mins)

    # Back-propagate: new Arrival-1 = new Pull-1 - stop_gap
    new_a1_date, new_a1_mins = dt_sub(new_pt_date, new_pt_mins, stop_gap)

    # Write all three fields
    old_a2 = f"{get_stop(row,2,'date')} {get_stop(row,2,'time')}"
    old_pt = get_pull(row)
    old_a1 = f"{get_stop(row,1,'date')} {get_stop(row,1,'time')}"

    set_stop(fixed_rows[fleet_idx], 2, "date", fmt_date(new_a2_date))
    set_stop(fixed_rows[fleet_idx], 2, "time", fmt_time(new_a2_mins))
    set_stop(fixed_rows[fleet_idx], 1, "date", fmt_date(new_a1_date))
    set_stop(fixed_rows[fleet_idx], 1, "time", fmt_time(new_a1_mins))
    set_pull(fixed_rows[fleet_idx], fmt_time(new_pt_mins))

    log(f"    [FIXED] {fc_code} ({lane})")
    log(f"            Arrival-2 : {old_a2} {note}")
    log(f"            Pull-1    : {old_pt} -> {fmt_time(new_pt_mins)}  (TT={tt_mins}min)")
    log(f"            Arrival-1 : {old_a1} -> {fmt_date(new_a1_date)} {fmt_time(new_a1_mins)}  (gap={stop_gap}min)")
    return True

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  YARD FIXER v3  //  FC + Terminal + Rail Chain Logic")
    print("=" * 64)

    for path, label in [(FLEET_FILE,"origin.csv"),(DRYRUN_FILE,"dry_run.csv"),
                        (FC_HOURS_FILE,"fc_hours.txt"),(TERM_FILE,"TerminalHours.csv"),
                        (TTH_FILE,"TTH.csv")]:
        if not os.path.exists(path):
            print(f"\n[ERROR] Missing: {label}  ({path})"); sys.exit(1)

    print("\n[1] Loading files...")
    fleet_h, fleet_rows    = read_csv(FLEET_FILE)
    _, dry_rows            = read_csv(DRYRUN_FILE)
    fc_reg, fc_spec        = load_fc_hours(FC_HOURS_FILE)
    term_reg, term_set     = load_terminal_hours(TERM_FILE)
    tth                    = load_tth(TTH_FILE)

    print(f"    Fleet rows     : {len(fleet_rows)}")
    print(f"    Dry run rows   : {len(dry_rows)}")
    print(f"    FC hours       : {len(fc_reg)} FCs")
    print(f"    Terminals      : {len(term_set)}")
    print(f"    TT lanes       : {len(tth)}")

    print("\n[2] Scanning for closed-yard errors...")
    errors = find_errors(dry_rows)
    if not errors:
        print("    None found. origin.csv is already clean.\n"); sys.exit(0)
    for e in errors:
        print(f"    CLOSED YARD  FC={e['fc']:8s}  Lane={e['lane']:25s}  Run={e['run_id'] or 'E2E'}")

    # Fleet index: (run_id, lane) -> row index
    fleet_index = {(r.get("Run Structure ID","").strip(), r.get("Lane","")): i
                   for i, r in enumerate(fleet_rows)}

    # Run groups: run_id -> [(row, fleet_idx), ...]
    run_groups = {}
    for i, r in enumerate(fleet_rows):
        rid = r.get("Run Structure ID","").strip()
        if rid:
            run_groups.setdefault(rid, []).append((r, i))

    print("\n[3] Calculating fixes...")
    fixed_rows = deepcopy(fleet_rows)
    log_lines  = []
    def log(msg): print(msg); log_lines.append(msg)

    for err in errors:
        run_id = err["run_id"]
        lane   = err["lane"]
        fc     = err["fc"]

        fleet_idx = fleet_index.get((run_id, lane))
        if fleet_idx is None:
            log(f"    [SKIP] No fleet row for run={run_id or 'E2E'} lane={lane}"); continue

        fleet_row = fleet_rows[fleet_idx]
        leg_type  = classify_leg(fleet_row, term_set)

        # ── E2E direct ──────────────────────────────────────────────────────
        if leg_type == "e2e" or not run_id:
            # fc is destination (Node 2) — choose hours source
            if is_terminal(fc, term_set):
                reg, spec = term_reg, None
            else:
                reg, spec = fc_reg, fc_spec
            fix_arrival2(fleet_idx, fixed_rows, fc, reg, spec, tth, log)
            continue

        # ── 3-leg rail chain ─────────────────────────────────────────────────
        if leg_type == "leg2":
            log(f"    [SKIP] Leg2 is fixed rail — {fc} on {lane} not touched")
            continue

        # Find Leg2 in this run to get rail departure constraint
        rail_constraint = None
        group = run_groups.get(run_id, [])
        for g_row, g_idx in group:
            if classify_leg(g_row, term_set) == "leg2":
                # Rail departure = Pull time 1 of Leg2 row
                pt_date = parse_date(get_stop(g_row, 1, "date"))
                pt_mins = parse_time(get_pull(g_row))
                if pt_date and pt_mins is not None:
                    rail_constraint = (pt_date, pt_mins)
                break

        if leg_type == "leg1":
            # Error is at destination terminal of Leg1
            # Use terminal hours for the destination
            parts = [p.strip() for p in lane.split("->")]
            dest  = parts[-1].upper()
            fix_arrival2(fleet_idx, fixed_rows, dest, term_reg, None, tth, log,
                         rail_constraint=rail_constraint)

        elif leg_type == "leg3":
            # Error is at destination FC of Leg3
            # Use FC hours for destination
            parts = [p.strip() for p in lane.split("->")]
            dest  = parts[-1].upper()
            if is_terminal(dest, term_set):
                reg, spec = term_reg, None
            else:
                reg, spec = fc_reg, fc_spec
            fix_arrival2(fleet_idx, fixed_rows, dest, reg, spec, tth, log)

        else:
            log(f"    [SKIP] Could not classify leg for {fc} on {lane} run={run_id}")

    print("\n[4] Writing output...")
    write_csv(OUTPUT_FILE, fleet_h, fixed_rows)
    fixed_n = sum(1 for l in log_lines if "[FIXED]" in l)
    skip_n  = sum(1 for l in log_lines if "[SKIP]"  in l)
    warn_n  = sum(1 for l in log_lines if "[WARN]"  in l)
    print(f"    Saved  : origin_fixed.csv")
    print(f"    Fixed  : {fixed_n}")
    print(f"    Skipped: {skip_n}")
    print(f"    Warned : {warn_n}")
    print("\n" + "="*64)
    print("  Done. Upload origin_fixed.csv to the system.")
    print("="*64 + "\n")

if __name__ == "__main__":
    main()