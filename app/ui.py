from pathlib import Path


class UiRenderer:
    def __init__(self, template_path: Path | None = None):
        self.template_path = template_path or Path(__file__).parent / "static" / "index.html"

    def render(self) -> str:
        return self.template_path.read_text(encoding="utf-8")


renderer = UiRenderer()


def render_index() -> str:
    return renderer.render()
