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
    card_last4 = _find_card_last4(tokens)
    transactions = _parse_transactions(tokens)
    return Statement(
        card_brand=card_brand,
        card_last4=card_last4,
        closing_date=_find_closing_date(tokens),
        due_date=_find_due_date(tokens),
        total_to_pay_ars=_find_amount_after(tokens, 'TOTAL A PAGAR'),
        usd_balance=_find_usd_balance(transactions, tokens),
        transactions=transactions,
    )


def _find_card_last4(tokens: list[str]) -> str | None:
    """Try to extract the last 4 digits of the card number.

    In these bank statements it often appears near lines that include "TARJETA"
    (e.g. "TARJETA 3095 Total Consumos ..."). Sometimes the extractor may show
    it as masked digits or embedded with other text.
    """
    # Small exclusions: avoid grabbing years and a few known contact/toll numbers.
    excluded = {
        '0000',
        '0800',
        '1000',
        '7528',
        '4379',
        '6200',
    }

    def _is_plausible_last4(x: str) -> bool:
        if x in excluded:
            return False
        # Drop obvious years like 2026/2025/2024
        if x.startswith('20'):
            return False
        return True

    # 1) Prefer regions near "TARJETA".
    tarjeta_idxs = [i for i, tok in enumerate(tokens) if 'TARJETA' in tok.upper()]
    for idx in tarjeta_idxs:
        tok0 = tokens[idx]
        t0u = tok0.upper()

        # Many PDFs also have a "Tarjeta Crédito VISA" header early on.
        # We want the line that includes the masked/unmasked last4, usually in
        # the same line as "TARJETA" + "Total Consumos".
        looks_like_statement_card_line = (
            ('TOTAL' in t0u) or ('CONSUMO' in t0u) or ('CONSUMOS' in t0u)
        )
        # If this TARJETA occurrence doesn't look like the card-summary line,
        # skip it to avoid grabbing unrelated 4-digit chunks (e.g. CUIT parts).
        if not looks_like_statement_card_line and not re.search(r'(?:\*+|X+)\s*\d{4}', tok0, flags=re.IGNORECASE) and not re.search(r'\d{4}', tok0):
            continue

        # Prefer digits already inside the TARJETA line.
        masked0 = re.findall(r'(?:\*+|X+)\s*(\d{4})', tok0, flags=re.IGNORECASE)
        for cand in masked0:
            if _is_plausible_last4(cand):
                return cand
        cands0 = [c for c in re.findall(r'(\d{4})', tok0) if _is_plausible_last4(c)]
        if cands0:
            return cands0[0]

        # Otherwise, look shortly after TARJETA (but still close to the card summary).
        for j in range(idx + 1, min(len(tokens), idx + 8)):
            tok = tokens[j]
            masked = re.findall(r'(?:\*+|X+)\s*(\d{4})', tok, flags=re.IGNORECASE)
            for cand in masked:
                if _is_plausible_last4(cand):
                    return cand

            cands = [c for c in re.findall(r'(\d{4})', tok) if _is_plausible_last4(c)]
            if cands:
                return cands[0]

    # Some PDFs may show last4 as two groups of two digits separated
    # by spaces/hyphens (e.g. "30 04"). Try this only in the vicinity of
    # TARJETA occurrences to avoid grabbing unrelated numbers elsewhere.
    for idx in tarjeta_idxs:
        window_text = ' '.join(tokens[idx: min(len(tokens), idx + 25)])
        pair_cands = re.findall(r'(\d{2})\D+(\d{2})', window_text)
        for a, b in reversed(pair_cands):
            cand = a + b
            if _is_plausible_last4(cand):
                return cand

    # 2) Fallback: scan whole document for masked patterns.
    masked_all = re.findall(r'(?:\*+|X+)\s*(\d{4})', '\n'.join(tokens), flags=re.IGNORECASE)
    for cand in reversed(masked_all):
        if _is_plausible_last4(cand):
            return cand

    # 3) Last fallback: sometimes it appears near 'CUENTA'.
    for token in tokens:
        if 'CUENTA' not in token.upper():
            continue
        cands = [c for c in re.findall(r'(\d{4})', token) if _is_plausible_last4(c)]
        if cands:
            return cands[-1]

    return None


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
    # Prefer parsed USD rows when we have them.
    usd_total = round(sum(tx.amount for tx in transactions if tx.currency == Currency.USD), 2)
    if usd_total:
        return usd_total

    # Fallback: most PDFs expose the current USD balance right next to
    # "TOTAL A PAGAR" as a PESOS + DÓLARES pair.
    #
    # Robustness: PDF text extraction varies (accents, whitespace, casing).
    # So we normalize labels before matching.
    import unicodedata

    def _norm_label(s: str) -> str:
        s = s.upper().strip()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')  # drop diacritics
        s = re.sub(r'\s+', ' ', s)
        return s

    def _extract_amount_maybe(token: str) -> float | None:
        # Some PDF extractors embed currency symbols in the same line,
        # e.g. "$ 237,07" or "237,07 USD".
        m = AMOUNT_RE.search(token)
        return _parse_amount(m.group(0)) if m else None

    total_indices: list[int] = []
    for i, t in enumerate(tokens):
        nt = _norm_label(t)
        if nt == 'TOTAL A PAGAR' or ('TOTAL' in nt and 'PAGAR' in nt):
            total_indices.append(i)

    if total_indices:
        i = total_indices[-1]
        window = tokens[i + 1:i + 80]
        amounts: list[float] = []
        for candidate in window:
            v = _extract_amount_maybe(candidate)
            if v is not None:
                amounts.append(v)
        if amounts:
            return round(amounts[-1], 2)

    # Last resort: scan for a standalone "DÓLARES" label.
    for i, token in enumerate(tokens[:-1]):
        if _norm_label(token) == 'DOLARES':
            for candidate in tokens[i + 1:i + 10]:
                v = _extract_amount_maybe(candidate)
                if v is not None:
                    return round(v, 2)
    return None


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
