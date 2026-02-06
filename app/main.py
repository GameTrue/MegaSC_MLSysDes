import asyncio
import base64
import logging
from io import BytesIO

from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

from app import model
from app.config import settings
from app.preprocess import load_image, prepare_tiles, resize_image, stitch_tiles, HIRES_MAX_SIDE
from app.postprocess import to_response
from app.prompt import PROMPT_TEMPLATE, GENERATE_PROMPT
from app.schemas import AnalyzeResponse, HealthResponse, GenerateRequest, GenerateResponse
from app.ui import render_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _image_to_data_url(image) -> str:
    buf = BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


async def _run_with_disconnect(request: Request, coro):
    """Run coroutine, cancel it if client disconnects."""
    task = asyncio.create_task(coro)
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            raise asyncio.CancelledError("Client disconnected")
        await asyncio.sleep(0.3)
    return task.result()


app = FastAPI(title="Diagram Analyzer", version="1.0.0")


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", model=settings.model_id, device=settings.device)


@app.post("/api/analyze")
async def analyze(request: Request, file: UploadFile = File(...)):
    try:
        content = await file.read()
        images, extracted_text, bpmn_response = load_image(content)
    except Exception:
        logger.exception("Failed to load image from file %s", file.filename)
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
            infer_image = resize_image(images[0], max_side=HIRES_MAX_SIDE)

        try:
            output_text = await _run_with_disconnect(
                request, model.infer(infer_image, PROMPT_TEMPLATE, extracted_text=extracted_text))
        except asyncio.CancelledError:
            return JSONResponse(status_code=499, content={"error": "Client disconnected"})
        except Exception:
            logger.warning("Primary inference failed for %s, trying resized fallback", file.filename, exc_info=True)
            try:
                output_text = await _run_with_disconnect(
                    request, model.infer(resize_image(images[0]), PROMPT_TEMPLATE, extracted_text=extracted_text))
            except asyncio.CancelledError:
                return JSONResponse(status_code=499, content={"error": "Client disconnected"})
            except Exception as e:
                logger.exception("Fallback inference also failed for %s", file.filename)
                raise HTTPException(status_code=500, detail=f"Inference error: {e}")

        response = to_response(output_text)
        body = response.model_dump()
        body["preview"] = _image_to_data_url(resize_image(images[0]))
        return JSONResponse(status_code=200, content=body)

    # Multi-page PDF
    pages = []
    for idx, image in enumerate(images):
        try:
            output_text = await _run_with_disconnect(
                request, model.infer(image, PROMPT_TEMPLATE, extracted_text=extracted_text))
        except asyncio.CancelledError:
            return JSONResponse(status_code=499, content={"error": "Client disconnected"})
        except Exception:
            logger.warning("Inference failed for page %d, trying resized fallback", idx + 1, exc_info=True)
            try:
                output_text = await _run_with_disconnect(
                    request, model.infer(resize_image(image), PROMPT_TEMPLATE, extracted_text=extracted_text))
            except asyncio.CancelledError:
                return JSONResponse(status_code=499, content={"error": "Client disconnected"})
            except Exception as e:
                logger.exception("Fallback inference also failed for page %d", idx + 1)
                raise HTTPException(status_code=500, detail=f"Inference error on page {idx + 1}: {e}")
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
            logger.exception("Failed to load image in batch: %s", file.filename)
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
                output_text = await model.infer(infer_image, PROMPT_TEMPLATE, extracted_text=extracted_text)
            except Exception:
                logger.warning("Batch inference failed for %s, trying resized fallback", file.filename, exc_info=True)
                output_text = await model.infer(resize_image(image), PROMPT_TEMPLATE, extracted_text=extracted_text)

            response = to_response(output_text)
            results.append({"filename": file.filename, **response.model_dump()})
    return {"count": len(results), "items": results}


@app.post("/api/generate", response_model=GenerateResponse)
async def generate_diagram(request: Request, req: GenerateRequest):
    prompt = GENERATE_PROMPT + "\n\nОписание процесса:\n" + req.text
    try:
        raw = await _run_with_disconnect(request, model.infer_text(prompt))
    except asyncio.CancelledError:
        return JSONResponse(status_code=499, content={"error": "Client disconnected"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference error: {e}")
    # Очистка: убрать markdown-обёртки если модель их добавила
    mermaid_code = raw.strip()
    if mermaid_code.startswith("```"):
        lines = mermaid_code.splitlines()
        # Убираем первую строку (```mermaid) и последнюю (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        mermaid_code = "\n".join(lines).strip()
    return GenerateResponse(mermaid=mermaid_code)


@app.get("/", response_class=HTMLResponse)
def web_ui():
    return render_index()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
