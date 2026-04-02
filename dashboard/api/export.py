"""
Ad card export — generates composite PNG images (ad image + copy text)
for downloading and sharing in Slack.

Each exported card is a 1080px-wide PNG showing:
  - The generated ad image at its native aspect ratio
  - Headline, primary text, description, and CTA below on white background

Single variant  → returns PNG bytes
Multiple variants → returns ZIP bytes containing one PNG per variant
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from engine.models import AdVariant
from engine.store import Store

# Image section height at 1080px card width, keyed by aspect ratio string
RATIO_TO_IMG_HEIGHT: dict[str, int] = {
    "1:1":  1080,
    "3:4":  1440,
    "4:5":  1350,  # legacy — replaced by 3:4
    "9:16": 1920,
}


def _img_height(aspect_ratio: str) -> int:
    return RATIO_TO_IMG_HEIGHT.get(aspect_ratio, 1080)


def _slug(text: str, max_len: int = 40) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:max_len]


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _image_data_uri(asset_path: str) -> Optional[str]:
    """Read the PNG from disk and return a base64 data URI, or None if missing."""
    p = Path(asset_path)
    if not p.exists():
        # Server runs from ads_engine_alex/; relative paths are already correct.
        # Fallback for any edge case where cwd differs.
        for base in (Path.cwd(), Path(__file__).parents[3]):
            candidate = base / asset_path
            if candidate.exists():
                p = candidate
                break
        else:
            return None
    raw = p.read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _build_card_html(variant: AdVariant, display_number: int) -> str:
    """Return a self-contained HTML string for one export card."""
    aspect_ratio = (variant.taxonomy.aspect_ratio if variant.taxonomy else "1:1")
    ih = _img_height(aspect_ratio)

    data_uri = _image_data_uri(variant.asset_path)
    if data_uri:
        img_block = (
            f'<img src="{data_uri}" '
            f'style="display:block;width:1080px;height:{ih}px;object-fit:cover;" />'
        )
    else:
        img_block = (
            f'<div style="width:1080px;height:{ih}px;background:#F8F8F7;'
            f'display:flex;align-items:center;justify-content:center;'
            f'color:#C0C0BE;font-size:20px;">Image not found</div>'
        )

    desc_block = ""
    if variant.description:
        desc_block = (
            f'<p style="font-size:18px;color:#9B9B9B;line-height:1.6;'
            f'margin:0 0 28px;font-style:italic;">{_esc(variant.description)}</p>'
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:1080px; background:#fff;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
</style>
</head>
<body>
  <div style="width:1080px;overflow:hidden;line-height:0;">{img_block}</div>

  <div style="width:1080px;background:#fff;padding:48px 56px 56px;
              border-top:3px solid #F0F0EE;">

    <div style="display:inline-flex;align-items:center;background:#1C1E85;color:#fff;
                font-size:13px;font-weight:700;letter-spacing:0.06em;
                padding:5px 14px;border-radius:20px;margin-bottom:28px;">
      #{display_number}
    </div>

    <h1 style="font-size:38px;font-weight:700;color:#1a1a1a;line-height:1.2;
               margin-bottom:20px;letter-spacing:-0.01em;">
      {_esc(variant.headline)}
    </h1>

    <p style="font-size:20px;color:#5C5C5C;line-height:1.65;
              margin-bottom:28px;white-space:pre-wrap;">
      {_esc(variant.primary_text)}
    </p>

    {desc_block}

    <div style="display:inline-block;background:#1C1E85;color:#fff;
                font-size:17px;font-weight:600;padding:15px 38px;
                border-radius:50px;letter-spacing:0.02em;">
      {_esc(variant.cta_button)}
    </div>

  </div>
</body>
</html>"""


def _screenshot_card(html: str, approx_height: int) -> bytes:
    """Screenshot the card HTML using Playwright. Returns PNG bytes."""
    from engine.generation.strategies import HtmlCssStrategy

    browser = HtmlCssStrategy._get_browser()
    page = browser.new_page(viewport={"width": 1080, "height": approx_height})
    try:
        page.set_content(html, wait_until="load")
        # Let the page measure its real height, then resize before screenshotting
        real_height = page.evaluate("() => document.body.scrollHeight")
        page.set_viewport_size({"width": 1080, "height": real_height})
        return page.screenshot(type="png", full_page=True)
    finally:
        page.close()


def run_export(
    variant_ids: list[str],
    display_map: dict[str, str],
    store: Store,
) -> tuple[bytes, str, str]:
    """
    Generate export card PNGs for the given variant IDs.

    Args:
        variant_ids:  List of variant UUIDs to export.
        display_map:  Gallery display map {"1": "uuid", ...} for badge numbers.
        store:        Store instance for loading variant records.

    Returns:
        (content_bytes, content_type, filename)
    """
    uuid_to_num: dict[str, int] = {v: int(k) for k, v in display_map.items() if k.isdigit()}

    results: list[tuple[int, bytes, str]] = []  # (display_num, png_bytes, headline)

    for vid in variant_ids:
        variant = store.get_variant(vid)
        display_num = uuid_to_num.get(vid, 0)
        aspect_ratio = (variant.taxonomy.aspect_ratio if variant.taxonomy else "1:1")
        approx_height = _img_height(aspect_ratio) + 700

        html = _build_card_html(variant, display_num)
        png_bytes = _screenshot_card(html, approx_height)
        results.append((display_num, png_bytes, variant.headline))

    results.sort(key=lambda x: x[0])

    if len(results) == 1:
        num, png_bytes, headline = results[0]
        filename = f"ad_{num}_{_slug(headline)}.png"
        return png_bytes, "image/png", filename

    # Multiple — zip them
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for num, png_bytes, headline in results:
                zf.writestr(f"ad_{num}_{_slug(headline)}.png", png_bytes)
        zip_bytes = Path(tmp_path).read_bytes()
    finally:
        os.unlink(tmp_path)

    return zip_bytes, "application/zip", "jotpsych_ads_export.zip"
