import gradio as gr
from app import model
from app.prompt import PROMPT_TEMPLATE
from app.postprocess import to_response


def predict(image):
    text = model.infer(image, PROMPT_TEMPLATE)
    parsed = to_response(text)
    steps = "\n".join(f"{s.step}. {s.action}" + (f" ({s.role})" if s.role else "") for s in parsed.steps)
    edges = "\n".join(f"{e.from_id} -> {e.to_id}" + (f" [{e.label}]" if e.label else "") for e in parsed.edges)
    return parsed.diagram_type, parsed.description, steps, edges


demo = gr.Interface(
    fn=predict,
    inputs=gr.Image(type="pil"),
    outputs=[
        gr.Textbox(label="Diagram type"),
        gr.Textbox(label="Description"),
        gr.Textbox(label="Steps"),
        gr.Textbox(label="Edges"),
    ],
    title="Diagram Analyzer",
    description="Загрузите BPMN или блок-схему, чтобы получить структурированное описание",
)


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
