PROMPT_TEMPLATE = r"""Внимательно рассмотри приложенное изображение диаграммы. Прочитай КАЖДЫЙ текст на изображении точно как он написан. НЕ ПРИДУМЫВАЙ текст — используй ТОЛЬКО то, что видишь на картинке.

Верни ТОЛЬКО валидный JSON (без markdown-блоков, без лишнего текста):
{
  "diagram_type": "bpmn|flowchart|other",
  "description": "краткое описание диаграммы на русском",
  "steps": [
    {
      "id": "<step id number or string, e.g. 'start' or '7.1'>",
      "action": "<exact Russian text inside the node; if the node has no text leave empty string>",
      "type": "start|end|task|decision",
      "role": "<lane/actor or null>",
      "next_steps": [
        {"to": "<target id>", "label": "<arrow text/condition or empty string>"}
      ]
    }
  ]
}
Правила:
- НЕ ВЫДУМЫВАЙ содержимое. Каждый action должен быть ДОСЛОВНОЙ копией текста из узла на изображении.
- Если не можешь прочитать текст — напиши "нечитаемо", но НЕ ПРИДУМЫВАЙ.
- Не добавляй markdown, только чистый JSON.
- Числовые префиксы (например "7.1", "ИК 7.2") сохраняй в action и id.
- Подписи стрелок (да/нет и т.п.) заноси только в next_steps.label.
- Если в узле нет текста, action должен быть пустой строкой.
- type подбирай корректно: start/end/task/decision.
- Соблюдай порядок обхода по стрелкам сверху-вниз слева-направо от стартового узла.
"""
