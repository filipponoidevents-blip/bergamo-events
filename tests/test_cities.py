"""Multi-city: Bergamo keeps its feed names; other cities live in cities/<name>/ with their own."""
from bget.config import ROOT, Config, all_cities, city_root


def test_bergamo_labels_unchanged():
    cfg = Config.load(city_root("bergamo"))
    assert cfg.root == ROOT
    assert [cfg.label(k) for k in ("feed_local", "feed_alerts", "feed_all", "feed_other")] == \
        ["bergamo.ics", "alerts.ics", "all.ics", "other.ics"]
    assert cfg.label("brand") == "Bergamo Nights"
    assert cfg.label("area") == "BERGAMO E DINTORNI"
    assert cfg.label("handle") == "@seratebergamo"


def test_brescia_has_own_config_and_feeds():
    assert "brescia" in all_cities()
    cfg = Config.load(city_root("brescia"))
    assert cfg.settings["home"]["name"] == "Brescia"
    assert cfg.settings["thresholds"]          # inherited from the main settings
    assert cfg.watchlist["dj_headliners"]      # shared watchlist
    assert cfg.label("feed_local") == "brescia.ics"
    assert cfg.label("feed_alerts") == "brescia_alerts.ics"
    assert cfg.label("area") == "BRESCIA E DINTORNI"
    assert cfg.match_venue("Circus Beat Club").city == "Brescia"
    assert cfg.match_venue("Bolgia") is None   # Bergamo venues stay in Bergamo
