from shapely.geometry import LineString, MultiLineString, Polygon

from sedona_benchmark.prepare import _line_parts


def test_line_parts_handles_line_and_multi_line():
    first = LineString([(0, 0), (1, 1)])
    second = LineString([(1, 1), (2, 1)])
    assert _line_parts(first) == [first]
    assert _line_parts(MultiLineString([first, second])) == [first, second]


def test_line_parts_drops_non_lines():
    assert _line_parts(Polygon([(0, 0), (1, 0), (0, 1)])) == []
