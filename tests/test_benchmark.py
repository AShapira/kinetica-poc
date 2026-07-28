from sedona_benchmark.benchmark import _fetch_all_kinetica


class _PagedCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.offset = 0
        self.requested_sizes = []

    def fetchmany(self, size):
        self.requested_sizes.append(size)
        page = self.rows[self.offset : self.offset + size]
        self.offset += len(page)
        return page


def test_fetch_all_kinetica_consumes_more_than_one_default_page():
    cursor = _PagedCursor((number,) for number in range(10_001))

    rows = _fetch_all_kinetica(cursor)

    assert len(rows) == 10_001
    assert rows[0] == (0,)
    assert rows[-1] == (10_000,)
    assert cursor.requested_sizes == [5_000, 5_000, 5_000, 5_000]
