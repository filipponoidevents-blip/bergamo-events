# Aggiungere una nuova città

Il tracker funziona per più città. Bergamo sta nella cartella principale (`config/`, `data/`),
ogni altra città in `cities/<nome>/` con le stesse cartelle `config/` e `data/`.
Codice, regole, calendari e grafiche sono gli stessi per tutte.

## Il prompt da usare

Copia questo messaggio nel progetto, cambiando il nome della città:

> Aggiungi **<CITTÀ>** come nuova città del tracker, come abbiamo fatto per Brescia.
> Segui `docs/nuova-citta.md` nel repo bergamo-events: crea `cities/<città>/`, fai la ricerca
> di locali e promoter nel raggio di 20 km (club e discoteche, dance, house, techno, tech house;
> più grandi eventi regionali e nazionali come alert), poi preparami le liste da completare.
> Account Instagram per i post: <@account oppure "nessun post per ora">.

## Cosa fa Claude (passi)

1. **Cartella della città**: copia `cities/brescia/config/` in `cities/<città>/config/` e svuota i dati.
   In `settings.yaml` cambia `home` (centro, raggio 20 km), `alert_provinces` (province vicine),
   `city` (nome dei calendari, riga "<CITTÀ> E DINTORNI", account, hashtag).
   Tutto il resto (pesi dei generi, orari serata/preserata, lettore Instagram) è ereditato.
2. **Ricerca locali** (`venues.yaml`): club, discoteche, locali con DJ entro 20 km, più quelli appena fuori
   che fanno grandi eventi. Fonti: quotidiano locale (articoli "dove ballare"), Resident Advisor (le pagine
   evento hanno gli handle Instagram), Xceed, nightguide.it, zero.eu, siti dei locali.
   Per ogni locale: indirizzo, coordinate, km dal centro, tipo, generi, stato (aperto, da verificare, chiuso),
   handle Instagram solo se confermato da una fonte (`ig_source`), altrimenti vuoto.
3. **Ricerca promoter** (`promoters.yaml`): nomi delle serate e dei crew che compaiono nei locali,
   con generi, locali e handle confermati.
4. **Prima bozza** di `ranking.yaml` (voti dal tipo di locale) e `fascia:` in `venues.yaml`:
   segnate come bozza, le corregge Filippo.
5. **Fonti** (`sources.yaml`): Instagram spento finché Filippo non conferma la lista. Le chiamate possono
   partire dallo stesso account tracker di Bergamo (@seratebergamo): stesso token, nessun segreto nuovo.
6. **Liste da completare** per Filippo (sotto), poi PR. I calendari escono come `<città>.ics`,
   `<città>_alerts.ics`, `<città>_all.ics` sullo stesso sito di quelli di Bergamo.

## Le domande per Filippo (le stesse fatte per Bergamo)

1. **Locali mancanti o chiusi**: quali aggiungere, quali togliere.
2. **Handle Instagram** dei locali e dei promoter senza handle confermato.
3. **Genere abituale** di ogni locale (es. commerciale/house, tech house, latino sempre).
4. **Serata o preserata** per tipo di locale (serve solo quando orari e prezzo non bastano).
5. **Capienza** e **sale** (main room, club room, sala 2...) con la capienza di ogni sala.
6. **Voto del locale** da 1 a 10.
7. **Genere dei promoter** principali; i promoter piccoli non in lista non contano.
8. **Locali solo nel calendario**, mai nelle grafiche (come Fabrique per Bergamo).
9. **Account Instagram** da cui fare i post della città, e se la città ha già i suoi post.

## Comandi

```
python -m bget --city <città> run      # solo quella città
python -m bget --city all run          # tutte (è quello che fa il workflow ogni 2 ore)
python -m bget --city <città> carousel --week 2026-11-23
```
