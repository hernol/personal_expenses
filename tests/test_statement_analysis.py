from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.parser import parse_statement_text
from app.analysis import analyze_statement

SAMPLE_PATH = Path('/home/hernol/3d0c3e4c-c901-479a-89a0-ebc2e2a92418.txt')


def sample_text() -> str:
    return SAMPLE_PATH.read_text(encoding='utf-8')


def test_parse_extracts_purchase_transactions_from_messy_visa_text():
    statement = parse_statement_text(sample_text())

    assert statement.card_brand == 'VISA'
    assert statement.closing_date.isoformat() == '2026-04-30'
    assert statement.total_to_pay_ars == 224142.46

    by_merchant = {tx.merchant: tx for tx in statement.transactions}
    assert by_merchant['MERPAGO*METASARGENTIN'].amount == 54166.66
    assert by_merchant['MERPAGO*METASARGENTIN'].currency == 'ARS'
    assert by_merchant['MERPAGO*METASARGENTIN'].installment == '03/03'

    assert by_merchant['GOOGLE *ChatGPT P1kUnkLi'].amount == 19.99
    assert by_merchant['GOOGLE *ChatGPT P1kUnkLi'].currency == 'USD'
    assert by_merchant['CURSOR, AI POWER in1TZKElB'].amount == 192.00
    assert by_merchant['CURSOR, AI POWER in1TZKElB'].currency == 'USD'


def test_analysis_groups_ai_subscriptions_and_recommends_cuts():
    report = analyze_statement(parse_statement_text(sample_text()))

    ai = next(category for category in report.categories if category.name == 'AI')
    assert ai.provider_count == 4
    assert ai.total_usd == 237.07
    assert {item.provider for item in ai.items} == {'ChatGPT', 'Gomesin IT', 'Cursor', 'Google AI/One'}

    assert report.recurring_candidates[0].provider == 'Cursor'
    assert any('AI' in recommendation for recommendation in report.recommendations)


def test_api_upload_text_returns_report():
    client = TestClient(app)
    response = client.post('/statements/analyze', files={'file': ('visa.txt', sample_text(), 'text/plain')})

    assert response.status_code == 200
    data = response.json()
    assert data['summary']['total_to_pay_ars'] == 224142.46
    assert data['summary']['usd_balance'] == 237.07
    assert data['categories'][0]['name'] == 'AI'
    assert data['categories'][0]['provider_count'] == 4
