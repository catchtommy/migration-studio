"""Decoding text from files the studio didn't write, such as a legacy monolith's source code.

Old codebases often aren't UTF-8 (Windows-1252 or ISO-8859-1 are common in old Java projects). Decoding those as
UTF-8 with replacement turns every accented letter or curly quote into U+FFFD (shown as a question mark in a diamond),
which agents then copy into their documents. So: UTF-8 when it is UTF-8, otherwise Windows-1252, otherwise Latin-1,
which accepts any byte. The studio's own files (documents, settings) are always read and written as UTF-8.
"""


def decode_with(data: bytes) -> tuple[str, str]:
    """(text, the encoding it was in)."""
    for encoding in ("utf-8-sig", "cp1252"):  # utf-8 (without a byte order mark if the file has one), else Windows
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1"), "latin-1"


def decode(data: bytes) -> str:
    return decode_with(data)[0]


def encode_like(text: str, encoding: str) -> bytes:
    """Text to write back in a file's own encoding (UTF-8 when that encoding can't hold it)."""
    try:
        return text.encode("utf-8" if encoding == "utf-8-sig" else encoding)
    except UnicodeEncodeError:
        return text.encode("utf-8")
