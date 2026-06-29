from __future__ import annotations

from collections import defaultdict

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.analysis import analyze_statement
from app.models import AnalysisReport, Statement
from app.parser import parse_statement_text
from app.storage import analytics_summary, calculate_what_if, delete_all_statements, delete_statement, list_statements, save_statement, save_usage

app = FastAPI(
    title='Card Expense Analyzer',
    description='Analiza resúmenes de tarjeta para detectar suscripciones, providers de AI y gastos recortables.',
    version='0.2.0',
)


class ManualUsageInput(BaseModel):
    provider: str = Field(min_length=1)
    days_used_per_month: int = Field(ge=0, le=31)
    importance: str = Field(pattern='^(low|medium|high)$')
    replacement: str | None = None
    notes: str | None = None


class WhatIfInput(BaseModel):
    cancel_providers: list[str] = Field(default_factory=list)
    usd_to_ars_rate: float = Field(default=1200, ge=0)


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/', response_class=HTMLResponse)
def upload_ui() -> str:
    return _dashboard_html()


@app.get('/dashboard', response_class=HTMLResponse)
def dashboard() -> str:
    return _dashboard_html()


@app.get('/analytics/summary')
def get_analytics_summary() -> dict:
    return analytics_summary()


@app.post('/usage/manual')
def save_manual_usage(payload: ManualUsageInput) -> dict:
    return save_usage(
        provider=payload.provider.strip(),
        days_used_per_month=payload.days_used_per_month,
        importance=payload.importance,
        replacement=payload.replacement.strip() if payload.replacement else None,
        notes=payload.notes,
    )


@app.post('/what-if')
def simulate_what_if(payload: WhatIfInput) -> dict:
    return calculate_what_if(payload.cancel_providers, payload.usd_to_ars_rate)


@app.get('/statements')
def get_statements() -> dict:
    return {'statements': list_statements()}


@app.delete('/statements')
def remove_all_statements() -> dict:
    deleted_count = delete_all_statements()
    return {'deleted': True, 'deleted_count': deleted_count}


@app.delete('/statements/{statement_id}')
def remove_statement(statement_id: int) -> dict:
    if not delete_statement(statement_id):
        raise HTTPException(status_code=404, detail=f'No encontré el resumen {statement_id}.')
    return {'deleted': True, 'statement_id': statement_id}


@app.post('/statements/analyze')
async def analyze_uploaded_statement(file: UploadFile = File(...)):
    statement, report = await _parse_and_analyze_file(file)
    statement_id = save_statement(statement, file.filename or 'statement.pdf')
    data = report.model_dump(mode='json')
    data['persisted'] = True
    data['statement_id'] = statement_id
    return data


@app.post('/statements/analyze-batch')
async def analyze_uploaded_statements(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail='Subí al menos un archivo.')

    file_reports = []
    reports: list[AnalysisReport] = []
    statement_ids: list[int] = []
    for file in files:
        statement, report = await _parse_and_analyze_file(file)
        statement_id = save_statement(statement, file.filename or 'statement.pdf')
        statement_ids.append(statement_id)
        reports.append(report)
        file_reports.append({'filename': file.filename, 'statement_id': statement_id, 'report': report.model_dump(mode='json')})

    return {
        'file_count': len(files),
        'persisted': True,
        'statement_ids': statement_ids,
        'files': file_reports,
        'aggregate': _aggregate_reports(reports),
    }


async def _analyze_file(file: UploadFile) -> AnalysisReport:
    _, report = await _parse_and_analyze_file(file)
    return report


async def _parse_and_analyze_file(file: UploadFile) -> tuple[Statement, AnalysisReport]:
    raw = await file.read()
    text = _extract_text(raw, file.filename or '')
    if 'DETALLE DEL CONSUMO' not in text:
        raise HTTPException(status_code=400, detail=f'No encontré DETALLE DEL CONSUMO en {file.filename}.')
    statement = parse_statement_text(text)
    return statement, analyze_statement(statement)


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
        text = '\n'.join(page.get_text() for page in doc).strip()
        if not text:
            raise HTTPException(
                status_code=400,
                detail='PDF no tiene texto extraíble. Probablemente es un escaneo/imagen; hace falta OCR.',
            )
        return text
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail='No pude extraer texto del PDF.') from exc


def _decode_text(raw: bytes) -> str:
    for encoding in ('utf-8', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail='No pude decodificar el archivo como texto.')


def _dashboard_html() -> str:
    return '''<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Personal Expenses Advisor</title>
  <!-- Chart.js -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: Inter, system-ui, sans-serif; margin: 0; background: #0f172a; color: #e5e7eb; }
    main { max-width: 1180px; margin: 40px auto; padding: 0 24px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }
    .card { background: #111827; border: 1px solid #334155; border-radius: 18px; padding: 22px; box-shadow: 0 20px 60px #0008; }
    h1 { margin: 0 0 8px; font-size: 34px; }
    h2 { margin-top: 0; }
    p, li { color: #cbd5e1; }
    input, select, textarea, button { font-size: 15px; box-sizing: border-box; }
    input, select, textarea { width: 100%; margin-top: 8px; padding: 10px; border-radius: 10px; border: 1px solid #334155; background: #020617; color: #e5e7eb; }
    input[type=file] { padding: 18px; border: 1px dashed #64748b; }
    label { display: block; margin-top: 10px; color: #dbeafe; }
    button { margin-top: 14px; padding: 11px 16px; border: 0; border-radius: 12px; background: #38bdf8; color: #082f49; font-weight: 800; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px; border-bottom: 1px solid #334155; text-align: left; }
    th { color: #93c5fd; }
    .metric { font-size: 28px; font-weight: 900; color: #7dd3fc; }
    .hint { font-size: 14px; color: #94a3b8; }
    pre { overflow: auto; background: #020617; border-radius: 14px; padding: 14px; border: 1px solid #334155; max-height: 260px; }
  </style>
</head>
<body>
  <main>
    <h1>Personal Expenses Advisor</h1>
    <p>Graphs, análisis, recomendaciones proactivas y comparativa de uso/costo para decidir qué mantener o dar de baja.</p>

    <section class="card">
      <h2>Cargar resúmenes</h2>
      <p>Subí directamente los PDFs de tus tarjetas. Podés seleccionar varios meses y varias tarjetas a la vez.</p>
      <form id="upload-form">
        <input id="files" name="files" type="file" multiple accept=".pdf,application/pdf" />
        <button>Analizar y guardar</button>
      </form>
      <p class="hint">Funciona con PDFs que tengan texto seleccionable. PDFs escaneados requieren OCR.</p>
    </section>

    <section class="card" style="margin-top:18px">
      <h2>Resúmenes cargados</h2>
      <p class="hint">Si un parseo quedó mal, borrá ese resumen y volvé a subir el PDF.</p>
      <button id="delete-all-statements">Borrar todos</button>
      <table><thead><tr><th>ID</th><th>Archivo</th><th>Cierre</th><th>Transacciones</th><th>Total ARS</th><th></th></tr></thead><tbody id="statements"></tbody></table>
    </section>

    <section class="grid" style="margin-top:18px">
      <div class="card"><h2>Total ARS</h2><div class="metric" id="total-ars">-</div></div>
      <div class="card"><h2>Total USD</h2><div class="metric" id="total-usd">-</div></div>
      <div class="card"><h2>Resúmenes</h2><div class="metric" id="statement-count">-</div></div>
    </section>

    <section class="grid" style="margin-top:18px">
      <div class="card"><h2>Gasto por categoría</h2><canvas id="category-chart"></canvas></div>
      <div class="card"><h2>Gasto mensual</h2><canvas id="monthly-chart"></canvas></div>
    </section>

    <section class="card" style="margin-top:18px">
      <h2>AI Cost Optimizer</h2>
      <p class="hint">Cargá uso manual ahora; después se pueden agregar conectores read-only de OpenAI, Claude, Cursor, etc.</p>
      <table>
        <thead><tr><th>Provider</th><th>USD/mes</th><th>Días usados</th><th>USD/día usado</th><th>Decisión</th><th>Motivo</th></tr></thead>
        <tbody id="ai-optimizer"></tbody>
      </table>
    </section>

    <section class="grid" style="margin-top:18px">
      <div class="card">
        <h2>Cargar uso manual</h2>
        <form id="usage-form">
          <label>Provider<input id="provider" placeholder="Cursor" required /></label>
          <label>Días usados por mes<input id="days" type="number" min="0" max="31" value="0" required /></label>
          <label>Importancia<select id="importance"><option value="low">baja</option><option value="medium">media</option><option value="high">alta</option></select></label>
          <label>Reemplazo posible<input id="replacement" placeholder="ChatGPT, Claude, Gemini..." /></label>
          <label>Notas<textarea id="notes" placeholder="Uso laboral, personal, si es plan team, etc."></textarea></label>
          <button>Guardar uso</button>
        </form>
      </div>
      <div class="card">
        <h2>Recomendaciones proactivas</h2>
        <ul id="recommendations"></ul>
        <h2>Preguntas pendientes</h2>
        <ul id="questions"></ul>
      </div>
    </section>

    <section class="card" style="margin-top:18px">
      <h2>What-if simulator</h2>
      <p class="hint">Marcá servicios a cancelar y calculá ahorro mensual/anual. El tipo de cambio es editable para estimar equivalente ARS.</p>
      <label>Tipo de cambio USD→ARS<input id="usd-rate" type="number" min="0" value="1200" /></label>
      <button id="simulate-button">Simular ahorro</button>
      <pre id="what-if-result">Elegí subscriptions de la tabla y simulá.</pre>
    </section>

    <section class="card" style="margin-top:18px">
      <h2>Top subscriptions</h2>
      <table><thead><tr><th>Cancelar?</th><th>Provider</th><th>Categoría</th><th>USD</th><th>ARS</th></tr></thead><tbody id="subscriptions"></tbody></table>
    </section>

    <section class="card" style="margin-top:18px">
      <h2>JSON crudo</h2>
      <pre id="out">Cargando...</pre>
    </section>
  </main>

  <script>
    let categoryChart, monthlyChart;
    const money = (value, currency) => new Intl.NumberFormat('es-AR', { style: 'currency', currency }).format(value || 0);

    async function refresh() {
      const summary = await fetch('/analytics/summary').then(r => r.json());
      const statements = await fetch('/statements').then(r => r.json());
      document.querySelector('#out').textContent = JSON.stringify(summary, null, 2);
      document.querySelector('#total-ars').textContent = money(summary.totals.total_to_pay_ars, 'ARS');
      document.querySelector('#total-usd').textContent = money(summary.totals.usd_balance, 'USD');
      document.querySelector('#statement-count').textContent = summary.statement_count;
      document.querySelector('#statements').innerHTML = statements.statements.map(item => `
        <tr><td>${item.id}</td><td>${item.filename}</td><td>${item.closing_date || '-'}</td><td>${item.transaction_count}</td><td>${money(item.total_to_pay_ars, 'ARS')}</td><td><button class="delete-statement" data-id="${item.id}">Borrar</button></td></tr>
      `).join('');
      document.querySelectorAll('.delete-statement').forEach(button => {
        button.addEventListener('click', async () => {
          if (!confirm('¿Borrar este resumen parseado? Después podés volver a subir el PDF.')) return;
          await fetch(`/statements/${button.dataset.id}`, { method: 'DELETE' });
          await refresh();
        });
      });

      const categories = summary.category_totals.map(x => x.category);
      const categoryUsd = summary.category_totals.map(x => x.total_usd);
      if (categoryChart) categoryChart.destroy();
      categoryChart = new Chart(document.querySelector('#category-chart'), {
        type: 'doughnut',
        data: { labels: categories, datasets: [{ label: 'USD', data: categoryUsd }] }
      });

      if (monthlyChart) monthlyChart.destroy();
      monthlyChart = new Chart(document.querySelector('#monthly-chart'), {
        type: 'bar',
        data: {
          labels: summary.monthly_totals.map(x => x.month),
          datasets: [
            { label: 'ARS total', data: summary.monthly_totals.map(x => x.total_to_pay_ars) },
            { label: 'USD balance', data: summary.monthly_totals.map(x => x.usd_balance) }
          ]
        }
      });

      document.querySelector('#ai-optimizer').innerHTML = summary.ai_optimizer.map(item => `
        <tr><td>${item.provider}</td><td>${money(item.monthly_cost_usd, 'USD')}</td><td>${item.days_used_per_month ?? '-'}</td><td>${item.cost_per_used_day_usd ? money(item.cost_per_used_day_usd, 'USD') : '-'}</td><td>${item.recommendation}</td><td>${item.reason}</td></tr>
      `).join('');
      document.querySelector('#recommendations').innerHTML = summary.recommendations.map(x => `<li>${x}</li>`).join('');
      document.querySelector('#questions').innerHTML = summary.proactive_questions.map(x => `<li><b>${x.provider}:</b> ${x.question}</li>`).join('');
      document.querySelector('#subscriptions').innerHTML = summary.top_subscriptions.map(x => `
        <tr><td><input class="what-if-provider" type="checkbox" value="${x.provider}" /></td><td>${x.provider}</td><td>${x.category}</td><td>${money(x.monthly_cost_usd, 'USD')}</td><td>${money(x.monthly_cost_ars, 'ARS')}</td></tr>
      `).join('');
    }

    document.querySelector('#upload-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = new FormData();
      for (const file of document.querySelector('#files').files) data.append('files', file);
      document.querySelector('#out').textContent = 'Analizando PDFs...';
      await fetch('/statements/analyze-batch', { method: 'POST', body: data });
      await refresh();
    });

    document.querySelector('#usage-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      await fetch('/usage/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: document.querySelector('#provider').value,
          days_used_per_month: Number(document.querySelector('#days').value),
          importance: document.querySelector('#importance').value,
          replacement: document.querySelector('#replacement').value || null,
          notes: document.querySelector('#notes').value || null,
        })
      });
      await refresh();
    });

    document.querySelector('#delete-all-statements').addEventListener('click', async () => {
      if (!confirm('¿Borrar TODOS los resúmenes parseados? Esta acción no borra tus PDFs originales, pero vas a tener que volver a subirlos.')) return;
      await fetch('/statements', { method: 'DELETE' });
      await refresh();
    });

    document.querySelector('#simulate-button').addEventListener('click', async () => {
      const cancelProviders = [...document.querySelectorAll('.what-if-provider:checked')].map(input => input.value);
      const result = await fetch('/what-if', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cancel_providers: cancelProviders,
          usd_to_ars_rate: Number(document.querySelector('#usd-rate').value || 0),
        })
      }).then(r => r.json());
      document.querySelector('#what-if-result').textContent = JSON.stringify(result, null, 2);
    });

    refresh();
  </script>
</body>
</html>'''
