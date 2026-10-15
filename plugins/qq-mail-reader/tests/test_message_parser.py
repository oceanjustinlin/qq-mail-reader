from __future__ import annotations

from dataclasses import FrozenInstanceError
from email import policy
from email.message import EmailMessage

import pytest

from qq_mail_reader.message_parser import (
    MAX_HEADER_CHARS,
    AttachmentInfo,
    ParsedMessage,
    parse_message,
)


def test_parses_rfc2047_chinese_headers_and_plain_text_body() -> None:
    raw = b"".join(
        (
            b"From: =?utf-8?b?5byg5LiJ?= <zhangsan@example.com>\r\n"
            b"Subject: =?utf-8?b?5b+r6YCS5Y2V5Y+3?=\r\n"
            b"Date: Tue, 22 Sep 2026 10:00:00 +0800\r\n"
            b"Message-ID: <message-1@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n",
            "您的快递单号是 SF123456789CN。".encode(),
        )
    )

    parsed = parse_message(raw)

    assert parsed.sender == "张三 <zhangsan@example.com>"
    assert parsed.subject == "快递单号"
    assert parsed.date == "Tue, 22 Sep 2026 10:00:00 +0800"
    assert parsed.message_id == "<message-1@example.com>"
    assert parsed.body == "您的快递单号是 SF123456789CN。"
    assert parsed.attachments == ()
    assert not parsed.truncated


def test_prefers_plain_text_in_multipart_alternative() -> None:
    raw = (
        b"Content-Type: multipart/alternative; boundary=alt\r\n\r\n"
        b"--alt\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        b"plain delivery update\r\n"
        b"--alt\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>html delivery update</p>\r\n"
        b"--alt--\r\n"
    )

    parsed = parse_message(raw)

    assert parsed.body == "plain delivery update"


def test_uses_visible_html_without_script_or_style_when_plain_text_is_absent() -> None:
    raw = (
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><head><style>.hidden { display: none; }</style></head>"
        b"<body><p>First   visible line</p><script>doNotExpose()</script>"
        b"<p>Second visible line</p></body></html>"
    )

    parsed = parse_message(raw)

    assert "First visible line" in parsed.body
    assert "Second visible line" in parsed.body
    assert "doNotExpose" not in parsed.body
    assert "hidden" not in parsed.body


def test_returns_attachment_metadata_without_exposing_attachment_content() -> None:
    attachment_secret = "ATTACHMENT_SECRET_DO_NOT_LEAK"
    raw = (
        b"Content-Type: multipart/mixed; boundary=mix\r\n\r\n"
        b"--mix\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        b"delivery update\r\n"
        b"--mix\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename*=utf-8''report%20%E4%B8%AD%E6%96%87.pdf\r\n"
        b"Content-ID: <report-1>\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n"
        b"QVRUQUNITUVOVF9TRUNSRVRfRE9fTk9UX0xFQUs=\r\n"
        b"--mix--\r\n"
    )

    parsed = parse_message(raw)

    assert parsed.body == "delivery update"
    assert parsed.attachments == (
        AttachmentInfo(
            filename="report 中文.pdf",
            content_type="application/pdf",
            size=len(attachment_secret.encode()),
            content_id="<report-1>",
        ),
    )
    assert attachment_secret not in parsed.body
    assert attachment_secret not in repr(parsed)


def test_excludes_an_attached_email_and_its_nested_body() -> None:
    attached_email = EmailMessage()
    attached_email.set_content("NESTED_ATTACHMENT_BODY_TOKEN")

    top_level = EmailMessage()
    top_level.set_content("top-level visible body")
    top_level.add_attachment(attached_email, filename="secret.eml")

    parsed = parse_message(top_level.as_bytes())
    attachment = next(
        attachment for attachment in parsed.attachments if attachment.filename == "secret.eml"
    )

    assert parsed.body == "top-level visible body"
    assert attachment.content_type == "message/rfc822"
    assert attachment.size == len(attached_email.as_bytes(policy=policy.SMTP))
    assert "NESTED_ATTACHMENT_BODY_TOKEN" not in parsed.body
    assert "NESTED_ATTACHMENT_BODY_TOKEN" not in repr(parsed)


def test_truncates_selected_body_at_requested_limit() -> None:
    parsed = parse_message(b"Content-Type: text/plain\r\n\r\nabcdefgh", max_body_chars=3)

    assert parsed.body == "abc"
    assert parsed.truncated


def test_zero_limit_and_negative_limit_boundaries() -> None:
    assert parse_message(b"\r\n", max_body_chars=0).body == ""
    assert not parse_message(b"\r\n", max_body_chars=0).truncated
    assert parse_message(b"\r\nx", max_body_chars=0).truncated

    with pytest.raises(ValueError, match="max_body_chars"):
        parse_message(b"\r\n", max_body_chars=-1)


def test_caps_decoded_headers() -> None:
    parsed = parse_message(b"Subject: " + (b"a" * (MAX_HEADER_CHARS + 10)) + b"\r\n\r\n")

    assert len(parsed.subject) == MAX_HEADER_CHARS


def test_missing_headers_are_empty_strings() -> None:
    parsed = parse_message(b"Content-Type: text/plain\r\n\r\nbody")

    assert parsed.sender == ""
    assert parsed.subject == ""
    assert parsed.date == ""
    assert parsed.message_id == ""


def test_malformed_or_unknown_charset_payload_does_not_crash() -> None:
    raw = (
        b"Subject: =?x-unknown?Q?not-a-known-charset?=\r\n"
        b"Content-Type: text/plain; charset=x-unknown\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n"
        b"not-valid-base64!\r\n"
    )

    parsed = parse_message(raw)

    assert isinstance(parsed, ParsedMessage)
    assert parsed.subject
    assert isinstance(parsed.body, str)


def test_malformed_content_type_is_treated_as_an_opaque_attachment() -> None:
    raw = b"Content-Type: ;l*\r\n\r\nMALFORMED_CONTENT_TYPE_SECRET"

    parsed = parse_message(raw)

    assert parsed.body == ""
    assert parsed.attachments == (
        AttachmentInfo(
            filename="unnamed",
            content_type="application/octet-stream",
            size=len(b"MALFORMED_CONTENT_TYPE_SECRET"),
            content_id=None,
        ),
    )
    assert "MALFORMED_CONTENT_TYPE_SECRET" not in repr(parsed)


def test_malformed_disposition_is_treated_as_an_opaque_attachment() -> None:
    raw = b"Content-Disposition: ;l*\r\n\r\nMALFORMED_DISPOSITION_SECRET"

    parsed = parse_message(raw)

    assert parsed.body == ""
    assert parsed.attachments == (
        AttachmentInfo(
            filename="unnamed",
            content_type="text/plain",
            size=len(b"MALFORMED_DISPOSITION_SECRET"),
            content_id=None,
        ),
    )
    assert "MALFORMED_DISPOSITION_SECRET" not in repr(parsed)


def test_rejects_mime_nesting_beyond_the_documented_limit() -> None:
    body = b"Content-Type: text/plain\r\n\r\nleaf"
    for index in range(101):
        boundary = f"nested-{index}".encode()
        body = b"".join(
            (
                b"Content-Type: multipart/mixed; boundary=",
                boundary,
                b"\r\n\r\n--",
                boundary,
                b"\r\n",
                body,
                b"\r\n--",
                boundary,
                b"--\r\n",
            )
        )

    with pytest.raises(ValueError, match="MIME nesting limit exceeded"):
        parse_message(body)


def test_rejects_mime_part_count_beyond_the_documented_limit() -> None:
    leaf = b"--many\r\nContent-Type: text/plain\r\n\r\nx\r\n"
    raw = b"Content-Type: multipart/mixed; boundary=many\r\n\r\n" + (leaf * 1001) + b"--many--\r\n"

    with pytest.raises(ValueError, match="MIME part limit exceeded"):
        parse_message(raw)


def test_self_closing_and_hidden_html_containers_are_omitted_with_table_separators() -> None:
    raw = (
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><head>HEAD_SECRET<title>TITLE_SECRET</title></head><body>"
        b"<script/>SCRIPT_SECRET</script><STYLE/>STYLE_SECRET</STYLE>"
        b"<template>TEMPLATE_SECRET</template><noscript>NOSCRIPT_SECRET</noscript>"
        b"<table><tr><th>Column A</th><th>Column B</th></tr>"
        b"<tr><td>Cell 1</td><td>Cell 2</td></tr></table></body></html>"
    )

    parsed = parse_message(raw)

    assert parsed.body == "Column A\nColumn B\nCell 1\nCell 2"
    for secret in (
        "HEAD_SECRET",
        "TITLE_SECRET",
        "SCRIPT_SECRET",
        "STYLE_SECRET",
        "TEMPLATE_SECRET",
        "NOSCRIPT_SECRET",
    ):
        assert secret not in parsed.body


@pytest.mark.parametrize(
    ("opening_tag", "mismatched_closer", "matching_closer"),
    [
        ("script", "head", "script"),
        ("STYLE", "template", "STYLE"),
        ("ScRiPt", "NoScRiPt", "sCrIpT"),
    ],
)
def test_mismatched_hidden_closers_do_not_reveal_self_closing_hidden_content(
    opening_tag: str, mismatched_closer: str, matching_closer: str
) -> None:
    html = f"<{opening_tag}/></{mismatched_closer}>SCRIPT_SECRET</{matching_closer}><p>BODY</p>"
    raw = b"Content-Type: text/html; charset=utf-8\r\n\r\n" + html.encode()

    parsed = parse_message(raw)

    assert parsed.body == "BODY"
    assert "SCRIPT_SECRET" not in parsed.body


def test_attachment_content_type_is_bounded_for_a_huge_subtype() -> None:
    raw = (
        b"Content-Type: application/"
        + (b"a" * (MAX_HEADER_CHARS + 100))
        + b"\r\nContent-Disposition: attachment; filename=oversized.bin\r\n\r\nx"
    )

    parsed = parse_message(raw)

    assert parsed.attachments[0].content_type == "application/octet-stream"
    assert len(parsed.attachments[0].content_type) <= MAX_HEADER_CHARS


def test_parsed_message_is_frozen() -> None:
    parsed = parse_message(b"\r\nbody")

    with pytest.raises(FrozenInstanceError):
        parsed.body = "changed"  # type: ignore[misc]
