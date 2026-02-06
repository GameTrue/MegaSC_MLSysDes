import base64
from io import BytesIO

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

from app import model
from app.config import settings
from app.preprocess import load_image, prepare_tiles, resize_image, stitch_tiles, HIRES_MAX_SIDE
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
        images, extracted_text, bpmn_response = load_image(content)
    except Exception:
        raise HTTPException(status_code=400, detail="Unsupported or invalid file format")

    # Programmatic BPMN extraction — skip model entirely
    if bpmn_response:
        body = bpmn_response.model_dump()
        body["preview"] = _image_to_data_url(images[0])
        return JSONResponse(status_code=200, content=body)

    if len(images) == 1:
        tiles = prepare_tiles(images[0])
        if len(tiles) == 1:
            infer_image = tiles[0]
        else:
            # High-res resize — чище чем склейка тайлов с перекрытием
            infer_image = resize_image(images[0], max_side=HIRES_MAX_SIDE)

        try:
            output_text = model.infer(infer_image, PROMPT_TEMPLATE, extracted_text=extracted_text)
        except Exception:
            # Fallback: resized original at standard resolution
            output_text = model.infer(resize_image(images[0]), PROMPT_TEMPLATE, extracted_text=extracted_text)

        response = to_response(output_text)
        body = response.model_dump()
        body["preview"] = _image_to_data_url(resize_image(images[0]))
        return JSONResponse(status_code=200, content=body)

    # Multi-page PDF — each page is normal size, no tiling needed
    pages = []
    for idx, image in enumerate(images):
        try:
            output_text = model.infer(image, PROMPT_TEMPLATE, extracted_text=extracted_text)
        except Exception:
            output_text = model.infer(resize_image(image), PROMPT_TEMPLATE, extracted_text=extracted_text)
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
            images, extracted_text, bpmn_response = load_image(content)
        except Exception:
            results.append({"filename": file.filename, "error": "invalid file"})
            continue
        if bpmn_response:
            results.append({"filename": file.filename, **bpmn_response.model_dump()})
            continue
        for image in images:
            tiles = prepare_tiles(image)
            if len(tiles) == 1:
                infer_image = tiles[0]
            else:
                infer_image = resize_image(image, max_side=HIRES_MAX_SIDE)

            try:
                output_text = model.infer(infer_image, PROMPT_TEMPLATE, extracted_text=extracted_text)
            except Exception:
                output_text = model.infer(resize_image(image), PROMPT_TEMPLATE, extracted_text=extracted_text)

            response = to_response(output_text)
            results.append({"filename": file.filename, **response.model_dump()})
    return {"count": len(results), "items": results}


@app.get("/", response_class=HTMLResponse)
def web_ui():
    return render_index()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
