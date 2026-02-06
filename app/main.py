import base64
from io import BytesIO

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

from app import model
from app.config import settings
from app.preprocess import load_image
from app.postprocess import to_response
from app.prompt import PROMPT_TEMPLATE
from app.schemas import AnalyzeResponse, HealthResponse
from app.ui import render_index


def _image_to_data_url(image) -> str:
    buf = BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"

app = FastAPI(title="Diagram Analyzer", version="1.0.0")


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", model=settings.model_id, device=settings.device)


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    try:
        content = await file.read()
        images, extracted_text = load_image(content)
    except Exception:
        raise HTTPException(status_code=400, detail="Unsupported or invalid file format")

    if len(images) == 1:
        output_text = model.infer(images[0], PROMPT_TEMPLATE, extracted_text=extracted_text)
        response = to_response(output_text)
        body = response.model_dump()
        body["preview"] = _image_to_data_url(images[0])
        return JSONResponse(status_code=200, content=body)

    pages = []
    for idx, image in enumerate(images):
        output_text = model.infer(image, PROMPT_TEMPLATE, extracted_text=extracted_text)
        response = to_response(output_text)
        page = {"page": idx + 1, **response.model_dump()}
        page["preview"] = _image_to_data_url(image)
        pages.append(page)
    return JSONResponse(status_code=200, content={"pages": pages})


@app.post("/api/analyze/batch")
async def analyze_batch(files: list[UploadFile] = File(...)):
    results = []
    for file in files:
        try:
            content = await file.read()
            images, extracted_text = load_image(content)
        except Exception:
            results.append({"filename": file.filename, "error": "invalid file"})
            continue
        for image in images:
            output_text = model.infer(image, PROMPT_TEMPLATE, extracted_text=extracted_text)
            response = to_response(output_text)
            results.append({"filename": file.filename, **response.model_dump()})
    return {"count": len(results), "items": results}


@app.get("/", response_class=HTMLResponse)
def web_ui():
    return render_index()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
