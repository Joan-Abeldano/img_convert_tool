from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from to_square import (
    MAX_SIDE,
    SVG_EXTS,
    fmt_num,
    output_format,
    output_suffix,
    replace_attr,
    save_options,
    split_length,
    svg_box,
    svg_open_tag,
    target_mode,
    fill_for,
)

PORTRAIT_RATIO = 120 / 138


@dataclass
class Options:
    width: int = 120
    height: int = 138
    bg: tuple[int, int, int] | None = None
    force: bool = False
    lossy: bool = False


def portrait_dims(width: float, height: float, ratio: float) -> tuple[float, float]:
    if width / height > ratio:
        return width, width / ratio
    return height * ratio, height


def portrait_target(out: Path, src: Path, args: Options, taken: set[Path]) -> Path:
    suffix = output_suffix(src.suffix, args.bg is None)
    if suffix == src.suffix.lower():
        dst = out / src.name
        taken.add(dst)
        return dst

    dst = out / f"{src.stem}{suffix}"
    index = 2
    while dst in taken:
        dst = out / f"{src.stem}_{index}{suffix}"
        index += 1
    taken.add(dst)
    return dst


def pad_frame(image: Image.Image, transparent: bool, args: Options) -> Image.Image:
    width, height = image.size
    canvas_w, canvas_h = portrait_dims(width, height, args.width / args.height)
    canvas_w, canvas_h = int(round(canvas_w)), int(round(canvas_h))

    mode = target_mode(image, transparent)
    if image.mode != mode:
        image = image.convert(mode)

    if args.bg is None and mode in ("RGB", "CMYK"):
        bg = (255, 255, 255)
    else:
        bg = args.bg
    fill = fill_for(mode, transparent, bg)
    if fill is None:
        mode = "RGBA"
        if image.mode != mode:
            image = image.convert(mode)
        fill = (255, 255, 255, 255)

    paste_x, paste_y = (canvas_w - width) // 2, (canvas_h - height) // 2
    canvas = Image.new(mode, (canvas_w, canvas_h), fill)
    canvas.paste(image, (paste_x, paste_y))
    return canvas


def pad_raster(src: Path, dst: Path | None, args: Options) -> str:
    transparent = args.bg is None
    out_fmt = output_format(src.suffix, transparent)
    with Image.open(src) as image:
        fmt = (image.format or "").upper()
        animated = getattr(image, "n_frames", 1) > 1 and fmt in ("GIF", "WEBP", "PNG")

        if not animated:
            oriented = ImageOps.exif_transpose(image)
            image = oriented if oriented is not None else image
            width, height = image.size
            canvas_w, canvas_h = portrait_dims(width, height, args.width / args.height)
            canvas_w, canvas_h = int(round(canvas_w)), int(round(canvas_h))

            already = (canvas_w, canvas_h) == (width, height)
            if already and not args.force and out_fmt == fmt:
                return "already 120:138"

            padded = pad_frame(image, transparent, args)
            if dst is not None:
                options = save_options(out_fmt, args.lossy, image)
                if out_fmt == "JPEG" and padded.mode not in ("L", "RGB", "CMYK"):
                    padded = padded.convert("RGB")
                    exif = image.info.get("exif") or b""
                    if exif:
                        options["exif"] = exif
                padded.save(dst, out_fmt, **options)
            note = f"{width}x{height} -> {canvas_w}x{canvas_h}"
            if (canvas_w, canvas_h) == (width, height):
                note = f"{width}x{height} (already 120:138)"
            return note if out_fmt == fmt else f"{note}, {fmt.lower()} -> {out_fmt.lower()}"

        frames, durations = [], []
        for index in range(image.n_frames):
            image.seek(index)
            duration = image.info.get("duration", 0)
            frames.append(pad_frame(image.convert("RGBA"), True, args))
            durations.append(duration)

        first = frames[0]
        width, height = image.size
        canvas_w, canvas_h = first.size
        if (canvas_w, canvas_h) == (width, height) and not args.force and len(frames) == 1:
            return "already 120:138"

        if dst is not None:
            options = save_options(out_fmt, args.lossy, first)
            options.update({
                "save_all": True,
                "append_images": frames[1:],
                "loop": image.info.get("loop", 0),
                "disposal": 2,
                "duration": max(10, round((sum(durations) or 100) / len(frames))),
            })
            first.save(dst, out_fmt, **options)
        suffix_note = f" ({len(frames)} frames)" if len(frames) > 1 else ""
        return f"{width}x{height} -> {canvas_w}x{canvas_h}{suffix_note}"


def pad_svg(src: Path, dst: Path | None, args: Options) -> str:
    data = src.read_bytes()
    box = svg_box(data)
    width, height = box.width, box.height
    canvas_w, canvas_h = portrait_dims(width, height, args.width / args.height)

    already = abs(canvas_w - width) < 1e-6 and abs(canvas_h - height) < 1e-6
    if already and not args.force:
        return "already 120:138"

    if max(canvas_w, canvas_h) > MAX_SIDE:
        raise ValueError(f"resulting side {fmt_num(max(canvas_w, canvas_h))} exceeds {MAX_SIDE}")

    new_viewbox = (
        f"{fmt_num(box.x + (width - canvas_w) / 2)} "
        f"{fmt_num(box.y + (height - canvas_h) / 2)} {fmt_num(canvas_w)} {fmt_num(canvas_h)}"
    ).encode()

    if box.width_relative or not box.width_value:
        new_width = box.width_text.encode() if box.width_text else fmt_num(canvas_w).encode()
    else:
        unit = split_length(box.width_text)[1]
        new_width = (fmt_num(canvas_w * (box.width_value / box.width)) + unit).encode()

    if box.height_relative or not box.height_value:
        new_height = box.height_text.encode() if box.height_text else fmt_num(canvas_h).encode()
    else:
        unit = split_length(box.height_text)[1]
        new_height = (fmt_num(canvas_h * (box.height_value / box.height)) + unit).encode()

    out = data
    for name, value in ((b"viewBox", new_viewbox), (b"width", new_width), (b"height", new_height)):
        start, end, current = svg_open_tag(out)
        out = replace_attr(out, (start, end), current, name, value)

    if out == data:
        return "unchanged"

    if dst is not None:
        dst.write_bytes(out)

    pixel_w = canvas_w * (box.width_value / box.width) if box.width_value and not box.width_relative else canvas_w
    pixel_h = canvas_h * (box.height_value / box.height) if box.height_value and not box.height_relative else canvas_h
    return f"{fmt_num(width)}x{fmt_num(height)} -> {fmt_num(pixel_w)}x{fmt_num(pixel_h)}"


def portrait_file(src: Path, dst: Path | None, args: Options) -> str:
    if src.suffix.lower() in SVG_EXTS:
        return pad_svg(src, dst, args)
    return pad_raster(src, dst, args)