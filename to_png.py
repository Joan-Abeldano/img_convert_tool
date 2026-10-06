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
    pad_frame,
    svg_box,
)

PNG_OPTIONS = {"optimize": True}


def svg_pixel_size(box: SvgBox) -> tuple[int, int]:
    side = max(box.width, box.height)
    if side > MAX_SIDE:
        raise ValueError(f"resulting side {fmt_num(side)} exceeds {MAX_SIDE}")
    return max(1, math.floor(box.width + 0.5)), max(1, math.floor(box.height + 0.5))


def svg_renderers(width: int, height: int) -> list[list[str]]:
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
    return commands


def render_svg(src: Path, width: int, height: int) -> Image.Image:
    problems: list[str] = []

    try:
        import cairosvg
    except ImportError:
        problems.append("cairosvg not installed (pip install cairosvg)")
    else:
        try:
            data = cairosvg.svg2png(url=str(src), output_width=width, output_height=height)
            return Image.open(BytesIO(data)).convert("RGBA")
        except Exception as error:
            problems.append(f"cairosvg: {error}")

    for command in svg_renderers(width, height):
        try:
            result = subprocess.run([*command, str(src)], capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as error:
            problems.append(f"{command[0]}: {error}")
            continue
        if result.returncode != 0 or not result.stdout:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
            problems.append(f"{command[0]}: {detail[-1] if detail else result.returncode}")
            continue
        return Image.open(BytesIO(result.stdout)).convert("RGBA")

    raise RuntimeError(
        "no SVG renderer could handle this file: " + "; ".join(problems or ["none installed"])
    )


def convert_to_png(src: Path, dst: Path, args: Options) -> str:
    if src.suffix.lower() in SVG_EXTS:
        box = svg_box(src.read_bytes())
        width, height = svg_pixel_size(box)
        rendered = render_svg(src, width, height)
        if rendered.size != (width, height):
            raise ValueError(f"renderer returned {rendered.size[0]}x{rendered.size[1]}, "
                             f"expected {width}x{height}")
        canvas = pad_frame(rendered, True, args.anchor, args.bg)
        canvas.save(dst, "PNG", **PNG_OPTIONS)
        return (f"{fmt_num(box.width)}x{fmt_num(box.height)} -> "
                f"{canvas.size[0]}x{canvas.size[1]} (svg rendered)")

    with Image.open(src) as image:
        fmt = (image.format or "").upper()
        animated = getattr(image, "n_frames", 1) > 1 and fmt in ("GIF", "WEBP", "PNG")

        if not animated:
            oriented = ImageOps.exif_transpose(image)
            if oriented is not None:
                image = oriented
            width, height = image.size
            canvas = pad_frame(image, True, args.anchor, args.bg)
            canvas.save(dst, "PNG", **PNG_OPTIONS)
            if (width, height) == canvas.size and not args.force:
                return f"{width}x{height} -> {canvas.size[0]}x{canvas.size[1]} (already 1:1)"
            return f"{width}x{height} -> {canvas.size[0]}x{canvas.size[1]}"

        frames, durations = [], []
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(image.info.get("duration", 0))
            frames.append(pad_frame(image.convert("RGBA"), True, args.anchor, args.bg))

        width, height = image.size
        frames[0].save(dst, "PNG", **PNG_OPTIONS, save_all=True, append_images=frames[1:],
                       loop=image.info.get("loop", 0), disposal=2,
                       duration=max(10, round((sum(durations) or 100) / len(frames))))
        side = frames[0].size[0]
        return f"{width}x{height} -> {side}x{side} ({len(frames)} frames)"