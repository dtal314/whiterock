from whiterock.mapping.universe import ALL_TICKERS, SECTORS, committee_sectors, map_action, ticker_sectors


def test_tariff_action_benefits_steel_and_hurts_retail():
    links = map_action(
        "Adjusting Imports of Steel and Aluminum Into the United States",
        "The President imposes a 25 percent tariff on imports of steel and aluminum articles under Section 232.",
        None, ["Executive Office of the President"])
    by = {l.sector_id: l for l in links}
    assert "steel_materials" in by and by["steel_materials"].direction == 1
    assert "retail" in by and by["retail"].direction == -1
    assert "steel" in " ".join(by["steel_materials"].matched_keywords).lower()


def test_drug_pricing_hurts_pharma():
    links = map_action("Delivering Most-Favored-Nation Prescription Drug Pricing to American Patients",
                       None, None, ["Executive Office of the President"])
    by = {l.sector_id: l for l in links}
    assert by["pharma"].direction == -1


def test_unrelated_action_maps_nowhere():
    assert map_action("Notice of Meeting of the Advisory Committee on Apprenticeship", None, None, ["Labor Department"]) == []


def test_universe_consistency():
    for s in SECTORS:
        assert s.tickers and s.benchmark and s.keywords and s.agencies
    assert "LMT" in ALL_TICKERS and "defense" in ticker_sectors("LMT")
    assert "defense" in committee_sectors(["HSAS"]) and "banks" in committee_sectors(["SSBK"])
