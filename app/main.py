from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from PIL import UnidentifiedImageError

from app import model
from app.config import settings
from app.preprocess import load_image
from app.postprocess import to_response
from app.prompt import PROMPT_TEMPLATE
from app.schemas import AnalyzeResponse, HealthResponse
from app.ui import render_index

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
    return render_index()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
