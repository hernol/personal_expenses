from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.analysis import _classify
from app.models import Statement


def db_path() -> Path:
    return Path(os.environ.get('CARD_EXPENSE_DB', '/tmp/card-expense-analyzer/expenses.db'))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            card_brand TEXT NOT NULL,
            closing_date TEXT,
            due_date TEXT,
            total_to_pay_ars REAL,
            usd_balance REAL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_id INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            merchant TEXT NOT NULL,
            provider TEXT,
            category TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            installment TEXT,
            voucher TEXT
        );

        CREATE TABLE IF NOT EXISTS provider_usage (
            provider TEXT PRIMARY KEY,
            days_used_per_month INTEGER NOT NULL,
            importance TEXT NOT NULL,
            replacement TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL
        );
        '''
    )
    conn.commit()


def save_statement(statement: Statement, filename: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute(
            '''
            INSERT INTO statements (filename, card_brand, closing_date, due_date, total_to_pay_ars, usd_balance, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                filename,
                statement.card_brand,
                statement.closing_date.isoformat() if statement.closing_date else None,
                statement.due_date.isoformat() if statement.due_date else None,
                statement.total_to_pay_ars,
                statement.usd_balance,
                now,
            ),
        )
        statement_id = int(cursor.lastrowid)
        for tx in statement.transactions:
            category, provider = _classify(tx)
            conn.execute(
                '''
                INSERT INTO transactions
                    (statement_id, date, merchant, provider, category, amount, currency, installment, voucher)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    statement_id,
                    tx.date.isoformat(),
                    tx.merchant,
                    provider if category else None,
                    category,
                    tx.amount,
                    tx.currency.value,
                    tx.installment,
                    tx.voucher,
                ),
            )
        conn.commit()
        return statement_id


def save_usage(provider: str, days_used_per_month: int, importance: str, replacement: str | None, notes: str | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            '''
            INSERT INTO provider_usage (provider, days_used_per_month, importance, replacement, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                days_used_per_month=excluded.days_used_per_month,
                importance=excluded.importance,
                replacement=excluded.replacement,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            ''',
            (provider, days_used_per_month, importance, replacement, notes, now),
        )
        conn.commit()
    return {
        'provider': provider,
        'days_used_per_month': days_used_per_month,
        'importance': importance,
        'replacement': replacement,
        'notes': notes,
        'updated_at': now,
    }


def analytics_summary() -> dict[str, Any]:
    with connect() as conn:
        statements = conn.execute('SELECT * FROM statements ORDER BY closing_date, id').fetchall()
        category_rows = conn.execute(
            '''
            SELECT category, provider,
                   SUM(CASE WHEN currency = 'ARS' THEN amount ELSE 0 END) AS total_ars,
                   SUM(CASE WHEN currency = 'USD' THEN amount ELSE 0 END) AS total_usd,
                   COUNT(*) AS transaction_count
            FROM transactions
            WHERE category IS NOT NULL
            GROUP BY category, provider
            ORDER BY total_usd DESC, total_ars DESC
            '''
        ).fetchall()
        usage_rows = conn.execute('SELECT * FROM provider_usage').fetchall()
        usd_balance_rows = conn.execute(
            '''
            SELECT statement_id, SUM(amount) AS usd_balance
            FROM transactions
            WHERE currency = 'USD'
            GROUP BY statement_id
            '''
        ).fetchall()

    usd_balance_by_statement = {row['statement_id']: round(row['usd_balance'] or 0, 2) for row in usd_balance_rows}
    usage_by_provider = {row['provider']: dict(row) for row in usage_rows}
    category_totals = _category_totals(category_rows)
    top_subscriptions = _top_subscriptions(category_rows)
    ai_optimizer = _ai_optimizer(top_subscriptions, usage_by_provider)
    questions = _proactive_questions(top_subscriptions, usage_by_provider)
    recommendations = _recommendations(ai_optimizer, top_subscriptions)

    return {
        'statement_count': len(statements),
        'totals': {
            'total_to_pay_ars': round(sum(row['total_to_pay_ars'] or 0 for row in statements), 2),
            'usd_balance': round(sum(usd_balance_by_statement.get(row['id'], 0) for row in statements), 2),
        },
        'monthly_totals': _monthly_totals(statements, usd_balance_by_statement),
        'category_totals': category_totals,
        'top_subscriptions': top_subscriptions,
        'ai_optimizer': ai_optimizer,
        'proactive_questions': questions,
        'recommendations': recommendations,
    }


def calculate_what_if(cancel_providers: list[str], usd_to_ars_rate: float) -> dict[str, Any]:
    providers = [provider.strip() for provider in cancel_providers if provider.strip()]
    top_subscriptions = analytics_summary()['top_subscriptions']
    selected = [item for item in top_subscriptions if item['provider'] in providers]
    selected.sort(key=lambda item: providers.index(item['provider']))

    monthly_usd = round(sum(item['monthly_cost_usd'] for item in selected), 2)
    monthly_ars_direct = round(sum(item['monthly_cost_ars'] for item in selected), 2)
    monthly_ars_equivalent = round(monthly_ars_direct + monthly_usd * usd_to_ars_rate, 2)
    annual_usd = round(monthly_usd * 12, 2)
    annual_ars_equivalent = round(monthly_ars_equivalent * 12, 2)

    return {
        'cancel_providers': providers,
        'usd_to_ars_rate': usd_to_ars_rate,
        'monthly_savings_usd': monthly_usd,
        'monthly_savings_ars': monthly_ars_direct,
        'monthly_savings_ars_equivalent': monthly_ars_equivalent,
        'annual_savings_usd': annual_usd,
        'annual_savings_ars_equivalent': annual_ars_equivalent,
        'items': selected,
    }


def _category_totals(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = grouped.setdefault(row['category'], {
            'category': row['category'],
            'provider_count': 0,
            'total_ars': 0.0,
            'total_usd': 0.0,
            'transaction_count': 0,
        })
        bucket['provider_count'] += 1
        bucket['total_ars'] += row['total_ars'] or 0
        bucket['total_usd'] += row['total_usd'] or 0
        bucket['transaction_count'] += row['transaction_count'] or 0
    result = list(grouped.values())
    for item in result:
        item['total_ars'] = round(item['total_ars'], 2)
        item['total_usd'] = round(item['total_usd'], 2)
    return sorted(result, key=lambda item: (item['category'] != 'AI', -item['total_usd'], -item['total_ars']))


def _top_subscriptions(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        result.append({
            'provider': row['provider'],
            'category': row['category'],
            'monthly_cost_ars': round(row['total_ars'] or 0, 2),
            'monthly_cost_usd': round(row['total_usd'] or 0, 2),
            'transaction_count': row['transaction_count'],
        })
    return sorted(result, key=lambda item: (-item['monthly_cost_usd'], -item['monthly_cost_ars']))


def _monthly_totals(statements: list[sqlite3.Row], usd_balance_by_statement: dict[int, float]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in statements:
        month = (row['closing_date'] or row['created_at'])[:7]
        bucket = grouped.setdefault(month, {'month': month, 'total_to_pay_ars': 0.0, 'usd_balance': 0.0, 'statement_count': 0})
        bucket['total_to_pay_ars'] += row['total_to_pay_ars'] or 0
        bucket['usd_balance'] += usd_balance_by_statement.get(row['id'], 0)
        bucket['statement_count'] += 1
    result = list(grouped.values())
    for item in result:
        item['total_to_pay_ars'] = round(item['total_to_pay_ars'], 2)
        item['usd_balance'] = round(item['usd_balance'], 2)
    return sorted(result, key=lambda item: item['month'])


def _ai_optimizer(top_subscriptions: list[dict[str, Any]], usage_by_provider: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for subscription in top_subscriptions:
        if subscription['category'] != 'AI':
            continue
        provider = subscription['provider']
        usage = usage_by_provider.get(provider)
        cost = subscription['monthly_cost_usd']
        days = usage['days_used_per_month'] if usage else None
        cost_per_day = round(cost / days, 2) if days else None
        recommendation = 'needs_usage'
        reason = 'Necesito que cargues uso o conectes read-only usage para decidir con confianza.'
        if usage:
            replacement = usage.get('replacement')
            if days <= 3 and cost >= 50:
                recommendation = 'cancel_or_downgrade'
                reason = f'Uso bajo para el costo. Probá cancelar/downgradear y cubrirlo con {replacement or "otra herramienta"}.'
            elif days >= 15 or usage.get('importance') == 'high':
                recommendation = 'keep'
                reason = 'Uso alto o importancia alta; probablemente conviene mantener.'
            else:
                recommendation = 'review'
                reason = 'Uso intermedio; compará contra herramientas solapadas antes de renovar.'
        items.append({
            'provider': provider,
            'monthly_cost_usd': cost,
            'days_used_per_month': days,
            'cost_per_used_day_usd': cost_per_day,
            'recommendation': recommendation,
            'reason': reason,
        })
    return items


def _proactive_questions(top_subscriptions: list[dict[str, Any]], usage_by_provider: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    questions = []
    for item in top_subscriptions[:8]:
        provider = item['provider']
        if provider not in usage_by_provider:
            questions.append({
                'provider': provider,
                'question': f'¿Cuántos días al mes usás {provider} y qué tan reemplazable es?',
            })
        if item['category'] == 'AI' and item['monthly_cost_usd'] >= 50:
            questions.append({
                'provider': provider,
                'question': f'{provider} cuesta USD {item["monthly_cost_usd"]:.2f}; ¿es plan individual, team o usage-based?',
            })
    return questions


def _recommendations(ai_optimizer: list[dict[str, Any]], top_subscriptions: list[dict[str, Any]]) -> list[str]:
    recommendations = []
    for item in ai_optimizer:
        if item['recommendation'] == 'cancel_or_downgrade':
            recommendations.append(
                f"Cancelar o downgradear {item['provider']} podría ahorrar USD {item['monthly_cost_usd']:.2f}/mes."
            )
    ai_total = sum(item['monthly_cost_usd'] for item in ai_optimizer)
    if ai_total:
        recommendations.append(f'Gasto AI detectado: USD {ai_total:.2f}/mes. Cargá uso para priorizar qué baja primero.')
    if not recommendations and top_subscriptions:
        recommendations.append('Cargá uso por proveedor para convertir gastos detectados en decisiones keep/cancel concretas.')
    if not recommendations:
        recommendations.append('Subí PDFs de tarjetas para empezar el análisis.')
    return recommendations
