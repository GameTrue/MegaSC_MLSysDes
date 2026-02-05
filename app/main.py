from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from PIL import UnidentifiedImageError

from app import model
from app.config import settings
from app.preprocess import load_image
from app.postprocess import to_response
from app.prompt import PROMPT_TEMPLATE
from app.schemas import AnalyzeResponse, HealthResponse

app = FastAPI(title="Diagram Analyzer", version="1.0.0")


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", model=settings.model_name, device=settings.device)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...)):
    try:
        content = await file.read()
        image = load_image(content)
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Invalid image format")
    output_text = model.infer(image, PROMPT_TEMPLATE)
    response = to_response(output_text)
    return JSONResponse(status_code=200, content=response.model_dump())


@app.post("/api/analyze/batch")
async def analyze_batch(files: list[UploadFile] = File(...)):
    results = []
    for file in files:
        try:
            content = await file.read()
            image = load_image(content)
        except UnidentifiedImageError:
            results.append({"filename": file.filename, "error": "invalid image"})
            continue
        output_text = model.infer(image, PROMPT_TEMPLATE)
        response = to_response(output_text)
        results.append({"filename": file.filename, **response.model_dump()})
    return {"count": len(results), "items": results}


@app.get("/", response_class=HTMLResponse)
def web_ui():
    return """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>Diagram Analyzer</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #0b1224;
      --panel: #0f172a;
      --border: #1f2937;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --error: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 32px;
      background: radial-gradient(circle at 20% 20%, #0b1224 0, #0b1224 30%, #070d1a 100%);
      font-family: "Inter", system-ui, -apple-system, sans-serif;
      color: var(--text);
      display: flex; justify-content: center;
    }
    .shell { width: min(1100px, 100%); }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 22px 26px;
      box-shadow: 0 25px 80px rgba(0,0,0,0.35);
    }
    h1 { margin: 0 0 6px; letter-spacing: -0.02em; }
    p.lead { margin: 0 0 16px; color: var(--muted); }
    .controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
    input[type=file] {
      border: 1px dashed var(--border);
      background: rgba(255,255,255,0.02);
      color: var(--text);
      padding: 10px;
      border-radius: 10px;
      max-width: 360px;
    }
    button {
      padding: 10px 18px;
      border-radius: 10px;
      border: 1px solid var(--accent);
      background: linear-gradient(90deg, #1d4ed8, #0ea5e9);
      color: white;
      cursor: pointer;
      font-weight: 600;
      transition: transform .1s ease, box-shadow .1s ease, opacity .2s;
    }
    button:disabled { opacity: .4; cursor: not-allowed; }
    button:not(:disabled):hover { transform: translateY(-1px); box-shadow: 0 10px 30px rgba(14,165,233,0.35); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; margin-top: 12px; }
    .card { border: 1px solid var(--border); border-radius: 12px; padding: 14px; background: rgba(255,255,255,0.02); }
    .card h3 { margin: 0 0 6px; font-size: 15px; }
    pre { background: #0b1224; border: 1px solid var(--border); border-radius: 8px; padding: 10px; overflow-x: auto; margin: 6px 0 0; font-size: 13px; }
    .error { color: var(--error); margin-top: 6px; }
    .status { color: var(--muted); font-size: 13px; margin-top: 6px; }
  </style>
  <script>
    async function sendFiles() {
      const input = document.getElementById('files');
      if (!input.files.length) return alert('Выберите файл(ы)');
      const form = new FormData();
      for (const f of input.files) form.append('files', f);
      const btn = document.getElementById('send');
      const out = document.getElementById('out');
      btn.disabled = true;
      out.innerHTML = '<div class="status">Отправляем и ждём ответ...</div>';
      try {
        const res = await fetch('/api/analyze/batch', { method: 'POST', body: form });
        const text = await res.text();
        let data;
        try { data = JSON.parse(text); } catch { data = { error: 'Невалидный JSON', raw: text }; }
        out.innerHTML = '';
        if (!res.ok) {
          out.innerHTML = `<div class="error">Ошибка ${res.status}: ${res.statusText}</div><pre>${JSON.stringify(data, null, 2)}</pre>`;
          return;
        }
        if (data.items) {
          const grid = document.createElement('div');
          grid.className = 'grid';
          data.items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'card';
            div.innerHTML = `<h3>${item.filename || 'file'}</h3><pre>${JSON.stringify(item, null, 2)}</pre>`;
            grid.appendChild(div);
          });
          out.appendChild(grid);
        } else {
          out.textContent = JSON.stringify(data, null, 2);
        }
      } catch (e) {
        out.innerHTML = `<div class="error">Ошибка запроса: ${e}</div>`;
      } finally {
        btn.disabled = false;
      }
    }
  </script>
</head>
<body>
  <div class="shell">
    <div class="panel">
      <h1>Diagram Analyzer</h1>
      <p class="lead">Прикрепите один или несколько файлов (PNG/JPG/WEBP) и получите структурированный JSON со связями шагов.</p>
      <div class="controls">
        <input type="file" id="files" multiple accept="image/*">
        <button id="send" onclick="sendFiles()">Отправить</button>
      </div>
      <div id="out" class="grid"></div>
    </div>
  </div>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
