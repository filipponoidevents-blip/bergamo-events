# Regole

Valgono per ogni città. I numeri si cambiano in `nightlife/defaults/settings.yaml` (o nel `city.yaml` di una città).

## Zona
- In zona: entro **25 km** dal centro.
- Fuori zona entra solo un **grande evento**: un marchio o festival in `defaults/brands.yaml` (elrow, Nameless, Fabrique...),
  un locale con voto 9 o più, o un DJ "top". Gli altri eventi lontani si scartano.

## Serata o preserata
In quest'ordine, vince la prima regola che decide:
1. scelta a mano (`conoscenze.yaml`, voce `fix`);
2. grande evento o festival → serata;
3. orari: inizia dalle 23:00 (o dopo mezzanotte) e non finisce presto → serata; inizia prima delle 22:00 → preserata;
   finisce entro le 03:00 → preserata; finisce dalle 03:00 → serata;
4. tipo: aperitivo, quiz, karaoke, comedy, live in un bar → preserata;
5. prezzo: lista, biglietto, drink incluso → serata; ingresso gratis → preserata;
6. tipo di locale: discoteca → serata, bar → preserata.
Il motivo è scritto in ogni evento (`fascia_why`).

## Classifica (voto 0-100)
Ogni voce vale 0-10, poi media pesata.
- **Serata**: rilevanza del locale ×3, capienza ×1.5, follower del locale ×1.5, rilevanza del promoter ×2, ospite ×2.
- **Preserata**: rilevanza del promoter ×3, vicinanza al centro ×2.5, capienza ×1.5, follower del locale ×3.
- Follower: 500 = 0, 100.000 = 10 (scala logaritmica). Capienza: 50 = 0, 3.000 = 10.
- Rilevanza del locale: voto scritto a mano, altrimenti calcolato da tipo di locale, follower e capienza.
- Rilevanza del promoter: voto scritto a mano, altrimenti dai suoi follower.
- Ospite: DJ "top" 10, "notable" 8, altrimenti dai follower del profilo taggato. Un nome non taggato e non in lista vale 0.
- Una seconda sala (club room, lab room...) vale il 60% del locale.

## Scoperta di locali e promoter
- Locali: OpenStreetMap, Google Maps (una volta al mese, se c'è la chiave), siti dei locali, profili Instagram che sono locali.
- Promoter: profili citati nei post letti (anche senza @), controllati per nome, biografia e follower; seguiti se citati almeno 2 volte.
- Pagine nightlife della città (es. @nightlifebergamo), trovate provando i nomi tipici: si leggono solo per scoprire promoter e locali, mai come fonte del calendario. Un promoter citato lì viene seguito subito.
- Esclusi: scuole di ballo, sale per matrimoni, sale slot, locali per adulti.

## Capienza
Quella scritta (OpenStreetMap o `conoscenze.yaml`), altrimenti una stima: discoteca 500, live club 400, open air 1000, bar 80,
corretta con i follower. Nel file si vede se è "dichiarata" o "stima".

## Generi
Prima la scelta a mano, poi il locale se ha un genere fisso, poi il genere del promoter scritto a mano, poi il testo del post,
poi l'abitudine imparata (il genere che quel promoter o locale fa di solito, da almeno 2 eventi).
Per i post le serate sono divise in: House / Elettronica, Hip Hop / Trap, Commerciale (dance, hit, revival, latino), Altro.

## Titoli e grafiche
- Calendario: `[Genere] Locale · Serata w/ Headliner`, le parti che mancano si tolgono.
- Grafiche: riga 1 "Serata @ Locale" (senza nome serata: l'headliner), riga 2 "genere, headliner, sala". Nomi dei profili, mai gli @.
  Solo serate DJ/club; pagine per famiglia di genere, preserate su pagine bianche, poi i grandi eventi fuori zona. Locali con `in_posts: false` solo nel calendario.
- Telefono: solo l'ora di inizio; dopo mezzanotte va alle 23:59 della sera prima.

## Nessuna approvazione
Un evento esce da solo se l'affidabilità è almeno 0,5 (data scritta, orario, locale riconosciuto, più fonti che concordano).
Sotto resta in "da controllare" nel file di stato e esce appena un'altra fonte lo conferma.

## Conoscenze (facoltative, `cities/<città>/conoscenze.yaml`)
`seeds` profili da cui partire · `venues` voto, capienza, sale, genere di un locale · `promoters` genere, voto, nome della serata ·
`artists` DJ importanti in più · `hide` eventi da togliere · `fix` correzioni di un evento · `events` eventi che nessuno pubblica.
Per Bergamo il lavoro a mano della versione 1 (voti, capienze, sale, generi, 85 promoter, artisti, nascosti) è in `conoscenze-importate.yaml`;
`conoscenze.yaml` viene dopo e vince. Un locale delle conoscenze assorbe i doppioni trovati dal sistema a meno di 300 m con una parola del nome in comune.

## Profili che non diventano eventi
Pagine scout (servono solo a scoprire), organizzatori nazionali (biografia "in Italia", "nel mondo", tour, booking: i loro post parlano di tutta Italia)
e profili che organizzano eventi ma non serate (catering, matrimoni, raduni, mercatini, band). Un locale sconosciuto conta "in zona" solo se
il post è di un locale della zona, non di un promoter (che gira anche fuori). Un locale cercato per nome sulla mappa deve avere davvero quel nome.

## Limiti noti
- Instagram: solo profili pubblici Business/Creator; circa 20 profili ogni 2 ore (limite di Meta). Le storie non si leggono.
- TicketSMS, Upass, Eventbrite, Fourvenues, Resident Advisor: bloccano le letture automatiche o non hanno dati leggibili. Le loro serate arrivano comunque dai post Instagram.
