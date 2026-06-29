from __future__ import annotations

from collections import defaultdict

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from app.analysis import analyze_statement
from app.models import AnalysisReport
from app.parser import parse_statement_text

app = FastAPI(
    title='Card Expense Analyzer',
    description='Analiza resúmenes de tarjeta para detectar suscripciones, providers de AI y gastos recortables.',
    version='0.1.0',
)


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/', response_class=HTMLResponse)
def upload_ui() -> str:
    return '''<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Card Expense Analyzer</title>
  <style>
    body { font-family: Inter, system-ui, sans-serif; margin: 0; background: #0f172a; color: #e5e7eb; }
    main { max-width: 980px; margin: 48px auto; padding: 0 24px; }
    .card { background: #111827; border: 1px solid #334155; border-radius: 18px; padding: 28px; box-shadow: 0 20px 60px #0008; }
    h1 { margin-top: 0; font-size: 34px; }
    p { color: #cbd5e1; }
    input, button { font-size: 16px; }
    input[type=file] { display: block; width: 100%; padding: 18px; border: 1px dashed #64748b; border-radius: 14px; background: #020617; }
    button { margin-top: 18px; padding: 12px 18px; border: 0; border-radius: 12px; background: #38bdf8; color: #082f49; font-weight: 800; cursor: pointer; }
    pre { overflow: auto; background: #020617; border-radius: 14px; padding: 18px; border: 1px solid #334155; }
    .hint { font-size: 14px; color: #94a3b8; }
  </style>
</head>
<body>
  <main>
    <section class="card">
      <h1>Cargar resúmenes</h1>
      <p>Subí todos los TXT exportados de tus tarjetas. Podés seleccionar varios meses y varias tarjetas a la vez.</p>
      <form id="form">
        <input id="files" name="files" type="file" multiple accept=".txt,.pdf,text/plain,application/pdf" />
        <button>Analizar gastos</button>
      </form>
      <p class="hint">Hoy el parser está calibrado con tu resumen Visa en TXT. PDFs directos dependen de que el texto sea extraíble.</p>
      <h2>Resultado</h2>
      <pre id="out">Esperando archivos...</pre>
    </section>
  </main>
  <script>
    const form = document.querySelector('#form');
    const out = document.querySelector('#out');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = new FormData();
      for (const file of document.querySelector('#files').files) data.append('files', file);
      out.textContent = 'Analizando...';
      const response = await fetch('/statements/analyze-batch', { method: 'POST', body: data });
      const json = await response.json();
      out.textContent = JSON.stringify(json, null, 2);
    });
  </script>
</body>
</html>'''


@app.post('/statements/analyze')
async def analyze_uploaded_statement(file: UploadFile = File(...)):
    report = await _analyze_file(file)
    return report.model_dump(mode='json')


@app.post('/statements/analyze-batch')
async def analyze_uploaded_statements(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail='Subí al menos un archivo.')

    file_reports = []
    reports: list[AnalysisReport] = []
    for file in files:
        report = await _analyze_file(file)
        reports.append(report)
        file_reports.append({'filename': file.filename, 'report': report.model_dump(mode='json')})

    return {
        'file_count': len(files),
        'files': file_reports,
        'aggregate': _aggregate_reports(reports),
    }


async def _analyze_file(file: UploadFile) -> AnalysisReport:
    raw = await file.read()
    text = _extract_text(raw, file.filename or '')
    if 'DETALLE DEL CONSUMO' not in text:
        raise HTTPException(status_code=400, detail=f'No encontré DETALLE DEL CONSUMO en {file.filename}.')
    statement = parse_statement_text(text)
    return analyze_statement(statement)


def _aggregate_reports(reports: list[AnalysisReport]) -> dict:
    category_items = defaultdict(list)
    for report in reports:
        for category in report.categories:
            category_items[category.name].extend(category.items)

    categories = []
    total_usd_subscriptions = 0.0
    for name, items in category_items.items():
        total_ars = round(sum(item.amount for item in items if item.currency == 'ARS'), 2)
        total_usd = round(sum(item.amount for item in items if item.currency == 'USD'), 2)
        total_usd_subscriptions += total_usd
        categories.append({
            'name': name,
            'provider_count': len({item.provider for item in items}),
            'total_ars': total_ars,
            'total_usd': total_usd,
            'items': [item.model_dump(mode='json') for item in items],
        })

    categories.sort(key=lambda category: (category['name'] != 'AI', -category['total_usd'], -category['total_ars']))
    return {
        'total_to_pay_ars': round(sum((report.summary.get('total_to_pay_ars') or 0) for report in reports), 2),
        'total_usd_balance': round(sum((report.summary.get('usd_balance') or 0) for report in reports), 2),
        'total_usd_subscriptions': round(total_usd_subscriptions, 2),
        'categories': categories,
        'top_recurring_candidates': sorted(
            [item.model_dump(mode='json') for report in reports for item in report.recurring_candidates],
            key=lambda item: item['amount'],
            reverse=True,
        )[:20],
    }


def _extract_text(raw: bytes, filename: str) -> str:
    if filename.lower().endswith('.pdf') or raw.startswith(b'%PDF'):
        return _extract_pdf_text(raw)
    return _decode_text(raw)


def _extract_pdf_text(raw: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise HTTPException(status_code=400, detail='Para leer PDF directo falta instalar PyMuPDF.') from exc

    try:
        doc = fitz.open(stream=raw, filetype='pdf')
        return '\n'.join(page.get_text() for page in doc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail='No pude extraer texto del PDF.') from exc


def _decode_text(raw: bytes) -> str:
    for encoding in ('utf-8', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail='No pude decodificar el archivo como texto.')
