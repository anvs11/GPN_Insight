"""Детерминированные правила пилота; это не полное обезличивание свободного текста."""

import re
from typing import NamedTuple

PHONE_MARKER = "[ТЕЛЕФОН]"
EMAIL_MARKER = "[EMAIL]"
CARD_MARKER = "[КАРТА]"
INN_MARKER = "[ИНН]"
ACCOUNT_MARKER = "[СЧЁТ]"
MASKING_VERSION = "masking-v2"

_SPACE = r"[ \t\u00a0\u202f]"
_SEP = r"[ \t\u00a0\u202f\-\u2010-\u2014]"
_GAP = r"[ \t\u00a0\u202f.\-\u2010-\u2014]{0,3}"
_EMAIL = re.compile(r"(?<![\w.!#$%&'*+/=?^`{|}~@-])[\w.!#$%&'*+/=?^`{|}~+-]+@(?:[\w-]+\.)*[\w-]+(?![\w@-])")
_PHONE = re.compile(
    rf"(?<![\w+])(?:(?:\+7|[78]){_GAP}\(?[0-9]{{3}}\)?|\([3489][0-9]{{2}}\)|[3489][0-9]{{2}})"
    rf"{_GAP}[0-9]{{3}}{_GAP}[0-9]{{2}}{_GAP}[0-9]{{2}}(?!\w)"
)
_INN_BEFORE = re.compile(
    rf"(?<!\w)ИНН[ \t\r\n\u00a0\u202f]{{0,16}}[:№#=(\-—–]?[ \t\r\n\u00a0\u202f]{{0,16}}"
    r"(?P<number>[0-9]{12}|[0-9]{10})(?!\w)", re.IGNORECASE,
)
_INN_AFTER = re.compile(
    rf"(?<!\w)(?P<number>[0-9]{{12}}|[0-9]{{10}}){_SPACE}{{0,16}}[(:—–,;-]?{_SPACE}{{0,16}}ИНН(?!\w)",
    re.IGNORECASE,
)
_NUMBERS = re.compile(rf"(?<![\w+])[0-9]+(?:{_SEP}{{1,3}}[0-9]+)*(?!\w)")
_DIGITS = re.compile(r"[0-9]+")
_ACCOUNT_GROUPS = {(4, 4, 4, 4, 4), (5, 5, 5, 5), (5, 3, 1, 4, 7)}
_SPECIAL_CARD_GROUPS = {(4, 6, 5), (4, 6, 4), (4, 3, 3, 3)}


class _Span(NamedTuple):
    start: int
    end: int
    marker: str | None
    priority: int


def _luhn(number: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(number)):
        digit = int(char)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _financial(text: str) -> list[_Span]:
    spans: list[_Span] = []
    for match in _NUMBERS.finditer(text):
        groups = list(_DIGITS.finditer(match.group()))
        options: list[list[tuple[int, _Span, int]]] = [[] for _ in groups]
        for first in range(len(groups)):
            # Максимум пять групп/20 цифр: соседние срок действия или номер
            # не должны скрывать карту. Не вырезаем подстроки внутри группы.
            chunks: list[str] = []
            size = 0
            for last in range(first, min(first + 5, len(groups))):
                size += len(groups[last].group())
                if size > 20:
                    break
                chunks.append(groups[last].group())
                lengths = tuple(map(len, chunks))
                marker = None
                priority = 3
                if size == 20 and (len(chunks) == 1 or lengths in _ACCOUNT_GROUPS):
                    marker, priority = ACCOUNT_MARKER, 2
                elif 13 <= size <= 19 and (len(chunks) == 1 or lengths in _SPECIAL_CARD_GROUPS or
                        (all(length == 4 for length in lengths[:-1]) and 1 <= lengths[-1] <= 4)):
                    marker = CARD_MARKER if _luhn("".join(chunks)) else None
                else:
                    continue
                span = _Span(match.start() + groups[first].start(), match.start() + groups[last].end(), marker, priority)
                if marker is None:
                    # Не превращаем части неверной карты в отдельные телефоны.
                    spans.append(span)
                else:
                    options[first].append((last + 1, span, size))

        # Максимальное покрытие известных форматов: иначе ложный 20-значный
        # «счёт» из частей двух карт оставит вторую карту открытой. При равном
        # покрытии предпочитаем счёт и меньшее число замен.
        scores = [(0, 0, 0)] * (len(groups) + 1)
        chosen: list[tuple[int, _Span] | None] = [None] * len(groups)
        for first in range(len(groups) - 1, -1, -1):
            scores[first] = scores[first + 1]
            for end, span, size in options[first]:
                tail = scores[end]
                score = (tail[0] + size, tail[1] + (size if span.marker == ACCOUNT_MARKER else 0), tail[2] - 1)
                if score >= scores[first]:
                    scores[first] = score
                    chosen[first] = (end, span)
        first = 0
        while first < len(groups):
            option = chosen[first]
            if option is None:
                first += 1
            else:
                first, span = option
                spans.append(span)
    return spans


def _select(text: str, spans: list[_Span]) -> list[_Span]:
    occupied = bytearray(len(text))
    selected = []
    for span in sorted(spans, key=lambda s: (s.priority, s.marker is None, -(s.end - s.start), s.start)):
        if not any(occupied[span.start:span.end]):
            occupied[span.start:span.end] = b"\x01" * (span.end - span.start)
            selected.append(span)
    return sorted(selected)


def _blocked(text: str, spans: list[_Span]) -> str:
    result = []
    cursor = 0
    for span in spans:
        result.extend((text[cursor:span.start], "\x00" * (span.end - span.start)))
        cursor = span.end
    result.append(text[cursor:])
    return "".join(result)


def mask_pii(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Для маскирования нужна строка.")
    text.encode("utf-8")
    spans = [_Span(m.start(), m.end(), EMAIL_MARKER, 0) for m in _EMAIL.finditer(text)]
    for pattern in (_INN_BEFORE, _INN_AFTER):
        spans.extend(_Span(m.start("number"), m.end("number"), INN_MARKER, 1) for m in pattern.finditer(text))
    base = _select(text, spans)
    working = _blocked(text, base)
    spans += _financial(working)
    spans += [_Span(m.start(), m.end(), PHONE_MARKER, 4) for m in _PHONE.finditer(working)]
    selected = _select(text, spans)
    # Разделение известных телефонов/ИНН открывает финансовые номера рядом с ними.
    selected = _select(text, selected + _financial(_blocked(text, selected)))
    result = []
    cursor = 0
    for span in selected:
        result.extend((text[cursor:span.start], span.marker or text[span.start:span.end]))
        cursor = span.end
    result.append(text[cursor:])
    return "".join(result)


def masking_recipe() -> dict[str, object]:
    """Снимок правил для normalization_hash; не изменяемая настройка приложения."""
    return {"version": MASKING_VERSION, "email_pattern": _EMAIL.pattern, "phone_pattern": _PHONE.pattern,
        "inn_patterns": [_INN_BEFORE.pattern, _INN_AFTER.pattern], "inn_ignore_case": True,
        "financial_pattern": _NUMBERS.pattern, "digits": "ASCII", "card_length": [13, 19],
        "card_checksum": "Luhn-mod10-right-to-left-v1", "special_card_groups": [list(g) for g in sorted(_SPECIAL_CARD_GROUPS)],
        "default_card_groups": "four-digit groups with final group of 1..4 digits; or contiguous",
        "inn_lengths": [10, 12], "inn_checksum": "not-validated", "phone_validation": "format-only",
        "account_length": 20, "account_groups": [list(g) for g in sorted(_ACCOUNT_GROUPS)],
        "account_checksum": "not-validated",
        "priority": ["email", "labelled-inn", "account", "card", "phone"],
        "financial_scan": "whole-digit-groups; max-five-groups/20-digits; maximal-digit-coverage; ties-account-digits-fewer-spans-earlier-longer-v1",
        "overlap_policy": "non-overlapping-priority-valid-before-protected-then-length; second-financial-pass-v1",
        "invalid_card_policy": "preserve-without-phone-submatches",
        "markers": {"phone": PHONE_MARKER, "email": EMAIL_MARKER, "card": CARD_MARKER,
                    "inn": INN_MARKER, "account": ACCOUNT_MARKER},
        "license_plates": "preserve", "names_and_addresses": "not-detected"}
