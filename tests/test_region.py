from mifit.region import BASE_HOST, region_for_country_code, regionalize


def test_cn_host_unchanged():
    assert regionalize(BASE_HOST, "cn") == BASE_HOST


def test_non_cn_host_prefixed():
    assert regionalize(BASE_HOST, "de") == "https://de.hlth.io.mi.com/"


def test_country_code_maps():
    assert region_for_country_code("CN") == "cn"
    assert region_for_country_code("DE") == "de"
    assert region_for_country_code("US") == "us"
    assert region_for_country_code("SG") == "sg"
    assert region_for_country_code("IN") == "i2"
    assert region_for_country_code("RU") == "ru"


def test_unknown_country_code_returns_none():
    assert region_for_country_code("ZZ") is None
