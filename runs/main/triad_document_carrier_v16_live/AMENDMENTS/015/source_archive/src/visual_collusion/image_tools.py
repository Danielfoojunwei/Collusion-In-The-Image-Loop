"""Local diagnostic tools. Diagnostics are private views, never peer deliveries.

Every delivered image must come from Qwen's generation API. These deterministic
inspection tools cannot write a substitute outgoing image or read arbitrary files.
"""
from __future__ import annotations
import io
from PIL import Image, ImageEnhance, ImageStat


def inspect_image(data: bytes, inspection: dict):
    operation = inspection["operation"]
    box = inspection["box"]
    with Image.open(io.BytesIO(data)) as source:
        rgb = source.convert("RGB")
    if len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError("Inspection box is four integer pixel coordinates")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= rgb.width and 0 <= y1 < y2 <= rgb.height):
        raise ValueError("Inspection box must lie inside the visible image")
    region = rgb.crop(tuple(box))
    stats = ImageStat.Stat(region)
    summary = {"operation": operation, "box": box, "mean_rgb": stats.mean,
               "min_max_rgb": [list(v) for v in stats.extrema], "width": region.width, "height": region.height}
    if operation == "pixel_stats":
        summary["sample_grid_rgb"] = [list(region.getpixel((min(region.width-1, (i*region.width)//5), min(region.height-1, (j*region.height)//5))))
                                      for j in range(5) for i in range(5)]
        return summary, None
    if operation == "full":
        region = rgb
    elif operation == "contrast":
        region = ImageEnhance.Contrast(region).enhance(4.0)
    elif operation != "crop":
        raise ValueError("Unknown inspection operation")
    # A crop is magnified for visual inspection; the original remains untouched.
    region.thumbnail((1024, 1024))
    if operation in {"crop", "contrast"}:
        region = region.resize((1024, 1024), Image.Resampling.NEAREST)
    output = io.BytesIO()
    region.save(output, format="PNG")
    return summary, output.getvalue()
