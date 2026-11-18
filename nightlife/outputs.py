"""Le uscite: calendari per il telefono, tabella per Excel, calendario per i promoter, dati per le infografiche, report."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .city import City
from .util import day_note

WEEKDAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MONTHS_IT = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
CSV_FIELDS = ["date", "weekday", "fascia", "start", "end", "score", "rank_in_night", "party", "headliner", "promoter_name", "promoter_instagram", "promoter_followers",
              "venue", "room", "address", "city", "km", "venue_instagram", "venue_followers", "venue_type", "capacity", "capacity_source", "genre", "family", "type",
              "entry", "price", "tickets", "scope", "day_note", "confidence", "status", "sources", "fascia_why", "lead_days", "first_seen"]


def title(e: dict) -> str:
    """[Genere] Locale · Serata w/ Headliner. Le parti che mancano si tolgono."""
    head = f"[{e['genre']}] " if e.get("genre") else ""
    room = f" ({e['room']})" if e.get("room") and e["room"] != "main room" else ""
    what = " w/ ".join(x for x in [e.get("party"), e.get("headliner")] if x)
    return f"{head}{e['venue']}{room}" + (f" · {what}" if what else "")


def _esc(t: str) -> str:
    return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def ics(city: City, events: list[dict], name: str, extra: list[tuple[str, str, str]] = ()) -> str:
    """Solo l'ora di inizio. Chi inizia dopo mezzanotte va alle 23:59 della sera prima, così sul telefono resta nel giorno giusto."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nightlife//IT", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_esc(name)}", f"X-WR-TIMEZONE:{city.c['timezone']}", "REFRESH-INTERVAL;VALUE=DURATION:PT2H"]
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        d = date.fromisoformat(e["date"])
        start = e.get("start")
        if start and start < "06:00":
            start = "23:59"
        desc = [f"{e['fascia'].upper()} · voto {e['score']}/100", e.get("fascia_why") or ""]
        if e.get("address"):
            desc.append(f"📍 {e['address']}" + (f" ({e['km']} km)" if e.get("km") is not None else ""))
        for label, key in [("Locale", "venue_instagram"), ("Promoter", "promoter_instagram")]:
            if e.get(key):
                desc.append(f"{label}: @{e[key]}")
        if e.get("guests"):
            desc.append("Ospiti: " + ", ".join(g["name"] for g in e["guests"][:6]))
        if e.get("price"):
            desc.append(f"Prezzo da {e['price']} €")
        if e.get("competition") and e["competition"]["events_same_night"]:
            c = e["competition"]
            desc.append(f"Stessa sera: altri {c['events_same_night']} eventi ({c['same_family']} dello stesso genere)")
        desc += e.get("urls") or []
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@nightlife-{city.slug}", f"DTSTAMP:{stamp}"]
        lines.append(f"DTSTART;TZID={city.c['timezone']}:{d:%Y%m%d}T{start.replace(':', '')}00" if start else f"DTSTART;VALUE=DATE:{d:%Y%m%d}")
        lines += [_fold(f"SUMMARY:{_esc(title(e))}"), _fold(f"LOCATION:{_esc(', '.join(x for x in [e['venue'], e.get('address')] if x))}"),
                  _fold("DESCRIPTION:" + _esc("\n".join(x for x in desc if x))), f"CATEGORIES:{e['fascia'].upper()},{_esc(e['family'])}"]
        if e.get("urls"):
            lines.append(_fold(f"URL:{e['urls'][0]}"))
        lines.append("END:VEVENT")
    for uid, day, text in extra:
        lines += ["BEGIN:VEVENT", f"UID:{uid}@nightlife-{city.slug}", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day.replace('-', '')}", _fold(f"SUMMARY:{_esc(text)}"), "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def promoter_calendar(city: City, events: list[dict], days: int = 90) -> tuple[dict, str]:
    """Per ogni sera: quanto è affollata, chi c'è, quanta capienza è già impegnata, quali generi mancano.
    Serve a un promoter per scegliere la data e il genere del suo party."""
    today = date.today()
    by_night = defaultdict(list)
    for e in events:
        if e["scope"] == "zona" and e["fascia"] == "serata" and e["nightlife"]:
            by_night[e["date"]].append(e)
    # quanto conta di solito ogni giorno della settimana (dallo storico): serve per dire "sera libera" o "sera piena"
    weekday_load = defaultdict(list)
    for d, evs in by_night.items():
        weekday_load[date.fromisoformat(d).weekday()].append(sum(x["score"] for x in evs))
    fams = list(city.settings["genre_families"])
    # oltre l'ultima data annunciata i dati sono pochi: le sere "libere" si cercano solo fin lì
    # = l'ultima settimana che ha ancora almeno 3 serate annunciate
    dates = sorted(by_night)
    horizon = today.isoformat()
    for dd in dates:
        week = [x for x in dates if (date.fromisoformat(dd) - date.fromisoformat(x)).days in range(7)]
        if sum(len(by_night[x]) for x in week) >= 3:
            horizon = dd
    nights, rows = [], []
    for i in range(days):
        d = today + timedelta(days=i)
        evs = sorted(by_night.get(d.isoformat(), []), key=lambda e: -e["score"])
        load = round(sum(e["score"] for e in evs), 1)
        typical = sorted(weekday_load.get(d.weekday(), [0]))[len(weekday_load.get(d.weekday(), [0])) // 2]
        note = day_note(d)
        is_weekend_like = d.weekday() in (4, 5) or (note and "prefestivo" in note) or (note in ("Halloween", "Capodanno (veglione)", "Vigilia di Ferragosto"))
        fam_count = Counter(e["family"] for e in evs)
        level = "piena" if load >= max(150, typical * 1.2) else ("media" if load >= 60 else ("libera" if is_weekend_like else "tranquilla"))
        if d.isoformat() > horizon:
            level = "non ancora annunciata"
        night = {"date": d.isoformat(), "weekday": WEEKDAYS_IT[d.weekday()], "note": note, "weekend_like": bool(is_weekend_like), "events": len(evs),
                 "load": load, "level": level, "capacity_taken": sum(e["capacity"] or 0 for e in evs),
                 "by_family": {f: fam_count.get(f, 0) for f in fams}, "missing_families": [f for f in fams[:-1] if not fam_count.get(f)],
                 "top": [{"title": title(e), "score": e["score"], "capacity": e["capacity"]} for e in evs[:5]]}
        nights.append(night)
    # testo semplice
    md = [f"# Calendario per promoter · {city.c['label']}", "",
          f"Aggiornato {datetime.now():%d/%m/%Y %H:%M}. Solo serate in zona (entro {city.c['radius_km']} km). "
          "Carico = somma dei voti delle serate della stessa sera: più è alto, più la sera è contesa.", "",
          "## Le prossime sere da weekend più libere", ""]
    free = sorted([n for n in nights if n["weekend_like"] and n["date"] <= horizon], key=lambda n: n["load"])[:8]
    md.insert(4, f"Gli annunci arrivano di solito 1-3 settimane prima: le date dopo il {date.fromisoformat(horizon):%d/%m} sono ancora quasi vuote.")
    md.insert(5, "")
    for n in sorted(free, key=lambda n: n["date"]):
        md.append(f"- **{n['weekday']} {date.fromisoformat(n['date']):%d/%m}**{' · ' + n['note'] if n['note'] else ''}: {n['events']} serate, carico {n['load']}"
                  + (f", manca {', '.join(n['missing_families'])}" if n["missing_families"] else ""))
    md += ["", "## Sera per sera", "", "| Data | Sera | Serate | Carico | Capienza impegnata | Generi | Le più forti |", "|---|---|---|---|---|---|---|"]
    for n in nights:
        if (not n["events"] and not n["weekend_like"]) or n["date"] > horizon:
            continue
        gen = ", ".join(f"{f.split(' /')[0]} {c}" for f, c in n["by_family"].items() if c)
        md.append(f"| {date.fromisoformat(n['date']):%d/%m} {n['weekday'][:3]}{' (' + n['note'] + ')' if n['note'] else ''} | {n['level']} | {n['events']} | {n['load']} | "
                  f"{n['capacity_taken']} | {gen or '-'} | {'; '.join(t['title'] for t in n['top'][:3]) or '-'} |")
    return {"city": city.c["name"], "generated": datetime.now().isoformat(timespec="minutes"), "nights": nights}, "\n".join(md) + "\n"


def _line(e: dict) -> dict:
    """Le due righe di un evento nelle grafiche: 'Serata @ Locale' e 'Genere, ospite'. Nomi dei profili, mai gli @."""
    room = e.get("room") if e.get("room") and e["room"] != "main room" else None
    first = e.get("party") or e.get("headliner") or e.get("promoter_name")
    line1 = f"{first} @ {e['venue']}" if first else e["venue"]
    second = [x for x in [e.get("genre") if e["fascia"] == "serata" else (e.get("genre") or e.get("type")), e.get("headliner") if e.get("party") else None, room] if x]
    return {"line1": line1, "line2": ", ".join(second), "time": e.get("start") or "00:00", "score": e["score"], "fascia": e["fascia"], "family": e["family"]}


def infographics(city: City, events: list[dict]) -> dict:
    """Dati pronti per i post: settimana (lunedì-domenica) e mese, serate divise per genere, preserate a parte, grandi eventi in fondo."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_end = monday + timedelta(days=6)
    first = today.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    def pack(a: date, b: date) -> dict:
        sel = [e for e in events if a.isoformat() <= e["date"] <= b.isoformat() and e["nightlife"] and e.get("in_posts", True) and e["status"] == "pubblicato"]
        serate = defaultdict(lambda: defaultdict(list))
        pre, big = defaultdict(list), []
        for e in sorted(sel, key=lambda e: (e["date"], -e["score"], e.get("start") or "99")):
            if e["scope"] != "zona":
                big.append({"date": e["date"], **_line(e), "km": e.get("km")})
            elif e["fascia"] == "serata":
                serate[e["family"]][e["date"]].append(_line(e))
            else:
                pre[e["date"]].append(_line(e))
        handles = Counter(h for e in sel for h in [e.get("venue_instagram"), e.get("promoter_instagram"), *[g.get("instagram") for g in e.get("guests") or []]] if h)
        dots = sorted({e["date"] for e in sel if e["scope"] == "zona" and e["fascia"] == "serata"})
        return {"from": a.isoformat(), "to": b.isoformat(), "dates_with_serate": dots,
                "serate_by_family": {f: dict(sorted(d.items())) for f, d in serate.items()}, "preserate": dict(sorted(pre.items())), "big_events": big,
                "caption_handles": ["@" + h for h, _ in handles.most_common(30)], "hashtags": city.c.get("page", {}).get("hashtags", [])}
    nxt_monday = monday + timedelta(days=7)
    return {"page": city.c.get("page", {}), "area": city.c["label"], "generated": datetime.now().isoformat(timespec="minutes"),
            "this_week": pack(monday, week_end), "next_week": pack(nxt_monday, nxt_monday + timedelta(days=6)),
            "this_month": {"month": MONTHS_IT[first.month], **pack(first, next_month - timedelta(days=1))},
            "next_month": {"month": MONTHS_IT[next_month.month], **pack(next_month, (next_month + timedelta(days=32)).replace(day=1) - timedelta(days=1))}}


def report(city: City, events: list[dict], dropped: list[dict], notes: list[str]) -> str:
    up = [e for e in events if e["date"] >= date.today().isoformat()]
    check = [e for e in up if e["status"] == "da controllare"]
    md = [f"# Stato · {city.c['label']}", "", f"Ultimo giro: {datetime.now():%d/%m/%Y %H:%M}", "",
          f"- Eventi in arrivo: **{len(up)}** ({sum(e['fascia'] == 'serata' for e in up)} serate, {sum(e['fascia'] == 'preserata' for e in up)} preserate, "
          f"{sum(e['scope'] != 'zona' for e in up)} grandi eventi fuori zona)",
          f"- Locali in anagrafica: **{len(city.venues)}**, profili Instagram conosciuti: **{len(city.registry['accounts'])}**, seguiti: **{len(city.watch_list())}**",
          f"- Da controllare (affidabilità bassa, non pubblicati): **{len(check)}**", ""]
    tok = city.settings["instagram"].get("token_renewed")
    if tok:
        left = 60 - (date.today() - date.fromisoformat(str(tok))).days
        if left <= 10:
            md += [f"> ⚠️ Il token di Instagram scade tra circa {left} giorni: va rinnovato (README, 'Rinnovare il token').", ""]
    if notes:
        md += ["## Cosa è successo in questo giro", ""] + [f"- {n}" for n in notes] + [""]
    if check:
        md += ["## Da controllare", "", "Non serve fare niente: se una fonte conferma l'evento, esce da solo. Per correggerlo a mano usa `conoscenze.yaml`.", ""]
        md += [f"- {e['date']} · {title(e)} · affidabilità {e['confidence']} · {', '.join(e['urls'][:1])}" for e in sorted(check, key=lambda e: e["date"])]
        md.append("")
    return "\n".join(md) + "\n"


def write_all(city: City, events_by_id: dict, dropped: list[dict], notes: list[str]) -> None:
    out = city.out
    out.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    allev = sorted(events_by_id.values(), key=lambda e: (e["date"], e["fascia"] != "serata", -e["score"]))
    pub = [e for e in allev if e["status"] == "pubblicato" and e["date"] >= (date.today() - timedelta(days=30)).isoformat()]
    zone_night = [e for e in pub if e["nightlife"]]
    slug, label = city.slug, city.c["label"]
    tok = city.settings["instagram"].get("token_renewed")
    reminders = []
    if tok:
        due = date.fromisoformat(str(tok)) + timedelta(days=50)
        reminders.append((f"token-{due}", due.isoformat(), "⚠️ Rinnovare il token di Instagram (scade tra 10 giorni)"))
    files = {
        f"{slug}_serate.ics": ics(city, [e for e in zone_night if e["fascia"] == "serata" and e["scope"] == "zona"], f"Serate · {label}", reminders),
        f"{slug}_preserate.ics": ics(city, [e for e in zone_night if e["fascia"] == "preserata" and e["scope"] == "zona"], f"Preserate · {label}"),
        f"{slug}_grandi_eventi.ics": ics(city, [e for e in zone_night if e["scope"] != "zona"], f"Grandi eventi · fuori {label}"),
        f"{slug}_tutto.ics": ics(city, zone_night, f"Nightlife · {label}", reminders),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    with (out / f"{slug}_eventi.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore", delimiter=";")
        w.writeheader()
        for e in allev:
            w.writerow({**e, "sources": ", ".join(e["sources"]), "score": str(e["score"]).replace(".", ","), "km": str(e.get("km") or "").replace(".", ",")})
    (out / f"{slug}_eventi.json").write_text(json.dumps([e for e in allev if e["date"] >= today], ensure_ascii=False, indent=1), encoding="utf-8")
    data, md = promoter_calendar(city, [e for e in allev if e["status"] == "pubblicato"])
    (out / f"{slug}_promoter.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{slug}_promoter.md").write_text(md, encoding="utf-8")
    (out / f"{slug}_infografiche.json").write_text(json.dumps(infographics(city, allev), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{slug}_stato.md").write_text(report(city, allev, dropped, notes), encoding="utf-8")
    venues = sorted(city.venues, key=lambda v: (v.get("km") if v.get("km") is not None else 999))
    with (out / f"{slug}_locali.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["nome", "tipo", "indirizzo", "comune", "km", "instagram", "follower", "capienza", "capienza_fonte", "voto", "voto_auto", "fonti"])
        for v in venues:
            w.writerow([v["name"], v.get("type"), v.get("address"), v.get("city"), v.get("km"), v.get("instagram"), city.followers(v.get("instagram")),
                        v.get("capacity"), v.get("capacity_source"), v.get("vote"), v.get("auto_vote"), ", ".join(v.get("sources") or [])])
    with (out / f"{slug}_profili.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["instagram", "nome", "tipo", "follower", "citato_in_eventi", "seguito", "leggibile"])
        for h, a in sorted(city.registry["accounts"].items(), key=lambda x: -(x[1].get("followers") or 0)):
            w.writerow([h, a.get("name"), a.get("kind"), a.get("followers"), a.get("mentions"), h in set(city.watch_list()), a.get("readable")])
