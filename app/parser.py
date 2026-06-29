from __future__ import annotations

import re
from datetime import date

from app.models import Currency, Statement, Transaction

SPANISH_MONTHS = {
    'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'set': 9, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12,
}

DATE_RE = re.compile(r'^\d{2}-\d{2}-\d{2}$')
MONTH_DATE_RE = re.compile(r'^(\d{2})-([A-Za-zÁÉÍÓÚáéíóú]{3})-(\d{2})$')
AMOUNT_RE = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$|^-?\d+,\d{2}$')
INSTALLMENT_RE = re.compile(r'^\d{2}/\d{2}$')
VOUCHER_RE = re.compile(r'^\d{6}$')


def parse_statement_text(text: str) -> Statement:
    tokens = _tokens(text)
    card_brand = 'VISA' if any('VISA' in token.upper() for token in tokens[:20]) else 'UNKNOWN'
    transactions = _parse_transactions(tokens)
    return Statement(
        card_brand=card_brand,
        closing_date=_find_closing_date(tokens),
        due_date=_find_due_date(tokens),
        total_to_pay_ars=_find_amount_after(tokens, 'TOTAL A PAGAR'),
        usd_balance=_find_usd_balance(transactions, tokens),
        transactions=transactions,
    )


def _tokens(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_transactions(tokens: list[str]) -> list[Transaction]:
    try:
        start = tokens.index('DETALLE DEL CONSUMO')
    except ValueError:
        return []

    end = next((i for i, token in enumerate(tokens[start + 1:], start + 1)
                if token.startswith('TARJETA') and 'Total Consumos' in token), len(tokens))
    block = tokens[start:end]

    transactions: list[Transaction] = []
    usd_pending: list[dict] = []
    i = 0
    while i < len(block):
        token = block[i]
        if not DATE_RE.match(token):
            i += 1
            continue

        if i + 5 < len(block) and block[i + 1] == '*' and INSTALLMENT_RE.match(block[i + 3]) and VOUCHER_RE.match(block[i + 4]) and _is_amount(block[i + 5]):
            transactions.append(Transaction(
                date=_parse_numeric_date(token),
                merchant=_clean_merchant(block[i + 2]),
                installment=block[i + 3],
                voucher=block[i + 4],
                amount=_parse_amount(block[i + 5]),
                currency=Currency.ARS,
                raw=' | '.join(block[i:i + 6]),
            ))
            i += 6
            continue

        # USD rows in this Visa extract are column-wrapped: dates/descriptions first,
        # then amount/voucher/amount triplets. Capture descriptions and pair later.
        merchant_parts: list[str] = []
        j = i + 1
        while j < len(block) and not DATE_RE.match(block[j]) and not _is_amount(block[j]):
            merchant_parts.append(block[j])
            j += 1
        if merchant_parts:
            usd_pending.append({'date': _parse_numeric_date(token), 'merchant': _clean_merchant(' '.join(merchant_parts))})
        i = j

    amounts = _usd_amounts_after_descriptions(block)
    for pending, amount, voucher in zip(usd_pending, amounts[::2], amounts[1::2]):
        # The PDF text repeats each USD amount on both sides of the voucher; keep one.
        transactions.append(Transaction(
            date=pending['date'],
            merchant=pending['merchant'],
            amount=amount[0],
            currency=Currency.USD,
            voucher=voucher[1],
            raw=f"{pending['date']} | {pending['merchant']} | {amount[0]} USD",
        ))

    return transactions


def _usd_amounts_after_descriptions(block: list[str]) -> list[tuple[float, str | None]]:
    first_amount_index = next((i for i, token in enumerate(block) if _is_amount(token) and i > 0 and not VOUCHER_RE.match(block[i - 1])), None)
    if first_amount_index is None:
        return []
    result: list[tuple[float, str | None]] = []
    for i, token in enumerate(block[first_amount_index:], first_amount_index):
        if _is_amount(token):
            prev = block[i - 1] if i > 0 and VOUCHER_RE.match(block[i - 1]) else None
            nxt = block[i + 1] if i + 1 < len(block) and VOUCHER_RE.match(block[i + 1]) else None
            result.append((_parse_amount(token), prev or nxt))
    return result


def _clean_merchant(value: str) -> str:
    value = value.replace(' in1TZKElBUSD', ' in1TZKElB')
    value = re.sub(r'\s+USD$', '', value)
    value = re.sub(r'^[Kk]\s+', '', value)
    return re.sub(r'\s+', ' ', value).strip()


def _find_closing_date(tokens: list[str]) -> date | None:
    for token in tokens[:40]:
        parsed = _parse_month_date(token)
        if parsed:
            return parsed
    return None


def _find_due_date(tokens: list[str]) -> date | None:
    dates = [parsed for token in tokens[:50] if (parsed := _parse_month_date(token))]
    return dates[1] if len(dates) > 1 else None


def _find_amount_after(tokens: list[str], label: str) -> float | None:
    for i, token in enumerate(tokens):
        if token == label:
            for candidate in tokens[i + 1:i + 8]:
                if _is_amount(candidate):
                    return _parse_amount(candidate)
    return None


def _find_usd_balance(transactions: list[Transaction], tokens: list[str]) -> float | None:
    # Prefer parsed USD purchases when we have them.
    usd_total = round(sum(tx.amount for tx in transactions if tx.currency == Currency.USD), 2)
    if usd_total:
        return usd_total

    # Fallback for banks/PDF layouts where USD detail rows are not parsed yet but
    # the summary exposes a current dollar balance. Keep this deliberately
    # conservative: values over 1000 are often credit limits (e.g. 35.000,00) or
    # table artifacts, not monthly spend for this user's cards.
    candidates: list[float] = []
    for i, token in enumerate(tokens[:-1]):
        if token == 'DÓLARES':
            for candidate in tokens[i + 1:i + 4]:
                if _is_amount(candidate):
                    value = _parse_amount(candidate)
                    if 0 < value <= 1000:
                        candidates.append(value)
                    break
    return candidates[-1] if candidates else None


def _is_amount(token: str) -> bool:
    return bool(AMOUNT_RE.match(token))


def _parse_amount(token: str) -> float:
    return float(token.replace('.', '').replace(',', '.'))


def _parse_numeric_date(token: str) -> date:
    day, month, year = [int(part) for part in token.split('-')]
    return date(2000 + year, month, day)


def _parse_month_date(token: str) -> date | None:
    match = MONTH_DATE_RE.match(token)
    if not match:
        return None
    day_s, month_s, year_s = match.groups()
    month = SPANISH_MONTHS.get(month_s.lower())
    if not month:
        return None
    return date(2000 + int(year_s), month, int(day_s))
