import re
import subprocess
from pathlib import Path


def render_thumbnails(pdf_path, output_dir=None, dpi=45, page_numbers=None):
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir or Path("output") / "thumbnails" / pdf_path.stem)
    output_dir.mkdir(parents=True, exist_ok=True)
    clear_thumbnails(output_dir)

    if page_numbers is not None:
        return render_selected_pages(pdf_path, output_dir, dpi, page_numbers)

    prefix = output_dir / "page"
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)],
        check=True,
        capture_output=True,
        text=True,
    )

    thumbnails = []
    for path in sorted(output_dir.glob("page-*.png"), key=page_number_from_path):
        page_number = page_number_from_path(path)
        clean_path = output_dir / f"page_{page_number:03d}.png"
        if path != clean_path:
            path.replace(clean_path)
        thumbnails.append({"page": page_number, "path": str(clean_path)})

    return thumbnails


def clear_thumbnails(output_dir):
    for path in output_dir.glob("page*.png"):
        path.unlink()


def render_selected_pages(pdf_path, output_dir, dpi, page_numbers):
    thumbnails = []
    for page_number in sorted(set(page_numbers)):
        prefix = output_dir / f"page_{page_number:03d}"
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-singlefile",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-r",
                str(dpi),
                str(pdf_path),
                str(prefix),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        thumbnails.append({"page": page_number, "path": str(prefix.with_suffix(".png"))})
    return thumbnails


def page_number_from_path(path):
    match = re.search(r"(?:page-|page_)(\d+)", Path(path).stem)
    if not match:
        return 0
    return int(match.group(1))
