"""Локальный PDF сохранённого снимка; без браузера, сети и новых зависимостей.

Pillow в составе media-импорта уже закреплён. Растровые страницы сохраняют
кириллицу без загрузки шрифтов; для поиска/копирования есть Markdown и JSON.
"""

from html import unescape
from io import BytesIO
from pathlib import Path
import re
from threading import Lock

from PIL import Image, ImageDraw, ImageFont

from .repositories import RepositoryError

_gate = Lock()
FONT_PATHS = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
MAX_PAGES = 30


def _font():
    for path in FONT_PATHS:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, 24)
            except OSError:
                continue
    raise RepositoryError("PDF_FONT_UNAVAILABLE", "Для PDF нужен локальный шрифт с кириллицей. Пока скачайте Markdown или JSON.", 503)


def _lines(text, font):
    # Удаляем только оформление Markdown. Данные не интерпретируются как HTML.
    text = unescape(re.sub(r"\\([\\`*_{}\[\]()#+.!|>~-])", r"\1", text))
    for paragraph in text.splitlines():
        paragraph = re.sub(r"^#{1,6} ", "", paragraph).expandtabs(4)
        if not paragraph:
            yield ""
        while paragraph:
            # Двоичный поиск ограничивает работу и для очень длинного ID/URL.
            lo, hi = 1, min(300, len(paragraph))
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.getlength(paragraph[:mid]) <= 1060:
                    lo = mid
                else:
                    hi = mid - 1
            cut = lo
            if cut < len(paragraph):
                space = paragraph.rfind(" ", 0, cut + 1)
                if space > cut // 2:
                    cut = space
            yield paragraph[:cut]
            paragraph = paragraph[cut:].lstrip()


def render_pdf(snapshot):
    if not _gate.acquire(blocking=False):
        raise RepositoryError("PDF_BUSY", "Другой PDF ещё формируется. Повторите скачивание через несколько секунд.", 409)
    pages = []
    try:
        font = _font()
        line_height, per_page = 36, 41
        lines = []
        content = (f"GPN Insight | {snapshot['title']}\n"
            f"Снимок: {snapshot['report_id']}\nРежим: {snapshot['model_mode']} | {snapshot['generated_at']}\n\n"
            + snapshot["markdown"] + "\n\n" + snapshot["limitations"])
        for line in _lines(content, font):
            lines.append(line)
            if len(lines) > per_page * MAX_PAGES:
                raise RepositoryError("PDF_TOO_LARGE", "PDF превышает 30 страниц. Скачайте полный Markdown или JSON; содержимое не обрезано.", 413)
        for offset in range(0, len(lines), per_page):
            page = Image.new("RGB", (1240, 1754), "white")
            pages.append(page)
            draw = ImageDraw.Draw(page)
            draw.rectangle((0, 0, 1240, 18), fill="#5953e4")
            for index, line in enumerate(lines[offset:offset + per_page]):
                draw.text((90, 90 + index * line_height), line, font=font, fill="#26314a")
            draw.text((90, 1660), f"GPN Insight · {offset // per_page + 1} / {(len(lines) + per_page - 1) // per_page}", font=font, fill="#73798b")
        output = BytesIO()
        pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=150, title=snapshot["title"], author="GPN Insight")
        return output.getvalue()
    finally:
        for page in pages:
            page.close()
        _gate.release()
