import tempfile
from pathlib import Path

from PIL import Image

from pdf_pipeline.renderer import render_thumbnails


def extract_pdf_visual_features(pdf_path):
    with tempfile.TemporaryDirectory(prefix="mech_visual_") as temp_dir:
        thumbnails = render_thumbnails(pdf_path, Path(temp_dir), dpi=55)
        return {item["page"]: image_features(item["path"]) for item in thumbnails}


def image_features(image_path):
    colour_image = Image.open(image_path).convert("RGB")
    colour_image.thumbnail((900, 900))
    photo_like_score = photo_score(colour_image)

    image = colour_image.convert("L")
    pixels = image.load()
    width, height = image.size

    dark = dark_grid(pixels, width, height)
    ink = sum(sum(row) for row in dark)
    total = width * height
    ink_density = ink / total if total else 0

    horizontal_runs = long_runs_by_row(dark, width, height)
    vertical_runs = long_runs_by_column(dark, width, height)
    drawing_balance = min(horizontal_runs, vertical_runs) / max(horizontal_runs, vertical_runs, 1)
    central_density = density_in_box(dark, width, height, 0.08, 0.12, 0.92, 0.88)
    title_block_density = density_in_box(dark, width, height, 0.55, 0.78, 0.98, 0.98)

    line_score = min(1.0, (horizontal_runs + vertical_runs) / 70)
    balance_score = min(1.0, drawing_balance * 2.2)
    drawing_density_score = clamp((central_density - 0.012) / 0.14)
    cover_like_score = cover_score(ink_density, horizontal_runs, vertical_runs, title_block_density)
    side_view_score = side_score(horizontal_runs, vertical_runs, drawing_balance, title_block_density)
    top_down_score = clamp(
        line_score * 0.36
        + balance_score * 0.24
        + drawing_density_score * 0.28
        + min(1.0, title_block_density / 0.08) * 0.12
        - cover_like_score * 0.35
        - photo_like_score * 0.45
    )
    plan_confidence = clamp(top_down_score - photo_like_score * 0.55 - cover_like_score * 0.25)

    return {
        "top_down_score": round(top_down_score, 2),
        "side_view_score": round(side_view_score, 2),
        "cover_like_score": round(cover_like_score, 2),
        "photo_like_score": round(photo_like_score, 2),
        "plan_confidence": round(plan_confidence, 2),
        "line_score": round(line_score, 2),
        "horizontal_line_runs": horizontal_runs,
        "vertical_line_runs": vertical_runs,
        "ink_density": round(ink_density, 4),
        "title_block_density": round(title_block_density, 4),
        "likely_view": likely_view(plan_confidence, side_view_score, cover_like_score, photo_like_score),
    }


def dark_grid(pixels, width, height):
    return [[pixels[x, y] < 215 for x in range(width)] for y in range(height)]


def long_runs_by_row(dark, width, height):
    minimum = max(24, int(width * 0.10))
    return sum(1 for y in range(height) if longest_run(dark[y]) >= minimum)


def long_runs_by_column(dark, width, height):
    minimum = max(24, int(height * 0.10))
    count = 0
    for x in range(width):
        run = 0
        longest = 0
        for y in range(height):
            if dark[y][x]:
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        if longest >= minimum:
            count += 1
    return count


def longest_run(values):
    run = 0
    longest = 0
    for value in values:
        if value:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def density_in_box(dark, width, height, left, top, right, bottom):
    x0 = int(width * left)
    y0 = int(height * top)
    x1 = max(x0 + 1, int(width * right))
    y1 = max(y0 + 1, int(height * bottom))
    ink = 0
    for y in range(y0, y1):
        ink += sum(dark[y][x0:x1])
    return ink / ((x1 - x0) * (y1 - y0))


def cover_score(ink_density, horizontal_runs, vertical_runs, title_block_density):
    few_long_lines = horizontal_runs < 10 and vertical_runs < 10
    sparse = ink_density < 0.055
    no_title_block = title_block_density < 0.018
    return clamp((0.42 if few_long_lines else 0) + (0.36 if sparse else 0) + (0.22 if no_title_block else 0))


def side_score(horizontal_runs, vertical_runs, drawing_balance, title_block_density):
    one_direction_heavy = drawing_balance < 0.25 and max(horizontal_runs, vertical_runs) > 18
    weak_sheet = title_block_density < 0.018
    return clamp((0.65 if one_direction_heavy else 0) + (0.2 if weak_sheet else 0))


def photo_score(image):
    pixels = image.load()
    width, height = image.size
    total = width * height
    if not total:
        return 0

    sampled = 0
    midtone = 0
    colourful = 0
    step = max(1, int(max(width, height) / 420))
    for y in range(0, height, step):
        for x in range(0, width, step):
            red, green, blue = pixels[x, y]
            sampled += 1
            brightness = (red + green + blue) / 3
            if 35 < brightness < 225:
                midtone += 1
            if max(red, green, blue) - min(red, green, blue) > 28:
                colourful += 1

    midtone_ratio = midtone / sampled
    colourful_ratio = colourful / sampled
    return clamp(midtone_ratio * 0.75 + colourful_ratio * 0.45)


def likely_view(plan_confidence, side_view_score, cover_like_score, photo_like_score):
    if photo_like_score >= 0.34 and plan_confidence < 0.55:
        return "render_or_photo"
    if cover_like_score >= 0.72 and plan_confidence < 0.35:
        return "cover_or_text"
    if plan_confidence >= 0.72 and plan_confidence >= side_view_score:
        return "top_down_plan"
    if side_view_score >= 0.55 and plan_confidence < 0.45:
        return "side_or_detail"
    return "uncertain"


def clamp(value):
    return max(0.0, min(1.0, value))
