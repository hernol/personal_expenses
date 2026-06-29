import os
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.main import app
from app.storage import analytics_summary, connect

SAMPLE_PATH = Path('/home/hernol/3d0c3e4c-c901-479a-89a0-ebc2e2a92418.txt')


def sample_text() -> str:
    return SAMPLE_PATH.read_text(encoding='utf-8')


def sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    lines = sample_text().splitlines()
    for start in range(0, len(lines), 48):
        page = doc.new_page(width=595, height=842)
        y = 36
        for line in lines[start:start + 48]:
            page.insert_text((36, y), line or ' ', fontsize=8, fontname='courier')
            y += 16
    return doc.tobytes()


def test_pdf_batch_persists_and_analytics_summary_drives_graphs(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    client = TestClient(app)
    pdf = sample_pdf_bytes()

    response = client.post(
        '/statements/analyze-batch',
        files=[
            ('files', ('visa-2026-05.pdf', pdf, 'application/pdf')),
            ('files', ('visa-2026-06.pdf', pdf, 'application/pdf')),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert data['persisted'] is True
    assert len(data['statement_ids']) == 2

    summary = client.get('/analytics/summary').json()
    assert summary['statement_count'] == 2
    assert summary['totals']['total_to_pay_ars'] == 448284.92
    assert summary['totals']['usd_balance'] == 474.14
    assert summary['category_totals'][0]['category'] == 'AI'
    assert summary['category_totals'][0]['total_usd'] == 474.14
    assert summary['monthly_totals'][0]['month'] == '2026-04'
    assert summary['monthly_totals'][0]['total_to_pay_ars'] == 448284.92
    assert summary['top_subscriptions'][0]['provider'] == 'Cursor'
    assert summary['top_subscriptions'][0]['monthly_cost_usd'] == 384.0
    assert any(question['provider'] == 'Cursor' for question in summary['proactive_questions'])


def test_manual_usage_makes_ai_cost_optimizer_recommend_cancellation(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    client = TestClient(app)

    client.post(
        '/statements/analyze',
        files={'file': ('visa.pdf', sample_pdf_bytes(), 'application/pdf')},
    )
    usage_response = client.post('/usage/manual', json={
        'provider': 'Cursor',
        'days_used_per_month': 3,
        'importance': 'low',
        'replacement': 'ChatGPT',
        'notes': 'Lo use poco este mes',
    })

    assert usage_response.status_code == 200
    summary = client.get('/analytics/summary').json()
    cursor = next(item for item in summary['ai_optimizer'] if item['provider'] == 'Cursor')
    assert cursor['monthly_cost_usd'] == 192.0
    assert cursor['cost_per_used_day_usd'] == 64.0
    assert cursor['recommendation'] == 'cancel_or_downgrade'
    assert 'ChatGPT' in cursor['reason']


def test_what_if_simulator_calculates_monthly_and_annual_savings(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    client = TestClient(app)
    client.post(
        '/statements/analyze',
        files={'file': ('visa.pdf', sample_pdf_bytes(), 'application/pdf')},
    )

    response = client.post('/what-if', json={
        'cancel_providers': ['Cursor', 'Google AI/One'],
        'usd_to_ars_rate': 1200,
    })

    assert response.status_code == 200
    data = response.json()
    assert data['cancel_providers'] == ['Cursor', 'Google AI/One']
    assert data['monthly_savings_usd'] == 201.99
    assert data['monthly_savings_ars_equivalent'] == 242388.0
    assert data['annual_savings_usd'] == 2423.88
    assert data['annual_savings_ars_equivalent'] == 2908656.0
    assert data['items'][0]['provider'] == 'Cursor'
    assert data['items'][0]['monthly_cost_usd'] == 192.0


def test_can_list_and_delete_parsed_statements_for_reupload(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    client = TestClient(app)
    pdf = sample_pdf_bytes()

    upload = client.post(
        '/statements/analyze-batch',
        files=[
            ('files', ('bad-parse-a.pdf', pdf, 'application/pdf')),
            ('files', ('bad-parse-b.pdf', pdf, 'application/pdf')),
        ],
    ).json()
    first_id = upload['statement_ids'][0]

    listed = client.get('/statements').json()
    assert [item['filename'] for item in listed['statements']] == ['bad-parse-a.pdf', 'bad-parse-b.pdf']
    assert listed['statements'][0]['transaction_count'] == 8

    delete_response = client.delete(f'/statements/{first_id}')

    assert delete_response.status_code == 200
    assert delete_response.json() == {'deleted': True, 'statement_id': first_id}
    summary = client.get('/analytics/summary').json()
    assert summary['statement_count'] == 1
    assert summary['totals']['total_to_pay_ars'] == 224142.46
    listed_after_delete = client.get('/statements').json()
    assert [item['filename'] for item in listed_after_delete['statements']] == ['bad-parse-b.pdf']


def test_deleting_missing_statement_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    client = TestClient(app)

    response = client.delete('/statements/999')

    assert response.status_code == 404
    assert 'No encontré el resumen' in response.json()['detail']


def test_analytics_ignores_legacy_usd_balance_when_statement_has_no_usd_transactions(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    with connect() as conn:
        conn.execute(
            '''
            INSERT INTO statements (filename, card_brand, closing_date, due_date, total_to_pay_ars, usd_balance, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            ('bad-mastercard.pdf', 'MASTERCARD', '2026-04-30', None, 381948.90, 35000.0, '2026-06-29T00:00:00Z'),
        )
        conn.commit()

    summary = analytics_summary()

    assert summary['totals']['usd_balance'] == 0
    assert summary['monthly_totals'][0]['usd_balance'] == 0


def test_analytics_keeps_small_legacy_usd_balance_without_transactions(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    with connect() as conn:
        conn.execute(
            '''
            INSERT INTO statements (filename, card_brand, closing_date, due_date, total_to_pay_ars, usd_balance, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            ('small-usd.pdf', 'MASTERCARD', '2026-03-30', None, 1000.0, 2.47, '2026-06-29T00:00:00Z'),
        )
        conn.commit()

    summary = analytics_summary()

    assert summary['totals']['usd_balance'] == 2.47
    assert summary['monthly_totals'][0]['usd_balance'] == 2.47


def test_dashboard_serves_graphs_recommendations_usage_form_and_what_if_simulator(tmp_path, monkeypatch):
    monkeypatch.setenv('CARD_EXPENSE_DB', str(tmp_path / 'expenses.db'))
    client = TestClient(app)

    response = client.get('/dashboard')

    assert response.status_code == 200
    assert 'Chart.js' in response.text
    assert 'AI Cost Optimizer' in response.text
    assert 'What-if simulator' in response.text
    assert 'Resúmenes cargados' in response.text
    assert 'Borrar' in response.text
    assert '/statements' in response.text
    assert '/analytics/summary' in response.text
    assert '/usage/manual' in response.text
    assert '/what-if' in response.text
    assert 'Recomendaciones proactivas' in response.text
