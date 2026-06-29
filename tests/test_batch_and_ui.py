from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

SAMPLE_PATH = Path('/home/hernol/3d0c3e4c-c901-479a-89a0-ebc2e2a92418.txt')


def sample_text() -> str:
    return SAMPLE_PATH.read_text(encoding='utf-8')


def test_batch_endpoint_accepts_multiple_statement_files_and_aggregates():
    client = TestClient(app)

    response = client.post(
        '/statements/analyze-batch',
        files=[
            ('files', ('visa_may.txt', sample_text(), 'text/plain')),
            ('files', ('visa_june.txt', sample_text(), 'text/plain')),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert data['file_count'] == 2
    assert data['aggregate']['total_to_pay_ars'] == 448284.92
    assert data['aggregate']['total_usd_subscriptions'] == 474.14
    assert data['aggregate']['categories'][0]['name'] == 'AI'
    assert data['aggregate']['categories'][0]['provider_count'] == 4
    assert data['files'][0]['filename'] == 'visa_may.txt'


def test_root_serves_upload_ui():
    client = TestClient(app)

    response = client.get('/')

    assert response.status_code == 200
    assert 'Cargar resúmenes' in response.text
    assert 'multiple' in response.text
    assert '/statements/analyze-batch' in response.text
