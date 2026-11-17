"""Sort an event announcement into a category from its text, so only music reaches the calendar."""
from __future__ import annotations

import re

CATEGORIES = {
    "dj_club": "DJ / club night",
    "ballo_da_sala": "Ballo da sala / liscio",
    "alternative": "Alternative",
    "live_music": "Live music",
    "comedy": "Comedy",
    "quiz_games": "Quiz / games",
    "food_drink": "Food & drink",
    "culture": "Culture / talks",
    "other": "Other",
}

_W = r"(?<![\w])(?:{})(?![\w])"
PATTERNS = [
    ("ballo_da_sala", r"ballo da sala|balli da sala|liscio|ballo liscio|balli di gruppo|ballo di gruppo|"
                      r"valzer|mazurka|polka|tango argentino|milonga|orchestra spettacolo|balera"),
    ("dj_club", r"dj|dj set|djset|b2b|line ?up|techno|house|tech house|afro house|deep house|minimal|"
                r"club night|after ?party|afterhour|rave|clubbing|disco ?music|selecta|vinili|vinyl|consolle|console|"
                r"drum ?(?:&|and|n) ?bass|dnb|trance|hard ?style|reggaeton|hip ?hop|urban|commerciale|"
                r"guest dj|resident|warm ?up|closing set|opening set|all night long|dancefloor|dance ?floor"),
    ("live_music", r"live|in concerto|concerto|concerti|band|acustico|acoustic|unplugged|tour|cantautore|"
                   r"jam session|open mic musicale|tributo|tribute"),
    ("comedy", r"comicit[aà]|comedy|stand ?-?up|cabaret|comico|comici|risate|monologhi"),
    ("quiz_games", r"quiz|trivia|torneo|tornei|giochi|gioco|bingo|tombola|karaoke|cervellone|burraco|poker|"
                   r"fantacalcio|freccette|beer pong|biliardo|game night"),
    ("culture", r"presentazione|libro|libri|mostra|esposizione|incontro|talk|laboratorio|workshop|corso|"
                r"rassegna|teatro|cinema|proiezione|reading|conferenza|dibattito|letture|lettere|vernissage|"
                r"inaugurazione della mostra|visita guidata|panel|convegno|convegni|tavola rotonda|forum|summit|keynote|"
                r"intervengono|interviene|interventi|relatori|relatore|talks|tavoli di lavoro|conference|seminario|lectio"),
    ("food_drink", r"aperitivo|apericena|cena|cene|brunch|pranzo|menu|menù|degustazione|pizza|pizzata|"
                   r"cocktail|wine|vino|vini|colazione|pasticceria|burger|sushi|panettone|cioccolata|"
                   r"tagliere|happy hour|drink list|piatti|chef"),
]
_COMPILED = [(cat, re.compile(_W.format(p), re.I)) for cat, p in PATTERNS]


def categorize(text: str, venue_kind: str | None = None, exclude: set[str] = frozenset()) -> str:
    """Ballroom / liscio first (a balera with a DJ is still not a club night); then DJ/club signals win over everything (a dinner with a DJ set is still a DJ night);
    then live music; then the non-music categories in the order above."""
    hits = {cat for cat, rx in _COMPILED if cat not in exclude and rx.search(text or "")}
    if "ballo_da_sala" in hits:
        return "ballo_da_sala"
    if "dj_club" in hits:
        return "dj_club"
    if "live_music" in hits and not ({"comedy", "quiz_games"} & hits):
        return "live_music"
    for cat in ("comedy", "quiz_games", "culture", "food_drink"):
        if cat in hits:
            return cat
    if venue_kind == "club" and "dj_club" not in exclude:
        return "dj_club"  # a dated post from a nightclub with no other clue is a club night
    return "other"
