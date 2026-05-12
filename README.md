# Yard Fixer v3 — README

Automatically fixes closed-yard arrival errors in your upload file
using FC opening hours, terminal hours, and transit times.
Handles 3-leg rail chains and E2E direct loads with full date rollover support.

---

## Folder setup

All files must be in the **same folder** as `yard_fixer.py`:

```
YardFixer/
  yard_fixer.py         ← the script (never edit this)
  fc_hours.txt          ← FC opening hours (you maintain)
  TerminalHours.csv     ← Terminal opening hours (you maintain)
  TTH.csv               ← Transit times per lane (you maintain)
  origin.csv            ← your upload file (rename to this)
  dry_run.csv           ← dry run output from the system (rename to this)
  origin_fixed.csv      ← output, auto-generated, upload this
```

---

## How to run

1. Put your upload file in the folder, rename it to `origin.csv`
2. Run it through the system, download the dry run, rename to `dry_run.csv`
3. Open VS Code terminal in the folder and run:
   ```
   python yard_fixer.py
   ```
4. Check the terminal output for FIXED / SKIP / WARN lines
5. Upload `origin_fixed.csv` to the system

---

## Time calculation logic

The script uses this chain for every row:

```
Arrival-1  = arrival at origin facility      (Scheduled Truck Arrival - 1)
Pull-1     = Arrival-1 + 30min              (Pull time 1)
Arrival-2  = Pull-1 + TT                   (Scheduled Truck Arrival - 2)
```

Date rollover is handled automatically. Example:

```
Arrival-1 date  10/05/2026
Arrival-1 time  20:44   (at LIN8)
Pull-1          21:14   (+ 30min)
TT              5h
Arrival-2 time  02:14   (at RMXP)
Arrival-2 date  11/05/2026   ← rolls to next day automatically
```

When a fix is applied, all three fields are updated together:
- Arrival-2 snapped to next valid opening window
- Pull-1 recalculated as Arrival-2 minus TT
- Arrival-1 recalculated as Pull-1 minus 30min

---

## Run types

**3-leg rail chain** — rows sharing the same Run Structure ID:

```
Leg 1:  FC -> Terminal        adjustable
Leg 2:  Terminal -> Terminal  FIXED — rail schedule, never touched
Leg 3:  Terminal -> FC        adjustable
```

- Leg 1 fix: snaps terminal arrival forward, but if that breaches
  the Leg 2 rail departure time — logs a WARN and skips (manual fix needed)
- Leg 2: never touched under any circumstance
- Leg 3: snaps FC arrival to next opening, recalculates Pull-1 and Arrival-1

**E2E direct** — no Run Structure ID:
Simple forward snap with TT back-propagation, no rail constraint.

Terminal detection is based on the actual list in TerminalHours.csv,
not name pattern — so FCs starting with R (like RMU1) are never misidentified.

---

## What the script only fixes

Only errors matching this message from the dry run:
  "The YARD for stop {FC} is closed at arrival"

Everything else in the Message column is ignored.
Original origin.csv is never modified — output always goes to origin_fixed.csv.

---

## File maintenance

### fc_hours.txt — FC opening hours

Paste blocks directly from the internal system. One block per FC.
Blank lines within a block are fine — special dates always belong
to the FC code above them regardless of blank lines.

  DTM2
  MON 06:45-14:45, 15:15-23:15
  TUE 00:00-06:00, 06:30-14:45, 15:15-23:30
  SAT 00:00-06:00, 06:30-14:45, 15:15-23:15
  SUN Closed

  2026-05-14  Closed
  2026-05-25  08:00-16:00

Rules:
- Days: MON TUE WED THU FRI SAT SUN
- Closed day: SAT Closed
- Two windows: MON 00:00-06:00, 14:30-23:59
- Special dates: YYYY-MM-DD  HH:mm-HH:mm  (overrides weekly for that date only)
- Lines starting with # are comments, ignored
- To add a new FC: paste its block anywhere, order does not matter

### TerminalHours.csv — Terminal opening hours

CSV columns: Terminal, Day, Open-window1, Open-window2, Open-window3

  RBET,Monday,06:00-22:00,,
  RBET,Sunday,closed,,

To add a new terminal: add 7 rows (one per day) in the same format.
The script builds its terminal list from this file — no code changes needed.

### TTH.csv — Transit times

CSV columns: Lane, TTH (decimal hours)

  LIN8->RMXP,5.00
  RKOR->DUS4,3.50

To add a lane: just add a row, order does not matter.
If a lane is missing the script prints [SKIP] no TT in TTH.csv for this lane.

Lanes currently missing that need to be added:
  LIN8->RMXP, TRN1->RMXP, RKOR->DUS4, RKOR->DUS2, TRN1->RPAR

---

## Reading the terminal output

[FIXED] DTM1 (RNET->DTM1)
        Arrival-2 : 5/16/2026 16:17 -> 5/16/2026 16:30
        Pull-1    : 13:15 -> 13:00  (TT=198min)
        Arrival-1 : 5/16/2026 13:00 -> 5/16/2026 12:30  (gap=30min)
All three fields updated, chain is consistent.

[SKIP] RMXP (LIN8->RMXP): no TT in TTH.csv for this lane
→ Add this lane to TTH.csv and re-run.

[SKIP] Leg2 is fixed rail — RBEU on RNET->RBEU not touched
→ Correct behaviour, Leg2 is never adjusted.

[WARN] forward snap breaches rail departure — cannot fix automatically
→ Next open window is after the train departs. Needs manual fix.

---

## Row order

Does not matter for either file. Matching is by Run Structure ID + Lane,
not row position. Dry run can be sorted or left as downloaded.

---

## Safe to re-run

The script never modifies origin.csv. If output looks wrong, delete
origin_fixed.csv and re-run. Safe to run as many times as needed.

If you lose this setup or need changes, paste yard_fixer.py into
Claude, ChatGPT, AWS Q, or Copilot and describe what you need.