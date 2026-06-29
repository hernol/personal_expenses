from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.main import app

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


def test_batch_endpoint_accepts_multiple_pdf_statement_files_and_aggregates():
    client = TestClient(app)
    pdf = sample_pdf_bytes()

    response = client.post(
        '/statements/analyze-batch',
        files=[
            ('files', ('visa_may.pdf', pdf, 'application/pdf')),
            ('files', ('visa_june.pdf', pdf, 'application/pdf')),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert data['file_count'] == 2
    assert data['aggregate']['total_to_pay_ars'] == 448284.92
    assert data['aggregate']['total_usd_subscriptions'] == 474.14
    assert data['aggregate']['categories'][0]['name'] == 'AI'
    assert data['aggregate']['categories'][0]['provider_count'] == 4
    assert data['files'][0]['filename'] == 'visa_may.pdf'


def test_root_serves_pdf_upload_ui():
    client = TestClient(app)

    response = client.get('/')

    assert response.status_code == 200
    assert 'Cargar resúmenes' in response.text
    assert 'directamente los PDFs' in response.text
    assert 'multiple' in response.text
    assert 'accept=".pdf,application/pdf"' in response.text
    assert '/statements/analyze-batch' in response.text
