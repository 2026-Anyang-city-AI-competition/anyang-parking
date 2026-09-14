import pandas as pd

p = "data/processed/parking_access_rules.csv"
d = pd.read_csv(p)

for c in [
    "saturday_access_start",
    "saturday_access_end",
    "sunday_access_start",
    "sunday_access_end",
]:
    if c not in d.columns:
        d[c] = pd.NA

sched = {
    14:  ("10:00", "18:00", None, None),
    22:  ("09:00", "19:00", "09:00", "19:00"),
    161: ("09:00", "19:00", None, None),
    162: ("09:00", "19:00", None, None),
    163: ("09:00", "19:00", None, None),
    164: ("09:00", "19:00", None, None),
    165: ("09:00", "19:00", None, None),
    166: ("09:00", "19:00", None, None),
    167: ("09:00", "19:00", None, None),
    168: ("09:00", "19:00", None, None),
    169: ("09:00", "19:00", None, None),
    170: ("09:00", "19:00", None, None),
    171: ("09:00", "19:00", None, None),
    172: ("09:00", "18:00", None, None),
    241: ("10:00", "21:00", "10:00", "21:00"),
    261: ("10:00", "21:00", "10:00", "21:00"),
    262: ("10:00", "21:00", "10:00", "21:00"),
    56:  ("10:00", "20:00", "10:00", "20:00"),
    23:  ("09:00", "17:00", None, None),
    52:  ("10:00", "18:00", None, None),
    53:  ("10:00", "22:00", "10:00", "22:00"),
    48:  ("10:00", "22:00", "10:00", "22:00"),
}

for pid, (ws, we, ss, se) in sched.items():
    m = d["parking_id"].eq(pid)

    d.loc[m, "weekday_access_start"] = ws
    d.loc[m, "weekday_access_end"] = we

    d.loc[m, "saturday_access_start"] = ss
    d.loc[m, "saturday_access_end"] = se

    d.loc[m, "sunday_access_start"] = pd.NA
    d.loc[m, "sunday_access_end"] = pd.NA

d.to_csv(p, index=False, encoding="utf-8-sig")

print(
    d[d["parking_id"].isin(sched)][
        [
            "parking_id",
            "name",
            "weekday_access_start",
            "weekday_access_end",
            "saturday_access_start",
            "saturday_access_end",
            "sunday_access_start",
            "sunday_access_end",
        ]
    ].to_string(index=False)
)