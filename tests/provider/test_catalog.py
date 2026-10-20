from playmatch.catalog_seed import load_catalog


def test_starter_catalog_is_source_linked_without_fabricated_prices():
    games = load_catalog()
    assert 20 <= len(games) <= 30
    assert len({g["id"] for g in games}) == len(games)
    for game in games:
        assert game["metadata_status"] == "curated"
        assert game["store_url"] == f"https://store.steampowered.com/app/{game['steam_app_id']}/"
        assert "price_minor" not in game
        assert "price" not in game
        assert game["estimated_hours"] is None
        assert game["horror"] == "unknown"
        fields = {e["field"] for e in game["evidence"] if e["observed_at"]}
        if game["chinese_support"] == "verified":
            assert "chinese_support" in fields
        if game["coop"] != "unknown":
            assert "coop" in fields


def test_loading_does_not_share_mutable_state():
    first = load_catalog()
    first[0]["themes"].append("do not persist")
    assert "do not persist" not in load_catalog()[0]["themes"]
