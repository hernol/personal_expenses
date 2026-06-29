from __future__ import annotations

from collections import defaultdict

from app.models import AnalysisReport, CategoryItem, CategoryReport, Currency, Statement, Transaction

AI_RULES = [
    ('ChatGPT', ('CHATGPT', 'OPENAI')),
    ('Cursor', ('CURSOR',)),
    ('Gomesin IT', ('GOMESIN IT',)),
    ('Google AI/One', ('GOOGLE O', 'GOOGLE ONE', 'GOOGLE AI')),
]
STREAMING_RULES = [
    ('Netflix', ('NETFLIX',)),
    ('Amazon Prime', ('AMAZON PRIME', 'PRIME VIDEO')),
    ('Disney+', ('DISNEY',)),
    ('Spotify', ('SPOTIFY',)),
    ('YouTube', ('YOUTUBE',)),
]


def analyze_statement(statement: Statement) -> AnalysisReport:
    categorized: dict[str, list[CategoryItem]] = defaultdict(list)

    for tx in statement.transactions:
        category, provider = _classify(tx)
        if not category:
            continue
        categorized[category].append(CategoryItem(
            provider=provider,
            merchant=tx.merchant,
            amount=tx.amount,
            currency=tx.currency,
            date=tx.date,
            confidence=0.9,
        ))

    categories = [_build_category(name, items) for name, items in categorized.items()]
    categories.sort(key=lambda category: (category.name != 'AI', -category.total_usd, -category.total_ars))

    recurring = [item for category in categories for item in category.items if item.currency == Currency.USD]
    recurring.sort(key=lambda item: item.amount, reverse=True)

    recommendations = _recommend(categories, statement)

    return AnalysisReport(
        summary={
            'card_brand': statement.card_brand,
            'closing_date': statement.closing_date.isoformat() if statement.closing_date else None,
            'due_date': statement.due_date.isoformat() if statement.due_date else None,
            'total_to_pay_ars': statement.total_to_pay_ars,
            'usd_balance': statement.usd_balance,
            'transaction_count': len(statement.transactions),
        },
        categories=categories,
        recurring_candidates=recurring,
        recommendations=recommendations,
    )


def _classify(tx: Transaction) -> tuple[str | None, str]:
    merchant = tx.merchant.upper()
    for provider, needles in AI_RULES:
        if any(needle in merchant for needle in needles):
            return 'AI', provider
    for provider, needles in STREAMING_RULES:
        if any(needle in merchant for needle in needles):
            return 'Streaming', provider
    return None, tx.merchant


def _build_category(name: str, items: list[CategoryItem]) -> CategoryReport:
    providers = {item.provider for item in items}
    return CategoryReport(
        name=name,
        provider_count=len(providers),
        total_ars=round(sum(item.amount for item in items if item.currency == Currency.ARS), 2),
        total_usd=round(sum(item.amount for item in items if item.currency == Currency.USD), 2),
        items=items,
    )


def _recommend(categories: list[CategoryReport], statement: Statement) -> list[str]:
    recommendations: list[str] = []
    ai = next((category for category in categories if category.name == 'AI'), None)
    if ai and ai.provider_count >= 3:
        recommendations.append(
            f"Tenés {ai.provider_count} providers de AI por USD {ai.total_usd:.2f}/mes aprox.; revisá solapamiento entre "
            f"{', '.join(sorted({item.provider for item in ai.items}))}."
        )
    streaming = next((category for category in categories if category.name == 'Streaming'), None)
    if streaming and streaming.provider_count >= 2:
        recommendations.append(
            f"Tenés {streaming.provider_count} servicios de streaming; rotalos mes a mes en vez de pagarlos todos juntos."
        )
    if statement.usd_balance and statement.usd_balance > 0:
        recommendations.append(
            f"Hay USD {statement.usd_balance:.2f} en consumos/impuestos asociados: priorizá recortar suscripciones dolarizadas."
        )
    if not recommendations:
        recommendations.append('No aparecen muchas suscripciones claras en este resumen; cargá más meses para detectar recurrencia real.')
    return recommendations
