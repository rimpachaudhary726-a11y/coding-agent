"""
vision_tools.py — gives the agent eyes: browser control, screenshot cropping,
and visual self-verification against a design mockup.

Requires:
    pip install playwright pillow numpy
    playwright install chromium
"""

import base64
import io
import os
import time

from tools import write_file

SCREENSHOT_DIR = ".agent_screenshots"


def _ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Desktop GUI / Browser tool
# ---------------------------------------------------------------------------

_browser_state = {"playwright": None, "browser": None, "page": None}


def _get_page():
    """Lazily launch a persistent headless browser + page."""
    if _browser_state["page"] is not None:
        return _browser_state["page"]

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    _browser_state["playwright"] = pw
    _browser_state["browser"] = browser
    _browser_state["page"] = page
    return page


def browser_navigate(url):
    """Open a URL in the persistent browser session."""
    try:
        page = _get_page()
        page.goto(url, timeout=20000, wait_until="load")
        return f"Navigated to {url}. Page title: {page.title()}"
    except Exception as e:
        return f"ERROR: could not navigate to {url}: {e}"


def browser_click(selector):
    """Click an element matched by a CSS selector."""
    try:
        page = _get_page()
        page.click(selector, timeout=10000)
        return f"Clicked '{selector}'."
    except Exception as e:
        return f"ERROR: could not click '{selector}': {e}"


def browser_type(selector, text, submit=False):
    """Type text into an input/textarea matched by a CSS selector."""
    try:
        page = _get_page()
        page.fill(selector, text, timeout=10000)
        if submit:
            page.press(selector, "Enter")
        return f"Typed into '{selector}'{' and submitted' if submit else ''}."
    except Exception as e:
        return f"ERROR: could not type into '{selector}': {e}"


def browser_screenshot(label="screenshot", full_page=True):
    """
    Take a screenshot of the current page state.
    Returns the saved file path (also viewable by the agent's vision).
    """
    try:
        _ensure_dir()
        page = _get_page()
        path = os.path.join(SCREENSHOT_DIR, f"{label}_{int(time.time())}.png")
        page.screenshot(path=path, full_page=full_page)
        return f"Screenshot saved to {path}"
    except Exception as e:
        return f"ERROR: could not take screenshot: {e}"


def browser_close():
    """Close the browser session and free resources."""
    try:
        if _browser_state["browser"]:
            _browser_state["browser"].close()
        if _browser_state["playwright"]:
            _browser_state["playwright"].stop()
        _browser_state.update({"playwright": None, "browser": None, "page": None})
        return "Browser session closed."
    except Exception as e:
        return f"ERROR closing browser: {e}"


# ---------------------------------------------------------------------------
# 2. Screen-crop tool — zoom into a noisy/blurry screenshot region
# ---------------------------------------------------------------------------

def screen_crop(image_path, left, top, right, bottom, zoom=2, label="crop"):
    """
    Crop a region out of a screenshot and upscale it so small text/buttons
    become legible. Coordinates are pixels in the original image.
    """
    if not os.path.exists(image_path):
        return f"ERROR: {image_path} does not exist."
    try:
        from PIL import Image
    except ImportError:
        return "ERROR: Pillow is not installed. Run: pip install pillow"

    try:
        _ensure_dir()
        img = Image.open(image_path)
        box = (left, top, right, bottom)
        cropped = img.crop(box)
        w, h = cropped.size
        if w <= 0 or h <= 0:
            return f"ERROR: crop box {box} produced an empty region."
        cropped = cropped.resize((w * zoom, h * zoom), Image.LANCZOS)
        out_path = os.path.join(SCREENSHOT_DIR, f"{label}_{int(time.time())}.png")
        cropped.save(out_path)
        return f"Cropped region {box} from {image_path}, zoomed {zoom}x, saved to {out_path}"
    except Exception as e:
        return f"ERROR: crop failed: {e}"


# ---------------------------------------------------------------------------
# 3. Visual self-verification — compare rendered output vs a design mockup
# ---------------------------------------------------------------------------

def visual_self_verify(rendered_path, mockup_path, diff_threshold=30, label="diff"):
    """
    Compare a screenshot of newly-built UI against a reference mockup image.
    Produces a diff-highlight image and a plain-text summary of how different
    they are, so the agent can decide whether to keep iterating.
    """
    if not os.path.exists(rendered_path):
        return f"ERROR: {rendered_path} does not exist."
    if not os.path.exists(mockup_path):
        return f"ERROR: {mockup_path} does not exist."

    try:
        from PIL import Image, ImageChops
        import numpy as np
    except ImportError:
        return "ERROR: Pillow and numpy are required. Run: pip install pillow numpy"

    try:
        _ensure_dir()
        rendered = Image.open(rendered_path).convert("RGB")
        mockup = Image.open(mockup_path).convert("RGB")

        if rendered.size != mockup.size:
            rendered = rendered.resize(mockup.size, Image.LANCZOS)

        diff = ImageChops.difference(rendered, mockup)
        diff_array = np.array(diff)
        gray_diff = diff_array.mean(axis=2)

        changed_pixels = int((gray_diff > diff_threshold).sum())
        total_pixels = gray_diff.shape[0] * gray_diff.shape[1]
        pct_changed = round(100 * changed_pixels / total_pixels, 2)

        # Highlight overlay: red where the difference exceeds the threshold
        highlight = np.array(rendered).copy()
        mask = gray_diff > diff_threshold
        highlight[mask] = [255, 0, 0]
        out_path = os.path.join(SCREENSHOT_DIR, f"{label}_{int(time.time())}.png")
        Image.fromarray(highlight).save(out_path)

        verdict = (
            "Close match — likely fine." if pct_changed < 2 else
            "Minor differences — spacing/color tweaks may be needed." if pct_changed < 10 else
            "Significant differences — layout or content likely wrong."
        )

        return (
            f"Compared {rendered_path} vs {mockup_path}: {pct_changed}% of pixels differ "
            f"beyond threshold={diff_threshold}. {verdict} "
            f"Diff-highlight image saved to {out_path}"
        )
    except Exception as e:
        return f"ERROR: comparison failed: {e}"


# ---------------------------------------------------------------------------
# Tool schema / registration
# ---------------------------------------------------------------------------

VISION_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "browser_navigate",
        "description": "Open a URL in a headless browser session, like a human tester would.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "Click an element in the current browser page via a CSS selector.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"},
        }, "required": ["selector"]},
    }},
    {"type": "function", "function": {
        "name": "browser_type",
        "description": "Type text into an input/textarea in the current browser page.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "submit": {"type": "boolean", "default": False},
        }, "required": ["selector", "text"]},
    }},
    {"type": "function", "function": {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current browser page state for visual inspection.",
        "parameters": {"type": "object", "properties": {
            "label": {"type": "string", "default": "screenshot"},
            "full_page": {"type": "boolean", "default": True},
        }},
    }},
    {"type": "function", "function": {
        "name": "browser_close",
        "description": "Close the current headless browser session.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "screen_crop",
        "description": "Crop and zoom into a region of a screenshot when it is too blurry, "
                        "cluttered, or small to read a specific button or error message clearly.",
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string"},
            "left": {"type": "integer"},
            "top": {"type": "integer"},
            "right": {"type": "integer"},
            "bottom": {"type": "integer"},
            "zoom": {"type": "integer", "default": 2},
            "label": {"type": "string", "default": "crop"},
        }, "required": ["image_path", "left", "top", "right", "bottom"]},
    }},
    {"type": "function", "function": {
        "name": "visual_self_verify",
        "description": "Compare a screenshot of just-built UI against a design mockup image to "
                        "check colors, spacing, and layout match before declaring frontend work done.",
        "parameters": {"type": "object", "properties": {
            "rendered_path": {"type": "string"},
            "mockup_path": {"type": "string"},
            "diff_threshold": {"type": "integer", "default": 30},
            "label": {"type": "string", "default": "diff"},
        }, "required": ["rendered_path", "mockup_path"]},
    }},
]

VISION_TOOL_FUNCTIONS = {
    "browser_navigate": browser_navigate,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_screenshot": browser_screenshot,
    "browser_close": browser_close,
    "screen_crop": screen_crop,
    "visual_self_verify": visual_self_verify,
}
