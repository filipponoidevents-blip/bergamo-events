# Dove sta ogni regola

Ogni informazione ha un solo posto. Se due file sembrano dire la stessa cosa, vale l'ordine scritto qui sotto.

| Cosa | File | Note |
|---|---|---|
| Locali: indirizzo, distanza, tipo, handle Instagram | `venues.yaml` | un locale = una voce, con gli alias |
| Locale serata / preserata / "orari" | `venues.yaml` → `fascia:` | usato solo se orari e prezzo non bastano |
| Voto del locale, capienza, sale | `ranking.yaml` → `venues:` | `{score: 7, capacity: 500, rooms: ...}` |
| Voti delle sale di default (main room 10, club room 6...) | `ranking.yaml` → `rooms:` | |
| Pesi dell'ordine (locale, sala, capienza, party, ospiti) | `ranking.yaml` → `weights:` | |
| Promoter: nome, handle, nome serata (`party_name`) | `promoters.yaml` | |
| Voto del party | `ranking.yaml` → `promoters:` | vuoto = calcolato dai follower Instagram |
| Generi (tuoi, vincono su tutto) | `genres.yaml` | `nights` (una serata) > `always` (locale sempre quel genere, es. Garota = Latino) > `promoters` > `venues` |
| Locale solo nel calendario, mai nelle grafiche | `venues.yaml` → `in_posts: false` | Fabrique, Magazzini Generali, Ippodromo |
| Generi trovati con la ricerca | `promoters.yaml` / `venues.yaml` → `genres:` | usati solo se `genres.yaml` non dice niente |
| Account Instagram letti | `sources.yaml` → `accounts:` | `venue:` = account del locale; `monthly: true` = programma mensile (Zero) |
| Approva / scarta un post, rinomina una serata, forza serata/preserata | `ig_decisions.yaml` | `approve`, `reject`, `titles`, `fascia` |
| Togli un evento | `hidden.yaml` | per testo, locale o data |
| Eventi inseriti a mano | `../data/manual_events.yaml` | es. Premiere a Zero, 21/11 |
| DJ e artisti importanti (ospiti) | `watchlist.yaml` | un nome qui = voto ospite 10 |
| Raggio, orari serata/preserata, pesi generi | `settings.yaml` | |

## Regole che il programma applica da solo

- **Serata o preserata:**
  - Prima vale la tua scelta in `ig_decisions.yaml`.
  - I grandi eventi musicali sono sempre serata.
  - Poi contano gli orari: inizio dalle 23:30 e fine dalle 03:00 = serata.
  - Poi prezzo, lista o drink incluso = serata; ingresso gratis = preserata.
  - Per ultimo conta il tipo di locale.
- **Nome della serata:**
  - Se posta un promoter, la serata prende il nome del promoter.
  - Se posta un locale, prende il nome del promoter taggato.
  - Altrimenti prende il nome scritto nel post.
- **Genere:** valgono i tuoi generi del promoter, se il nome del party è nel titolo. Poi quello che c'è scritto nel post, poi il genere del promoter, poi quello del locale.
- **Due sale nella stessa sera** (club room, lab room, sala 2) restano due serate separate.
- **Bar:** non hanno voto. Vanno dopo i club, dal più vicino al centro, e in ordine alfabetico a distanza simile.
