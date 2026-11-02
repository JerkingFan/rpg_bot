"""Рендер текста с LaTeX-формулами ($...$) в PNG через matplotlib mathtext."""
import hashlib
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

DEFAULT_FONT_SIZE = 20
DEFAULT_DPI = 150
MAX_WIDTH_IN = 12      # максимальная ширина картинки в дюймах
LINE_HEIGHT_IN = 0.6   # высота одной строки


def render_latex(source: str, fontsize: int = DEFAULT_FONT_SIZE) -> str:
    """
    Рендерит строку (с LaTeX в $...$) в PNG.
    Возвращает путь к файлу.
    Кэширует по md5(source + fontsize).
    """
    key = hashlib.md5(f"{source}|{fontsize}".encode("utf-8")).hexdigest()[:16]
    out = CACHE_DIR / f"{key}.png"
    if out.exists():
        return str(out)

    lines = source.split("\n") if source else ["(пусто)"]
    n = len(lines)

    height = max(1.0, n * LINE_HEIGHT_IN + 0.4)
    fig = plt.figure(figsize=(MAX_WIDTH_IN, height), dpi=DEFAULT_DPI)
    fig.patch.set_facecolor("white")

    for i, line in enumerate(lines):
        y = 1 - (i + 0.5) / n
        try:
            fig.text(
                0.02, y, line,
                ha="left", va="center",
                fontsize=fontsize,
                fontfamily="DejaVu Sans",  # поддерживает кириллицу
            )
        except Exception:
            # Если LaTeX сломан — рисуем как есть, экранируя $
            fig.text(
                0.02, y, line.replace("$", r"\$"),
                ha="left", va="center",
                fontsize=fontsize,
                fontfamily="DejaVu Sans",
            )

    try:
        plt.savefig(
            out,
            bbox_inches="tight",
            pad_inches=0.5,
            facecolor="white",
            dpi=DEFAULT_DPI,
        )
    except Exception as e:
        logger.exception(f"Не удалось сохранить картинку: {e}")
        raise
    finally:
        plt.close(fig)

    return str(out)
