"""어휘 분석 (language.md §2)."""

import pytest

from cool0.cool0 import CompileError, lex


def kinds(src: str):
    return [(t.kind, t.text) for t in lex(src.encode("ascii"))]


def values(src: str):
    return [t.value for t in lex(src.encode("ascii"))[:-1]]


def error(src: bytes) -> str:
    with pytest.raises(CompileError) as ei:
        lex(src)
    return f"{ei.value.line}:{ei.value.col}: {ei.value.msg}"


# --- 토큰 종류 -------------------------------------------------------------


def test_eof_is_always_last():
    assert lex(b"")[-1].kind == "eof"
    assert len(lex(b"")) == 1


def test_keywords_are_all_twenty():
    words = """fn struct enum const let mut if else for break continue
               return match unsafe as true false slice slice_mut offset""".split()
    assert len(words) == 20
    assert all(k == "kw" for k, _ in kinds(" ".join(words))[:-1])
    # 목록이 구현과 어긋나면 여기서 잡힌다. 예전에는 한쪽만 늘어도 통과했다
    from cool0.cool0 import KEYWORDS

    assert set(words) == set(KEYWORDS)


def test_underscore_is_an_identifier():
    assert kinds("_")[0] == ("ident", "_")


def test_identifier_rules():
    assert kinds("_a9 A_")[:2] == [("ident", "_a9"), ("ident", "A_")]


def test_comment_runs_to_end_of_line():
    assert kinds("a // b c\nd") == [("ident", "a"), ("ident", "d"), ("eof", "")]


def test_comment_at_eof_without_newline():
    assert kinds("a // trailing") == [("ident", "a"), ("eof", "")]


# --- 정수 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "src,want",
    [
        ("0", 0),
        ("123", 123),
        ("0xFF", 255),
        ("0xff", 255),
        ("0b1010", 10),
        ("1_000_000", 1000000),
        ("0xDEAD_BEEF", 0xDEADBEEF),
        ("4294967295", 0xFFFFFFFF),
    ],
)
def test_integer_values(src, want):
    assert values(src) == [want]


def test_integer_out_of_range():
    assert error(b"4294967296") == "1:1: integer literal out of range"


def test_integer_followed_by_letter():
    assert error(b"123abc") == "1:1: invalid digit in integer literal"


def test_hex_without_digits():
    assert error(b"0x") == "1:1: integer literal has no digits"


def test_binary_rejects_other_digits():
    assert error(b"0b12") == "1:1: invalid digit in integer literal"


# --- 문자와 문자열 ---------------------------------------------------------


@pytest.mark.parametrize(
    "src,want",
    [("'a'", 97), (r"'\n'", 10), (r"'\t'", 9), (r"'\r'", 13), (r"'\0'", 0),
     (r"'\\'", 92), (r"'\''", 39)],
)
def test_char_values(src, want):
    assert values(src) == [want]


def test_char_escapes_are_exactly_six():
    for bad in [r"'\a'", r"'\x'", r"'\"'"]:
        assert "escape" in error(bad.encode())


def test_empty_char():
    assert error(b"''") == "1:1: empty character literal"


def test_unterminated_char():
    assert error(b"'ab'") == "1:1: unterminated character literal"


def test_string_value_and_escapes():
    assert values(r'"a\nb\"c"') == [b'a\nb"c']


def test_string_backslash_quote_is_only_in_strings():
    assert values(r'"\""') == [b'"']


def test_unterminated_string_at_newline():
    assert error(b'"abc\n"') == "1:1: unterminated string literal"


def test_unterminated_string_at_eof():
    assert error(b'"abc') == "1:1: unterminated string literal"


# --- ASCII 전용 ------------------------------------------------------------


def test_non_ascii_byte_is_an_error():
    assert error("한".encode("utf-8")) == "1:1: non-ascii byte in source"


def test_non_ascii_inside_comment_is_an_error():
    assert "non-ascii" in error("// 주석\n".encode("utf-8"))


def test_non_ascii_inside_string_is_an_error():
    assert "invalid character in string literal" in error('"한"'.encode("utf-8"))


def test_control_bytes_are_rejected():
    assert error(b"\x00") == "1:1: non-ascii byte in source"
    assert error(b"\x7f") == "1:1: non-ascii byte in source"


def test_allowed_whitespace():
    assert kinds("a\tb\r\nc")[:3] == [("ident", "a"), ("ident", "b"), ("ident", "c")]


# --- 구두점: 최장 일치 ------------------------------------------------------


@pytest.mark.parametrize(
    "src,want",
    [
        ("<<=", ["<<="]),
        (">>=", [">>="]),
        ("<<", ["<<"]),
        ("<=", ["<="]),
        ("<", ["<"]),
        ("->", ["->"]),
        ("=>", ["=>"]),
        ("==", ["=="]),
        ("&&", ["&&"]),
        ("&mut", ["&", "mut"]),
        (".^", [".", "^"]),
        ("+=-=", ["+=", "-="]),
    ],
)
def test_longest_match(src, want):
    assert [t.text for t in lex(src.encode())[:-1]] == want


def test_unexpected_character():
    assert error(b"@") == "1:1: unexpected character"
    assert error(b"~") == "1:1: unexpected character"


# --- 위치 -----------------------------------------------------------------


def test_positions_are_one_based():
    toks = lex(b"a\n  bb")
    assert (toks[0].line, toks[0].col) == (1, 1)
    assert (toks[1].line, toks[1].col) == (2, 3)


def test_tab_counts_as_one_column():
    toks = lex(b"\t\tx")
    assert (toks[0].line, toks[0].col) == (1, 3)


def test_crlf_advances_one_line():
    toks = lex(b"a\r\nb")
    assert (toks[1].line, toks[1].col) == (2, 1)
