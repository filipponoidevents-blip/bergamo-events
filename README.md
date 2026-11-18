# Bergamo Events Tracker

Raccoglie gli eventi di musica da ballo nella zona di Bergamo (+20 km) e li pubblica come calendari.

## Dove sta cosa

- `RULES.md`: le regole decise con Filippo (titoli, serata/preserata, generi, grafiche).
- `config/`: le liste curate (locali, promoter, generi, voti, watchlist, filtri Instagram).
- `data/`: i dati raccolti e gli inserimenti manuali. Non si cancellano.
- `src/`: il programma che legge, filtra e genera calendari e grafiche.
- `docs/archivio-vecchi-md/`: le vecchie note, tenute solo per riferimento.

## Far girare il programma

    pip install -e .
    python -m bget run

Il calendario automatico gira ogni 2 ore su GitHub e pubblica su il branch `feed`.
