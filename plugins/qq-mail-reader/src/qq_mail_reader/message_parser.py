"""Local, defensive parsing for untrusted RFC 5322 messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import policy
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser

MAX_HEADER_CHARS = 2_000
MAX_BODY_CHARS = 50_000
MAX_MIME_DEPTH = 100
MAX_MIME_PARTS = 1_000

_OPAQUE_CONTENT_TYPE = "application/octet-stream"
_MIME_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


@dataclass(frozen=True)
class AttachmentInfo:
    filename: str
    content_type: str
    size: int
    content_id: str | None


@dataclass(frozen=True)
class ParsedMessage:
    sender: str
    subject: str
    date: str
    message_id: str
    body: str
    truncated: bool
    attachments: tuple[AttachmentInfo, ...]


class _PlainTextHTMLParser(HTMLParser):
    """Extract visible text without following links or loading resources."""

    _HIDDEN_TAGS = {"head", "noscript", "script", "style", "template", "title"}
    _BREAK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._hidden_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_tags.append(tag)
        elif not self._hidden_tags and tag in self._BREAK_TAGS:
            self._line_break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_tags.append(tag)
        elif not self._hidden_tags and tag in self._BREAK_TAGS:
            self._line_break()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            if self._hidden_tags and self._hidden_tags[-1] == tag:
                self._hidden_tags.pop()
        elif not self._hidden_tags and tag in self._BREAK_TAGS:
            self._line_break()

    def handle_data(self, data: str) -> None:
        if not self._hidden_tags:
            self._chunks.append(data)

    def _line_break(self) -> None:
        if self._chunks and not self._chunks[-1].endswith("\n"):
            self._chunks.append("\n")

    def text(self) -> str:
        lines = (
            re.sub(r"[\t\f\v ]+", " ", line).strip() for line in "".join(self._chunks).splitlines()
        )
        return "\n".join(line for line in lines if line)


def parse_message(raw: bytes, max_body_chars: int = MAX_BODY_CHARS) -> ParsedMessage:
    """Parse an email locally, retaining only body text and attachment metadata."""
    if max_body_chars < 0:
        raise ValueError("max_body_chars must not be negative")

    message = _parse_message(raw)
    attachments: list[AttachmentInfo] = []
    plain_parts: list[str] = []
    html_parts: list[str] = []

    stack: list[tuple[Message, int]] = [(message, 0)]
    parts_seen = 0
    while stack:
        part, depth = stack.pop()
        parts_seen += 1
        if parts_seen > MAX_MIME_PARTS:
            raise ValueError("MIME part limit exceeded")
        if depth > MAX_MIME_DEPTH:
            raise ValueError("MIME nesting limit exceeded")

        content_type, malformed_content_type = _safe_content_type_details(part)
        if _is_attachment(part, malformed_content_type):
            attachments.append(_attachment_info(part))
            continue

        if _safe_is_multipart(part):
            try:
                payload = part.get_payload()
            except Exception:
                attachments.append(_attachment_info(part))
                continue
            if isinstance(payload, list):
                for child in reversed(payload):
                    if isinstance(child, Message):
                        stack.append((child, depth + 1))
            continue

        if content_type == "text/plain":
            text = _decode_part_text(part)
            if text.strip():
                plain_parts.append(text.strip())
        elif content_type == "text/html":
            text = _html_to_text(_decode_part_text(part))
            if text:
                html_parts.append(text)

    selected_parts = plain_parts if plain_parts else html_parts
    selected_body = "\n\n".join(selected_parts)
    truncated = len(selected_body) > max_body_chars

    return ParsedMessage(
        sender=_safe_part_header(message, "From"),
        subject=_safe_part_header(message, "Subject"),
        date=_safe_part_header(message, "Date"),
        message_id=_safe_part_header(message, "Message-ID"),
        body=selected_body[:max_body_chars],
        truncated=truncated,
        attachments=tuple(attachments),
    )


def _parse_message(raw: bytes) -> Message:
    try:
        return BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        try:
            return BytesParser(policy=policy.compat32).parsebytes(raw)
        except Exception:
            raise ValueError("unable to parse message") from None


def _decoded_header(value: object | None) -> str:
    if value is None:
        return ""

    try:
        decoded = "".join(
            _decode_header_piece(piece, charset) for piece, charset in decode_header(str(value))
        )
    except (LookupError, TypeError, UnicodeError, ValueError):
        decoded = str(value)
    return decoded[:MAX_HEADER_CHARS]


def _decode_header_piece(piece: bytes | str, charset: str | None) -> str:
    if isinstance(piece, str):
        return piece
    try:
        return piece.decode(charset or "ascii", errors="replace")
    except LookupError:
        return piece.decode("utf-8", errors="replace")


def _safe_part_header(part: Message, name: str) -> str:
    try:
        return _decoded_header(part.get(name))
    except Exception:
        return ""


def _safe_content_type(part: Message) -> str:
    return _safe_content_type_details(part)[0]


def _safe_content_type_details(part: Message) -> tuple[str, bool]:
    try:
        header = part.get("Content-Type")
    except Exception:
        return _OPAQUE_CONTENT_TYPE, True
    if header is None:
        return "text/plain", False

    try:
        declared_type = str(header).split(";", maxsplit=1)[0].strip().lower()
    except Exception:
        return _OPAQUE_CONTENT_TYPE, True
    if len(declared_type) > MAX_HEADER_CHARS or not _MIME_TYPE_PATTERN.fullmatch(declared_type):
        return _OPAQUE_CONTENT_TYPE, True

    try:
        parsed_type = part.get_content_type().lower()
    except Exception:
        return _OPAQUE_CONTENT_TYPE, True
    if len(parsed_type) > MAX_HEADER_CHARS or not _MIME_TYPE_PATTERN.fullmatch(parsed_type):
        return _OPAQUE_CONTENT_TYPE, True
    return parsed_type, False


def _safe_content_disposition(part: Message) -> str | None:
    try:
        header = part.get("Content-Disposition")
    except Exception:
        return "attachment"
    if header is None:
        return None

    try:
        disposition = str(header).split(";", maxsplit=1)[0].strip().lower()
    except Exception:
        return "attachment"
    if disposition not in {"attachment", "inline"}:
        return "attachment"

    try:
        return disposition if part.get_content_disposition() == disposition else "attachment"
    except Exception:
        return "attachment"


def _safe_filename(part: Message) -> str | None:
    try:
        filename = part.get_filename()
    except Exception:
        return "unnamed"
    if filename is None:
        return None
    return _decoded_header(filename) or "unnamed"


def _is_attachment(part: Message, malformed_content_type: bool) -> bool:
    return (
        malformed_content_type
        or _safe_content_disposition(part) == "attachment"
        or _safe_filename(part) is not None
    )


def _safe_is_multipart(part: Message) -> bool:
    try:
        return part.is_multipart()
    except Exception:
        return False


def _attachment_info(part: Message) -> AttachmentInfo:
    filename = _safe_filename(part) or "unnamed"
    content_id = _safe_part_header(part, "Content-ID") or None
    return AttachmentInfo(
        filename=filename,
        content_type=_safe_content_type(part),
        size=len(_decoded_payload(part)),
        content_id=content_id,
    )


def _decode_part_text(part: Message) -> str:
    payload = _decoded_payload(part)
    try:
        charset = part.get_content_charset() or "utf-8"
    except Exception:
        charset = "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decoded_payload(part: Message) -> bytes:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8", errors="replace")

    try:
        raw_payload = part.get_payload()
    except Exception:
        return b""
    if isinstance(raw_payload, str):
        return raw_payload.encode("utf-8", errors="replace")
    if isinstance(raw_payload, list):
        serialized_children: list[bytes] = []
        for child in raw_payload:
            if isinstance(child, Message):
                serialized = _serialize_message(child)
                if serialized is not None:
                    serialized_children.append(serialized)
        return b"".join(serialized_children)
    return b""


def _serialize_message(message: Message) -> bytes | None:
    try:
        return message.as_bytes(policy=policy.SMTP)
    except Exception:
        return None


def _html_to_text(html: str) -> str:
    parser = _PlainTextHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        return ""
    return parser.text()
