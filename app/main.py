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
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-top: 16px; }
    pre { background: #f7f7f7; padding: 8px; overflow-x: auto; }
    button { padding: 8px 16px; }
    .error { color: #b00020; }
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
      out.innerHTML = 'Отправка...';
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
          data.items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'card';
            div.innerHTML = `<h3>${item.filename || 'file'}</h3><pre>${JSON.stringify(item, null, 2)}</pre>`;
            out.appendChild(div);
          });
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
  <h1>Diagram Analyzer</h1>
  <p>Прикрепите один или несколько файлов (PNG/JPG/WEBP), нажмите «Отправить» и получите JSON.</p>
  <input type="file" id="files" multiple accept="image/*">
  <button id="send" onclick="sendFiles()">Отправить</button>
  <div id="out"></div>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
