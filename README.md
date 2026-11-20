# Nightlife tracker (versione 2)

Trova da solo locali, promoter e DJ di una città, raccoglie gli eventi, li divide in **serata** e **preserata**,
li mette in classifica e prepara i calendari e i dati per i post. Gratis: niente AI a pagamento.

## Cosa esce (cartella `cities/<città>/out/`, pubblicata anche sul branch `feed-v2`)

| File | A cosa serve |
|---|---|
| `bergamo_serate.ics`, `bergamo_preserate.ics`, `bergamo_grandi_eventi.ics`, `bergamo_tutto.ics` | calendari da aggiungere al telefono |
| `bergamo_eventi.csv` | tutti gli eventi in una tabella (si apre con Excel) |
| `bergamo_promoter.md` / `.json` | calendario per promoter: quanto è contesa ogni sera, generi che mancano, sere libere |
| `bergamo_infografiche.json` | dati pronti per i post della settimana e del mese, con gli @ per la didascalia |
| `bergamo_locali.csv`, `bergamo_profili.csv` | l'anagrafica trovata: locali (indirizzo, km, capienza, follower) e profili Instagram |
| `bergamo_stato.md` | com'è andato l'ultimo giro, fonti saltate, eventi da controllare |

## Come funziona

1. **Scoperta**: OpenStreetMap (discoteche e locali con musica), siti dei locali, Xceed, agende dei giornali,
   e Instagram a catena (chi viene taggato in un evento viene controllato e, se è un promoter, seguito).
2. **Raccolta**: post Instagram dei profili seguiti, pagine Xceed dei club, agenda de L'Eco di Bergamo, articoli di Prima Bergamo.
3. **Lettura**: data, orari, sala, prezzo, promoter, ospiti, genere e tipo di intrattenimento, con un'affidabilità.
4. **Classifica**: regole in `REGOLE.md`.
5. **Uscite**: i file qui sopra, ogni 2 ore su GitHub.

## Comandi

    pip install -e .
    python -m nightlife run --city bergamo        # un giro completo
    python -m nightlife run --offline              # rilegge solo i post già salvati
    python -m nightlife new-city brescia 45.5398 10.2195 25
    python -m nightlife compare archivio/database-v1   # quanto della vecchia lista a mano ha trovato da solo

## Cartelle

- `nightlife/` il programma, uguale per ogni città. `nightlife/defaults/` valori di base, DJ importanti, grandi marchi.
- `cities/<città>/city.yaml` centro, raggio, pagina social. `conoscenze.yaml` facoltativo.
- `cities/<città>/data/` scritto dal programma: anagrafica, post letti, eventi (con lo storico).
- `archivio/` la versione 1: note e database salvati, usati solo per il confronto.
- `skill/SKILL.md` la procedura per rifare tutto su un'altra città.

## Google Maps (facoltativo)

Trova i club che mancano su OpenStreetMap. Serve il segreto `GOOGLE_PLACES_KEY` su GitHub: progetto su console.cloud.google.com con fatturazione collegata, "Places API (New)" abilitata, chiave API limitata a quel servizio, quota giornaliera a 100. Circa 20 richieste al mese, dentro la quota gratuita.

## Rinnovare il token di Instagram (ogni ~60 giorni, il promemoria esce nel calendario)

1. Apri developers.facebook.com → Strumenti → Graph API Explorer, scegli la tua app e l'account della pagina.
2. Genera il token con i permessi `instagram_basic`, `pages_show_list`, `business_management`, poi "Estendi" il token (Access Token Debugger → Extend).
3. Su GitHub: Settings → Secrets and variables → Actions → `IG_ACCESS_TOKEN` → Update, incolla il token.
4. In `cities/<città>/city.yaml` aggiorna `token_renewed` con la data di oggi.
