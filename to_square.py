from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageOps, JpegImagePlugin

RASTER_EXTS = {
    ".png", ".webp", ".jpg", ".jpeg", ".jpe", ".jfif",
    ".gif", ".bmp", ".tif", ".tiff", ".avif", ".ico", ".ppm", ".pgm", ".tga",
}
SVG_EXTS = {".svg"}
SUPPORTED = RASTER_EXTS | SVG_EXTS

ALPHA_FORMATS = {".png", ".webp", ".tif", ".tiff", ".gif", ".avif", ".ppm", ".pgm"}

MAX_SIDE = 16384


@dataclass
class Options:
    anchor: str = "center"
    bg: tuple[int, int, int] | None = None
    force: bool = False
    lossy: bool = False


def fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


def leading_number(text: str) -> float | None:
    match = re.match(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)", text)
    return float(match.group(1)) if match else None


def split_length(text: str) -> tuple[float | None, str]:
    match = re.match(
        r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z%]*)\s*$", text
    )
    if not match:
        return None, ""
    return float(match.group(1)), match.group(2)


def anchor_offsets(size: tuple[int, int], side: int, anchor: str):
    width, height = size
    if anchor == "top-left":
        return 0, 0
    if anchor == "top":
        return (side - width) // 2, 0
    if anchor == "bottom-left":
        return 0, side - height
    if anchor == "bottom":
        return (side - width) // 2, side - height
    return (side - width) // 2, (side - height) // 2


def target_mode(image: Image.Image, transparent: bool) -> str:
    if image.mode in ("RGBA", "LA"):
        return image.mode
    if image.mode in ("1", "I", "F", "I;16", "I;16B", "I;16L", "I;16N"):
        return "L"
    if transparent:
        return "RGBA" if image.mode != "LA" else "LA"
    if image.mode in ("RGB", "L", "CMYK"):
        return image.mode
    return "RGB"


def fill_for(mode: str, transparent: bool, color: tuple[int, int, int] | None):
    if color is None:
        if mode in ("RGBA", "PA"):
            return (0, 0, 0, 0)
        if mode == "LA":
            return (0, 0)
        if mode == "L":
            return 255
        if mode == "CMYK":
            return (0, 0, 0, 0)
        return None
    if mode == "L":
        return round(0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2])
    if mode == "CMYK":
        return Image.new("RGB", (1, 1), color).convert("CMYK").getpixel((0, 0))
    if mode == "LA":
        return (*color, 255)
    return (*color, 255) if mode in ("RGBA", "PA") else color


def pad_frame(frame: Image.Image, transparent: bool, anchor: str,
              color: tuple[int, int, int] | None = None) -> Image.Image:
    width, height = frame.size
    side = max(width, height)
    mode = target_mode(frame, transparent)
    if frame.mode != mode:
        frame = frame.convert(mode)

    if color is None and mode in ("RGB", "CMYK"):
        color = (255, 255, 255)
    fill = fill_for(mode, transparent, color)
    if fill is None:
        mode = "RGBA"
        if frame.mode != mode:
            frame = frame.convert(mode)
        fill = (255, 255, 255, 255)

    left, top = anchor_offsets((width, height), side, anchor)
    canvas = Image.new(mode, (side, side), fill)
    canvas.paste(frame, (left, top))
    return canvas


def save_options(fmt: str, lossy: bool, source: Image.Image | None = None) -> dict:
    options: dict = {}
    if fmt == "PNG":
        options = {"optimize": True}
    elif fmt in ("JPEG", "MPO"):
        options = {"quality": 95, "progressive": True}
        if source is not None and (source.format or "").upper() in ("JPEG", "MPO"):
            sampling = JpegImagePlugin.get_sampling(source)
            if sampling != -1:
                options["subsampling"] = sampling
            tables = source.quantization
            layers = getattr(source, "layers", len(tables))
            if tables and len(tables) == layers:
                options["qtables"] = tables
    elif fmt == "WEBP":
        options = {"method": 6, "lossless": not lossy, "exact": True}
    elif fmt == "AVIF":
        options = {"lossless": not lossy, "quality": 95}
    elif fmt in ("TIFF", "TIF"):
        options = {}
    return options


def pad_raster(src: Path, dst: Path | None, args: Options) -> str:
    with Image.open(src) as image:
        fmt = (image.format or "").upper()
        transparent = args.bg is None and src.suffix.lower() in ALPHA_FORMATS
        animated = getattr(image, "n_frames", 1) > 1 and fmt in ("GIF", "WEBP", "PNG")

        if not animated:
            oriented = ImageOps.exif_transpose(image)
            image = oriented if oriented is not None else image
            width, height = image.size
            side = max(width, height)
            if (width, height) == (side, side) and not args.force:
                return "already square"
            padded = pad_frame(image, transparent, args.anchor, args.bg)
            if dst is not None:
                options = save_options(fmt, args.lossy, image)
                if fmt in ("JPEG", "MPO") and padded.mode not in ("L", "RGB", "CMYK"):
                    padded = padded.convert("RGB")
                    exif = image.info.get("exif") or b""
                    if exif:
                        options["exif"] = exif
                padded.save(dst, fmt, **options)
            return f"{width}x{height} -> {side}x{side}"

        frames, durations = [], []
        for index in range(image.n_frames):
            image.seek(index)
            duration = image.info.get("duration", 0)
            frames.append(pad_frame(image.convert("RGBA"), True, args.anchor, args.bg))
            durations.append(duration)

        first = frames[0]
        width, height = image.size
        side = first.size[0]
        if (width, height) == (side, side) and not args.force and len(frames) == 1:
            return "already square"

        if dst is not None:
            options = save_options(fmt, args.lossy, first)
            options.update({
                "save_all": True,
                "append_images": frames[1:],
                "loop": image.info.get("loop", 0),
                "disposal": 2,
                "duration": max(10, round((sum(durations) or 100) / len(frames))),
            })
            first.save(dst, fmt, **options)
        suffix_note = f" ({len(frames)} frames)" if len(frames) > 1 else ""
        return f"{width}x{height} -> {side}x{side}{suffix_note}"


ATTR_RE = re.compile(rb'([A-Za-z_:][-\w.:]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


class SvgBox(NamedTuple):
    start: int
    end: int
    attrs: dict[bytes, tuple[int, int]]
    x: float
    y: float
    width: float
    height: float
    width_text: str | None
    height_text: str | None
    width_value: float | None
    height_value: float | None
    width_relative: bool
    height_relative: bool


def svg_open_tag(data: bytes) -> tuple[int, int, dict[bytes, tuple[int, int]]]:
    start = re.search(rb"<svg(?=[\s/>])", data)
    if not start:
        raise ValueError("no <svg> root element found")

    index, quote = start.end(), None
    while index < len(data):
        char = data[index : index + 1]
        if quote:
            if char == quote:
                quote = None
        elif char in (b'"', b"'"):
            quote = char
        elif char == b">":
            break
        index += 1
    else:
        raise ValueError("unterminated <svg> tag")

    attrs = {}
    for match in ATTR_RE.finditer(data, start.end(), index):
        name = match.group(1)
        attrs[name] = (match.start(), match.end())
    return start.start(), index, attrs


def svg_box(data: bytes) -> SvgBox:
    start, end, attrs = svg_open_tag(data)
    body = data[start : end + 1]

    def value_of(name: bytes) -> str | None:
        span = attrs.get(name)
        if not span:
            return None
        raw = body[span[0] - start : span[1] - start]
        found = ATTR_RE.search(raw)
        return (found.group(2) or found.group(3)).decode("utf-8", "replace") if found else None

    width_text = value_of(b"width")
    height_text = value_of(b"height")
    viewbox_text = value_of(b"viewBox")

    if viewbox_text:
        parts = re.split(r"[\s,]+", viewbox_text.strip())
        if len(parts) != 4:
            raise ValueError(f"unusable viewBox: {viewbox_text!r}")
        vx, vy, vw, vh = (float(part) for part in parts)
        if vw <= 0 or vh <= 0:
            raise ValueError("viewBox has non-positive size")
    else:
        if width_text is None or height_text is None:
            raise ValueError("no viewBox and no width/height to measure")
        if "%" in width_text + height_text:
            raise ValueError("relative width/height without viewBox")
        vw, vh = leading_number(width_text), leading_number(height_text)
        if not vw or not vh:
            raise ValueError(f"unusable width/height: {width_text!r} x {height_text!r}")
        vx = vy = 0.0

    width_value, width_unit = split_length(width_text) if width_text else (None, "")
    height_value, height_unit = split_length(height_text) if height_text else (None, "")

    return SvgBox(
        start=start,
        end=end,
        attrs=attrs,
        x=vx,
        y=vy,
        width=vw,
        height=vh,
        width_text=width_text,
        height_text=height_text,
        width_value=width_value,
        height_value=height_value,
        width_relative=width_unit == "%",
        height_relative=height_unit == "%",
    )


def replace_attr(data: bytes, tag: tuple[int, int], attrs, name: bytes, value: bytes) -> bytes:
    span = attrs.get(name)
    if span:
        return data[: span[0]] + b' ' + name + b'="' + value + b'"' + data[span[1] :]
    return data[: tag[1]] + b" " + name + b'="' + value + b'"' + data[tag[1] :]


def pad_svg(src: Path, dst: Path | None, force: bool) -> str:
    data = src.read_bytes()
    box = svg_box(data)

    if box.width_relative or box.height_relative:
        already = abs(box.width - box.height) < 1e-6
        before = (box.width, box.height)
    elif box.width_value and box.height_value:
        already = abs(box.width_value - box.height_value) < 1e-6 * max(box.width_value, box.height_value)
        before = (box.width_value, box.height_value)
    else:
        already = abs(box.width - box.height) < 1e-6
        before = (box.width, box.height)
    if already and not force:
        return "already square"

    side = max(box.width, box.height)
    if side > MAX_SIDE:
        raise ValueError(f"resulting side {fmt_num(side)} exceeds {MAX_SIDE}")

    new_viewbox = (
        f"{fmt_num(box.x + (box.width - side) / 2)} {fmt_num(box.y + (box.height - side) / 2)} "
        f"{fmt_num(side)} {fmt_num(side)}"
    ).encode()

    if box.width_relative or not box.width_value:
        new_width = box.width_text.encode() if box.width_text else fmt_num(side).encode()
    else:
        new_width = (fmt_num(side * (box.width_value / box.width)) + split_length(box.width_text)[1]).encode()

    if box.height_relative or not box.height_value:
        new_height = box.height_text.encode() if box.height_text else fmt_num(side).encode()
    else:
        unit = split_length(box.height_text)[1]
        new_height = (fmt_num(side * (box.height_value / box.height)) + unit).encode()

    out = data
    for name, value in ((b"viewBox", new_viewbox), (b"width", new_width), (b"height", new_height)):
        start, end, current = svg_open_tag(out)
        out = replace_attr(out, (start, end), current, name, value)

    if out == data:
        return "unchanged"

    if dst is not None:
        dst.write_bytes(out)

    if box.width_relative or not box.width_value:
        pixel_side = side
    else:
        pixel_side = side * (box.width_value / box.width)
    return (f"{fmt_num(before[0])}x{fmt_num(before[1])} -> "
            f"{fmt_num(pixel_side)}x{fmt_num(side)}")


def square_file(src: Path, dst: Path | None, args: Options) -> str:
    if src.suffix.lower() in SVG_EXTS:
        return pad_svg(src, dst, force=args.force)
    return pad_raster(src, dst, args)