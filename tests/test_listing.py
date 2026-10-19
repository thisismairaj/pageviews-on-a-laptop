from pvl.listing import parse_listing

HTML = (
    '<a href="pageviews-20240101-000000.gz">pageviews-20240101-000000.gz</a>'
    "                       01-Jan-2024 01:03            43896263\n"
    '<a href="pageviews-20240101-230000.gz">pageviews-20240101-230000.gz</a>'
    "                       02-Jan-2024 00:02            41000000\n"
    '<a href="readme.html">readme.html</a>   01-Jan-2024 01:03   100\n'
)


def test_parse_listing_reads_name_hour_and_size():
    files = parse_listing(HTML, "https://example.test/base")
    assert [(f.day, f.hour, f.size) for f in files] == [
        ("2024-01-01", 0, 43896263),
        ("2024-01-01", 23, 41000000),
    ]
    assert files[0].url == "https://example.test/base/pageviews-20240101-000000.gz"
