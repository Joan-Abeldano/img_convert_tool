from __future__ import annotations

import math
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from to_square import (
    MAX_SIDE,
    SVG_EXTS,
    Options,
    SvgBox,
    fmt_num,
    svg_box,
)

PNG_OPTIONS = {"optimize": True}

PNG_MODES = {"1", "L", "LA", "I", "I;16", "I;16B", "I;16L", "I;16N", "P", "RGB", "RGBA"}


ABSOLUTE_UNITS = {"", "px"}


def declared_scale(value: float | None, unit: str, relative: bool, base: float) -> float:
    if value is None or relative or base <= 0 or unit not in ABSOLUTE_UNITS:
        return 1.0
    return value / base


def svg_pixel_size(box: SvgBox) -> tuple[int, int]:
    scale_x = declared_scale(box.width_value, box.width_unit, box.width_relative, box.width)
    scale_y = declared_scale(box.height_value, box.height_unit, box.height_relative, box.height)
    width, height = box.width * scale_x, box.height * scale_y
    if max(width, height) > MAX_SIDE:
        raise ValueError(f"resulting size {fmt_num(width)}x{fmt_num(height)} exceeds {MAX_SIDE}")
    return max(1, math.floor(width + 0.5)), max(1, math.floor(height + 0.5))


class RendererMissing(RuntimeError):
    pass


def load_rgba(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        return image.convert("RGBA")


def render_resvg(src: Path, width: int, height: int) -> Image.Image:
    try:
        import resvg_py
    except ImportError as error:
        raise RendererMissing(f"pip install resvg-py ({error})") from error

    return load_rgba(resvg_py.svg_to_bytes(svg_path=str(src), width=width, height=height))


def render_cairosvg(src: Path, width: int, height: int) -> Image.Image:
    try:
        import cairosvg
    except ImportError as error:
        raise RendererMissing(f"pip install cairosvg ({error})") from error

    return load_rgba(cairosvg.svg2png(url=str(src), output_width=width, output_height=height))


def render_cli(src: Path, width: int, height: int) -> Image.Image:
    commands: list[list[str]] = []
    if shutil.which("rsvg-convert"):
        commands.append(["rsvg-convert", "-w", str(width), "-h", str(height)])
    if shutil.which("inkscape"):
        commands.append([
            "inkscape", "--export-type=png", "--export-filename=-",
            f"--export-width={width}", f"--export-height={height}",
        ])
    for tool in ("magick", "convert"):
        if shutil.which(tool):
            commands.append([tool, "-background", "none", "-resize", f"{width}x{height}!", "png:-"])

    if not commands:
        raise RendererMissing("no rsvg-convert/inkscape/magick found in PATH")

    problems: list[str] = []
    for command in commands:
        try:
            result = subprocess.run([*command, str(src)], capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as error:
            problems.append(f"{command[0]}: {error}")
            continue
        if result.returncode != 0 or not result.stdout:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
            problems.append(f"{command[0]}: {detail[-1] if detail else result.returncode}")
            continue
        return load_rgba(result.stdout)

    raise RuntimeError("; ".join(problems))


RENDERERS = [render_resvg, render_cairosvg, render_cli]


def render_svg(src: Path, width: int, height: int) -> tuple[Image.Image, str]:
    global RENDERERS
    problems: list[str] = []
    for renderer in list(RENDERERS):
        name = renderer.__name__[len("render_"):]
        try:
            return renderer(src, width, height), name
        except Exception as error:
            problems.append(f"{name}: {type(error).__name__}: {error}")
            if isinstance(error, RendererMissing):
                RENDERERS.remove(renderer)
                RENDERERS.append(renderer)

    raise RuntimeError("no SVG renderer available, tried " + "; ".join(problems))


def png_ready(image: Image.Image) -> Image.Image:
    if image.mode in PNG_MODES:
        return image
    return image.convert("RGBA" if "A" in image.getbands() else "RGB")


def convert_to_png(src: Path, dst: Path, args: Options) -> str:
    if src.suffix.lower() in SVG_EXTS:
        box = svg_box(src.read_bytes())
        width, height = svg_pixel_size(box)
        rendered, backend = render_svg(src, width, height)
        if rendered.size != (width, height):
            raise ValueError(f"{backend} returned {rendered.size[0]}x{rendered.size[1]}, "
                             f"expected {width}x{height}")
        png_ready(rendered).save(dst, "PNG", **PNG_OPTIONS)
        return f"{width}x{height} (svg via {backend})"

    with Image.open(src) as image:
        fmt = (image.format or "").upper()
        animated = getattr(image, "n_frames", 1) > 1 and fmt in ("GIF", "WEBP", "PNG")

        if not animated:
            oriented = ImageOps.exif_transpose(image)
            if oriented is not None:
                image = oriented
            width, height = image.size
            png_ready(image).save(dst, "PNG", **PNG_OPTIONS)
            return f"{width}x{height}"

        frames, durations = [], []
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(image.info.get("duration", 0))
            frames.append(image.convert("RGBA"))

        width, height = image.size
        frames[0].save(dst, "PNG", **PNG_OPTIONS, save_all=True, append_images=frames[1:],
                       loop=image.info.get("loop", 0), disposal=2,
                       duration=max(10, round((sum(durations) or 100) / len(frames))))
        return f"{width}x{height} ({len(frames)} frames)"