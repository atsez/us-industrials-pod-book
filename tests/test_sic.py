from podbook.sic import (
    AEROSPACE_DEFENCE,
    ELECTRICAL,
    MACHINERY,
    REVIEW_ONLY,
    SIC_SUBSECTOR,
    SIC_TITLES,
    classify_sic,
    is_candidate_sic,
    parse_sic,
)


def test_core_codes_map_to_subsectors():
    assert classify_sic("3721") == AEROSPACE_DEFENCE
    assert classify_sic(3760) == AEROSPACE_DEFENCE
    assert classify_sic("3531") == MACHINERY
    assert classify_sic("3743") == MACHINERY
    assert classify_sic("3613") == ELECTRICAL


def test_tech_energy_and_consumer_codes_are_out():
    for code in (3570, 3571, 3674, 3533, 3559, 3630, 3711, 7372):
        assert classify_sic(code) is None


def test_bad_sic_values():
    assert parse_sic("") is None
    assert parse_sic(None) is None
    assert parse_sic("37A1") is None
    assert classify_sic("") is None


def test_review_list_is_separate_from_universe():
    assert not set(REVIEW_ONLY) & set(SIC_SUBSECTOR)
    assert is_candidate_sic(3559) and classify_sic(3559) is None


def test_every_code_has_a_title():
    assert set(SIC_SUBSECTOR) | set(REVIEW_ONLY) <= set(SIC_TITLES)
