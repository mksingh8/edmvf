"""IR-012: the reader's memory use changed, its behaviour must not.

`reference_read` is a frozen copy of the reader as it was before the change
(whole file decoded to a str, parsed through io.StringIO). The tests compare the
current `read_csv_dataset` with it, byte for byte, on an explicit matrix and on
seeded random inputs: same headers, rows and line spans, or the same exception
type, message, location and cause.
"""

import csv
import io
import random
from pathlib import Path

import pytest

from edmvf.data.csv_dataset import CsvDataset, CsvFileAccessError, CsvParseError, read_csv_dataset


def reference_read(path):
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    try:
        text = csv_path.read_bytes().decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        records, line_spans, previous_line = [], [], 0
        try:
            for record in reader:
                records.append(record)
                line_spans.append((previous_line + 1, reader.line_num))
                previous_line = reader.line_num
        except csv.Error as exc:
            raise CsvParseError(
                csv_path,
                f"Malformed CSV content in '{csv_path}': {exc}",
                row_number=len(records) + 1,
                start_line=previous_line + 1,
                end_line=reader.line_num,
            ) from exc
        if not records:
            return CsvDataset(headers=[], rows=[])
        return CsvDataset(headers=list(records[0]), rows=records[1:], line_spans=line_spans)
    except OSError as exc:
        raise CsvFileAccessError(f"Unable to read CSV file '{csv_path}': {exc}") from exc
    except UnicodeDecodeError as exc:
        raise CsvParseError(
            csv_path,
            f"CSV file '{csv_path}' is not valid UTF-8 (byte offset {exc.start}: {exc.reason}).",
        ) from exc


def outcome(reader, path):
    try:
        dataset = reader(path)
    except Exception as exc:  # compare every exception exactly
        return (
            "raised",
            type(exc).__name__,
            str(exc),
            getattr(exc, "row_number", None),
            getattr(exc, "start_line", None),
            getattr(exc, "end_line", None),
            type(exc.__cause__).__name__ if exc.__cause__ is not None else None,
        )
    return "ok", dataset.headers, dataset.rows, dataset.line_spans


def assert_same(tmp_path, content, name="case.csv"):
    path = tmp_path / name
    path.write_bytes(content)
    assert outcome(read_csv_dataset, path) == outcome(reference_read, path), content[:80]


FILLER = b"x" * 100

EXPLICIT = {
    "empty": b"",
    "blank_first_record": b"\nid,name\n1,a\n",
    "header_only": b"id,name\n",
    "header_only_no_newline": b"id,name",
    "lf": b"id,name\n1,a\n2,b\n",
    "crlf": b"id,name\r\n1,a\r\n2,b\r\n",
    "cr": b"id,name\r1,a\r2,b\r",
    "mixed_endings": b"id,name\r\n1,a\n2,b\r3,c\n",
    "no_final_newline": b"id,name\n1,a\n2,b",
    "trailing_blank_lines": b"id,name\n1,a\n\n\n",
    "multiline_lf": b'id,name\n1,"a\nb"\n2,c\n',
    "multiline_cr": b'id,name\n1,"a\rb"\n2,c\n',
    "multiline_crlf": b'id,name\r\n1,"a\r\nb\r\nc"\r\n2,c\r\n',
    "multiline_header": b'"i\nd",name\n1,a\n',
    "quoted_commas_and_quotes": b'id,name\n1,"a,""b"", c"\n',
    "bad_quote": b'id,name\n1,"a"b\n',
    "bad_quote_in_header": b'"a"b,name\n',
    "unterminated_quote": b'id,name\n1,"abc\n',
    "unterminated_no_newline": b'id,name\n1,"abc',
    "nul_byte": b"id,name\n1,a\x00b\n",
    "bom": b"\xef\xbb\xbfid,name\n1,a\n",
    "non_ascii": "id,name\n1,Café\n2,日本\n".encode("utf-8"),
    "line_separator_chars": "id,name\n1,a b\n2,c\u0085d\n".encode("utf-8"),
    "invalid_utf8_early": b"id,name\n1,\xff\n",
    "invalid_utf8_truncated_sequence": b"id,name\n1,\xe2\x82",
    "invalid_utf8_overlong": b"id,name\n1,\xc0\xaf\n",
    "invalid_utf8_surrogate": b"id,name\n1,\xed\xa0\x80\n",
    "csv_error_before_invalid_utf8": b'id,name\n1,"a"b\n2,\xff\n',
    "invalid_utf8_before_csv_error": b'id,name\n1,\xff\n2,"a"b\n',
    "field_size_limit": b"id,name\n1," + b'"' + b"y" * 140000 + b"\n" + b'z"\n',
}


@pytest.mark.parametrize("name", EXPLICIT)
def test_reader_matches_the_reference_algorithm(tmp_path, name):
    assert_same(tmp_path, EXPLICIT[name])


@pytest.mark.parametrize("name", ["missing", "directory"])
def test_missing_file_and_directory_match_the_reference(tmp_path, name):
    path = tmp_path / "target"
    if name == "directory":
        path.mkdir()

    assert outcome(read_csv_dataset, path) == outcome(reference_read, path)


# The text stream decodes in chunks; a line break or invalid byte near a chunk edge
# must behave exactly like the all-at-once decode.
CHUNK_EDGES = list(range(8180, 8204)) + [16380, 16385, 32765, 32771, 65532, 65540]


@pytest.mark.parametrize("edge", CHUNK_EDGES)
def test_line_break_at_a_chunk_boundary_matches_the_reference(tmp_path, edge):
    head = b"id,name\n"
    body = b"a" * (edge - len(head))
    for suffix in (b"\nx,y\n", b"\r\nx,y\r\n", b'",\n"q\nr"\n'):
        assert_same(tmp_path, head + body + suffix, name=f"edge{edge}.csv")
    assert_same(tmp_path, head + b'1,"' + b"m" * (edge - len(head) - 3) + b'\nm"\n3,4\n', name=f"quoted{edge}.csv")


@pytest.mark.parametrize("edge", CHUNK_EDGES)
def test_invalid_utf8_offset_is_absolute_across_chunk_boundaries(tmp_path, edge):
    content = b"id,name\n1," + b"a" * (edge - 10) + b"\xff\n"
    path = tmp_path / "late.csv"
    path.write_bytes(content)

    with pytest.raises(CsvParseError) as info:
        read_csv_dataset(path)

    offset = content.index(b"\xff")
    assert f"byte offset {offset}:" in info.value.message
    assert outcome(read_csv_dataset, path) == outcome(reference_read, path)


def test_invalid_utf8_wins_over_an_earlier_csv_error_even_far_into_the_file(tmp_path):
    content = b'id,name\n1,"a"b\n' + b"2,c\n" * 5000 + b"3,\xff\n"
    assert_same(tmp_path, content)
    path = tmp_path / "case.csv"
    with pytest.raises(CsvParseError, match="not valid UTF-8"):
        read_csv_dataset(path)


def test_csv_error_location_is_unchanged_past_the_chunk_boundary(tmp_path):
    rows = b"".join(b"%d,%s\n" % (n, b"v" * 40) for n in range(400))
    content = b"id,name\n" + rows + b'999,"a"b\n'
    assert_same(tmp_path, content)

    multiline = b"id,name\n" + rows + b'500,"' + b"w" * 9000 + b'\nw"x\n'
    assert_same(tmp_path, multiline, name="crossing.csv")


def test_reader_matches_the_reference_on_seeded_random_inputs(tmp_path):
    alphabet = [b"a", b"b", b",", b'"', b"\n", b"\r", b"\r\n", b" ", " ".encode(), "\u0085".encode(), b"\x00", "é".encode()]
    rng = random.Random(20261107)
    path = tmp_path / "fuzz.csv"
    for _ in range(300):
        pieces = [rng.choice(alphabet) for _ in range(rng.randint(0, 60))]
        content = b"".join(pieces)
        if rng.random() < 0.05:
            content += bytes([rng.choice([0xFF, 0xC0, 0x80])])
        path.write_bytes(content)
        assert outcome(read_csv_dataset, path) == outcome(reference_read, path), content


def test_public_interface_of_the_reader_is_unchanged():
    import dataclasses
    import inspect

    assert str(inspect.signature(read_csv_dataset)) == "(path: 'str | Path') -> 'CsvDataset'"
    assert [f.name for f in dataclasses.fields(CsvDataset)] == ["headers", "rows", "line_spans"]
    dataset = CsvDataset(headers=["a"], rows=[["1"]])
    assert dataset.line_spans == [] and isinstance(dataset.line_spans, list)
    parse_error = CsvParseError("p", "m")
    assert (parse_error.row_number, parse_error.start_line, parse_error.end_line) == (None, None, None)
