"""Comandi:
  python -m nightlife run [--city bergamo] [--offline]   un giro completo: scoperta, raccolta, classifica, uscite
  python -m nightlife discover [--city bergamo]          solo la scoperta dei locali (OpenStreetMap ecc.)
  python -m nightlife new-city <nome> <lat> <lon> [raggio]   crea la cartella di una città nuova
  python -m nightlife compare <cartella-dati-vecchi>     confronta con un'anagrafica fatta a mano (verifica)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import discover, outputs, pipeline
from .city import City

ROOT = Path(__file__).resolve().parent.parent


def city_dirs(name: str) -> list[Path]:
    base = ROOT / "cities"
    return sorted(p for p in base.iterdir() if (p / "city.yaml").exists()) if name == "all" else [base / name]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="nightlife")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--city", default="all")
    r.add_argument("--offline", action="store_true", help="niente rete: rilegge solo i post già salvati")
    d = sub.add_parser("discover")
    d.add_argument("--city", default="all")
    n = sub.add_parser("new-city")
    n.add_argument("name")
    n.add_argument("lat", type=float)
    n.add_argument("lon", type=float)
    n.add_argument("radius", type=float, nargs="?", default=25)
    c = sub.add_parser("compare")
    c.add_argument("old")
    c.add_argument("--city", default="bergamo")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd == "new-city":
        folder = ROOT / "cities" / a.name.lower().replace(" ", "-")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "city.yaml").write_text(f"""# Città creata con: python -m nightlife new-city. Controlla i valori e salva.
city:
  name: {a.name.title()}
  label: {a.name.title()} e dintorni
  lat: {a.lat}
  lon: {a.lon}
  radius_km: {a.radius:g}
  timezone: Europe/Rome
  big_event_provinces: []     # sigle delle province vicine da cui prendere i grandi eventi (es. [MI, BS])
  page: {{brand: "Serate {a.name.title()}", handle: "", hashtags: []}}
instagram:
  user_id: ""                 # id dell'account Instagram Business che legge i profili
  token_renewed: null
""", encoding="utf-8")
        (folder / "conoscenze.yaml").write_text("# Facoltativo. Vedi REGOLE.md, sezione 'Conoscenze'.\nseeds: []      # profili Instagram da cui partire (locali o promoter importanti)\n", encoding="utf-8")
        print(f"Creata {folder}. Ora: python -m nightlife run --city {folder.name}")
        return

    for folder in city_dirs(a.city):
        city = City(folder)
        if a.cmd == "discover":
            for note in discover.run(city, force_osm=True):
                print(note)
        elif a.cmd == "run":
            res = pipeline.run(city, online=not a.offline)
            outputs.write_all(City(folder), res["events"], res["dropped"], res["notes"])
            print(f"{city.c['name']}: {len(res['events'])} eventi salvati, {len(res['dropped'])} scartati")
            for note in res["notes"]:
                print(" -", note)
        elif a.cmd == "compare":
            from .compare import compare
            print(compare(city, Path(a.old)))


if __name__ == "__main__":
    main(sys.argv[1:])
