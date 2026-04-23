import streamlit as st
import os, json, requests, re, time, zipfile
import ast
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from google import genai
from google.genai import types
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from io import BytesIO
from PIL import Image
from fpdf import FPDF
import tempfile
<<<<<<< HEAD
=======

>>>>>>> 44a003b6b5c70ad7031eb9e92339c81d54fe05f3

try:
    from html_generator import generate_html_from_outline
except Exception as e:
    print(f"⚠️ Warning: HTML generator import failed: {type(e).__name__}: {e}")
    generate_html_from_outline = None

# ==========================================
# CONFIGURATION
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

# ── Dual-theme palette ──────────────────────────────────────────────
# Heading slides  →  deep blue background, gold text
HEADING_BG    = RGBColor(10,  25,  80)   # Deep navy-blue
HEADING_TEXT  = RGBColor(255, 196, 61)   # Warm gold
HEADING_SUB   = RGBColor(220, 235, 255)  # Pale blue-white for subtitles

# Content slides  →  pure white background, near-black text
CONTENT_BG    = RGBColor(255, 255, 255)  # White
CONTENT_TITLE = RGBColor(10,  25,  80)   # Deep blue for slide title on white
CONTENT_BODY  = RGBColor(30,  30,  30)   # Near-black body text
CONTENT_BULLET= RGBColor(10,  25,  80)   # Blue bullet dots
CARD_BG       = RGBColor(240, 244, 255)  # Very light blue card on white slides

# Accent
ACCENT_GOLD   = RGBColor(255, 196, 61)
ACCENT_BLUE   = RGBColor(10,  25,  80)

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)
FONT_TITLE = "Calibri"
FONT_BODY  = "Calibri"

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

# ==========================================
# GEMINI HELPERS
# ==========================================
GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

def parse_json_response(text):
    def _strip_fences(raw):
        cleaned_local = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip(), flags=re.MULTILINE)
        return re.sub(r'\n?```\s*$', '', cleaned_local.strip(), flags=re.MULTILINE)

    def _extract_first_json_array(raw):
        start = raw.find('[')
        if start == -1:
            return None
        depth, in_str, escape = 0, False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if escape: escape = False
                elif ch == '\\': escape = True
                elif ch == '"': in_str = False
                continue
            if ch == '"': in_str = True
            elif ch == '[': depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]
        return None

    def _sanitize_json_like(raw):
        s = raw
        s = s.replace('\u201c', '"').replace('\u201d', '"')
        s = s.replace('\u2018', "'").replace('\u2019', "'")
        s = re.sub(r',\s*([}\]])', r'\1', s)
        s = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', s)
        return s.strip()

    cleaned = _strip_fences(text)
    candidates = [cleaned]
    first_array = _extract_first_json_array(cleaned)
    if first_array:
        candidates.append(first_array)
    candidates.append(_sanitize_json_like(cleaned))
    if first_array:
        candidates.append(_sanitize_json_like(first_array))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue

    for candidate in candidates:
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            continue

    raise ValueError(f"Could not parse JSON from response:\n{text[:500]}")


def repair_slides_json(raw_text):
    repair_prompt = f"""You are a strict JSON repair assistant.
Fix the following malformed JSON-like content into VALID JSON.

Rules:
- Output ONLY a JSON array.
- Preserve intended slide content and order.
- Ensure each item is an object with keys: type, title, body, search_query, section_number (use null/empty where not applicable).
- Do not include markdown fences.

Malformed content:
{raw_text[:12000]}
"""
    repaired = gemini_generate(repair_prompt, response_mime='application/json')
    return parse_json_response(repaired.text)


def normalize_slides_data(slides_data, class_num, subject, chapter, use_images):
    if not isinstance(slides_data, list):
        raise ValueError("Model output is not a slide list.")

    normalized = []
    for i, slide in enumerate(slides_data):
        if not isinstance(slide, dict):
            continue
        s_type = str(slide.get('type', 'content')).strip().lower()
        if s_type not in ('title', 'section', 'content', 'diagram', 'summary'):
            s_type = 'content'

        title = str(slide.get('title') or f"Slide {i + 1}").strip()
        body = slide.get('body', [])
        if isinstance(body, str):
            body = [b.strip() for b in re.split(r'[\n\r]+', body) if b.strip()]
        elif not isinstance(body, list):
            body = []
        body = [str(b).strip() for b in body if str(b).strip()]

        search_query = str(slide.get('search_query', '') or '').strip()
        section_number = slide.get('section_number', None)

        if s_type in ('content', 'diagram', 'summary') and not body:
            body = [f"Key points about {title}"]

        if s_type == 'diagram' and not search_query:
            search_query = f"labeled diagram of {title}"

        normalized.append({
            'type': s_type,
            'title': title,
            'body': body,
            'search_query': search_query,
            'section_number': section_number,
        })

    if not normalized:
        raise ValueError("No valid slide objects were produced by the model.")

    if normalized[0]['type'] != 'title':
        normalized.insert(0, {
            'type': 'title',
            'title': chapter,
            'body': [f"Class {class_num} — {subject}"],
            'search_query': '',
            'section_number': None,
        })

    if use_images:
        min_diagrams = 12
        diag_count = sum(1 for s in normalized if s['type'] == 'diagram')
        if diag_count < min_diagrams:
            for s in normalized:
                if diag_count >= min_diagrams:
                    break
                if s['type'] == 'content':
                    s['type'] = 'diagram'
                    if not s['search_query']:
                        s['search_query'] = f"labeled diagram of {s['title']}"
                    diag_count += 1

    return normalized


def normalize_pdf_text(text, unicode_fonts):
    if text is None:
        return ""
    cleaned = str(text)
    cleaned = cleaned.replace('—', '-').replace('–', '-')
    cleaned = cleaned.replace('"', '"').replace('"', '"')
    cleaned = cleaned.replace('\u2018', "'").replace('\u2019', "'")
    cleaned = cleaned.replace('\u2022', '-').replace('\u25b8', '-').replace('\u2726', '*')
    cleaned = cleaned.replace('$', '')
    latex_map = {
        r'\\implies': '=>', r'\\sqrt': 'sqrt', r'\\circ': 'deg',
        r'\\alpha': 'alpha', r'\\beta': 'beta', r'\\theta': 'theta',
        r'\\pi': 'pi', r'\\mu': 'mu', r'\\sigma': 'sigma',
        r'\\omega': 'omega', r'\\Delta': 'Delta', r'\\times': 'x',
        r'\\div': '/', r'\\pm': '+/-', r'\\neq': '!=',
        r'\\leq': '<=', r'\\geq': '>=', r'\\approx': '~=',
        r'\\sin': 'sin', r'\\cos': 'cos', r'\\tan': 'tan',
        r'\\log': 'log', r'\\ln': 'ln',
    }
    for k, v in latex_map.items():
        cleaned = cleaned.replace(k, v)
    cleaned = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)', cleaned)
    cleaned = cleaned.replace('{', '(').replace('}', ')').replace('\\', '')
    if not unicode_fonts:
        cleaned = cleaned.encode('latin-1', 'ignore').decode('latin-1')
    return cleaned


def gemini_generate(prompt, response_mime='application/json'):
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                return client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type=response_mime)
                )
            except Exception as e:
                err = str(e)
                if '429' in err or 'RESOURCE_EXHAUSTED' in err or '503' in err or 'UNAVAILABLE' in err:
                    if attempt < 2:
                        time.sleep(15 * (attempt + 1))
                        continue
                    errors.append(f"{model}: rate limited/unavailable")
                    break
                else:
                    raise
    raise RuntimeError(f"All Gemini models exhausted. Tried: {', '.join(errors)}")


def gemini_generate_text(prompt):
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                return resp.text
            except Exception as e:
                err = str(e)
                if '429' in err or 'RESOURCE_EXHAUSTED' in err or '503' in err or 'UNAVAILABLE' in err:
                    if attempt < 2:
                        time.sleep(15 * (attempt + 1))
                        continue
                    errors.append(f"{model}: rate limited/unavailable")
                    break
                else:
                    raise
    raise RuntimeError(f"All Gemini models exhausted. Tried: {', '.join(errors)}")

# ==========================================
# IMAGE SCRAPER
# ==========================================
def generate_image_with_gemini(query):
    """Generate image using Gemini's image generation capabilities."""
    try:
        print(f"  🎨 Generating image with Gemini for: {query[:40]}...")
        
        # Refine prompt for better image generation
        refined_prompt = f"Create a clear, professional diagram or illustration of: {query}. Style: clean, educational, high-quality."
        
        # Use Gemini to generate an image
        print(f"    📤 Calling gemini-3.1-flash-image-preview model...")
        response = client.models.generate_images(
            model="gemini-3.1-flash-image-preview",
            prompt=refined_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                safety_filter_level="block_only_high",
                aspect_ratio="1:1",
            )
        )
        print(f"    ✓ Model responded successfully")
        
        if response and hasattr(response, 'generated_images') and response.generated_images:
            img_obj = response.generated_images[0]
            
            # Try to get the image URL (could be display_url or gcs_uri)
            img_url = None
            if hasattr(img_obj, 'image'):
                if hasattr(img_obj.image, 'display_url'):
                    img_url = img_obj.image.display_url
                elif hasattr(img_obj.image, 'gcs_uri'):
                    img_url = img_obj.image.gcs_uri
            elif hasattr(img_obj, 'display_url'):
                img_url = img_obj.display_url
            elif hasattr(img_obj, 'gcs_uri'):
                img_url = img_obj.gcs_uri
            
            if img_url:
                # Download the generated image
                print(f"    📥 Downloading generated image from {img_url[:50]}...")
                img_resp = requests.get(img_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
                if img_resp.status_code == 200:
                    stream = BytesIO(img_resp.content)
                    img = Image.open(stream)
                    buf = BytesIO()
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.save(buf, format='PNG')
                    buf.seek(0)
                    print(f"  ✓ Image generated successfully ({len(buf.getvalue())} bytes)")
                    return buf
                else:
                    print(f"    ✗ Failed to download: HTTP {img_resp.status_code}")
            else:
                print(f"    ✗ No image URL in response - response type: {type(img_obj)}")
        else:
            print(f"    ✗ No generated_images in response")
    except Exception as e:
        print(f"  ⚠️ Gemini generation error: {str(e)[:100]}")
    
    return None

def scrape_image(query):
    """Fetch image from web or generate using Gemini as fallback."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    
    # Try web scraping first
    try:
        print(f"  🔍 Scraping image for: {query[:40]}...")
        url = f"https://www.bing.com/images/search?q={quote_plus(query)}&first=1"
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code == 200:
            img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', resp.text)
            if not img_urls:
                img_urls = re.findall(r'src2?="(https?://[^"]+\.(?:jpg|jpeg|png|webp))', resp.text)
            
            for img_url in img_urls[:5]:
                try:
                    r = requests.get(img_url, headers=headers, timeout=8)
                    if r.status_code == 200 and len(r.content) > 2000:
                        stream = BytesIO(r.content)
                        try:
                            img = Image.open(stream)
                            # Verify it's a valid image
                            img.load()
                            
                            # Convert if needed
                            if img.format not in ('JPEG', 'PNG'):
                                buf = BytesIO()
                                if img.mode != 'RGB':
                                    img = img.convert('RGB')
                                img.save(buf, format='PNG')
                                buf.seek(0)
                                print(f"  ✓ Image scraped successfully")
                                return buf
                            else:
                                stream.seek(0)
                                print(f"  ✓ Image scraped successfully")
                                return stream
                        except Exception:
                            continue
                except Exception:
                    continue
    except Exception as e:
        print(f"  ⚠️ Web scraping failed: {str(e)[:60]}")
    
    # Fallback to Gemini image generation
    print(f"  📡 Falling back to Gemini generation...")
    return generate_image_with_gemini(query)

# ==========================================
# SHAPE / LAYOUT HELPERS
# ==========================================
def set_slide_bg(slide, color):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color

def add_textbox(slide, left, top, width, height, text, font_size=18,
                color=CONTENT_BODY, bold=False, alignment=PP_ALIGN.LEFT,
                font_name=FONT_BODY, anchor=MSO_ANCHOR.TOP):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    txBox.text_frame._txBody.bodyPr.set('anchor', {
        MSO_ANCHOR.TOP: 't', MSO_ANCHOR.MIDDLE: 'ctr', MSO_ANCHOR.BOTTOM: 'b'
    }.get(anchor, 't'))
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.font.name = font_name
    p.alignment = alignment
    return txBox

def add_bullet_list(slide, left, top, width, height, items, font_size=17,
                    color=CONTENT_BODY, bullet_color=CONTENT_BULLET):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for idx, item in enumerate(items):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        # Bullet run (colored dot)
        run_bullet = p.add_run()
        run_bullet.text = "• "
        run_bullet.font.size = Pt(font_size)
        run_bullet.font.color.rgb = bullet_color
        run_bullet.font.bold = True
        run_bullet.font.name = FONT_BODY
        # Text run
        run_text = p.add_run()
        run_text.text = str(item)
        run_text.font.size = Pt(font_size)
        run_text.font.color.rgb = color
        run_text.font.name = FONT_BODY
        p.space_after = Pt(5)
    return txBox

def add_line(slide, left, top, width, color=ACCENT_GOLD, thickness=3):
    shape = slide.shapes.add_shape(1, left, top, width, Pt(thickness))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape

def add_rect(slide, left, top, width, height, fill_color=CARD_BG):
    shape = slide.shapes.add_shape(5, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape

def _lock_shape(shape):
    cNvPicPr = shape._element.find('.//' + qn('p:cNvPicPr'))
    if cNvPicPr is not None:
        locks = cNvPicPr.find(qn('a:picLocks'))
        if locks is None:
            from lxml import etree
            locks = etree.SubElement(cNvPicPr, qn('a:picLocks'))
        for attr in ('noSelect','noMove','noResize','noRot','noChangeAspect'):
            locks.set(attr, '1')

def add_logo(slide):
    if not os.path.exists(LOGO_PATH):
        return
    # Add picture initially with desired height
    pic = slide.shapes.add_picture(LOGO_PATH, 0, 0, height=Inches(0.45))
    # Move to bottom right corner with some padding
    pic.left = SLIDE_W - pic.width - Inches(0.25)
    pic.top = SLIDE_H - pic.height - Inches(0.25)
    _lock_shape(pic)

# ==========================================
# SLIDE BUILDERS
# ==========================================

# ── HEADING SLIDES (blue bg, gold text) ──────────────────────────
def build_title_slide(prs, title, subtitle):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, HEADING_BG)
    # Gold horizontal bars
    add_line(slide, Inches(1.2), Inches(2.3), Inches(10.93), ACCENT_GOLD, 4)
    add_textbox(slide, Inches(1.2), Inches(2.5), Inches(10.93), Inches(1.8),
                title, font_size=44, color=HEADING_TEXT, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_textbox(slide, Inches(1.2), Inches(4.4), Inches(10.93), Inches(0.9),
                subtitle, font_size=22, color=HEADING_SUB,
                alignment=PP_ALIGN.CENTER)
    add_line(slide, Inches(1.2), Inches(5.35), Inches(10.93), ACCENT_GOLD, 4)
    add_logo(slide)


def build_section_slide(prs, section_number, section_title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, HEADING_BG)
    label = f"SECTION {section_number}" if section_number else "SECTION"
    add_textbox(slide, Inches(1), Inches(2.1), Inches(11.33), Inches(0.8),
                label, font_size=18, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER)
    add_line(slide, Inches(4.5), Inches(3.0), Inches(4.33), ACCENT_GOLD, 3)
    add_textbox(slide, Inches(1), Inches(3.1), Inches(11.33), Inches(1.6),
                section_title, font_size=40, color=HEADING_TEXT, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_line(slide, Inches(4.5), Inches(4.8), Inches(4.33), ACCENT_GOLD, 3)
    add_logo(slide)


# ── CONTENT SLIDES (white bg, black text) ────────────────────────
def build_content_slide(prs, title, bullets):
    """Build content slides with automatic overflow handling - creates new slides if needed."""
    # Parse bullets into list
    if isinstance(bullets, list):
        items = bullets
    else:
        items = [l.strip('- •▸✦').strip() for l in str(bullets).split('\n') if l.strip()]
        if len(items) <= 1:
            items = [s.strip() for s in str(bullets).split('.') if s.strip()]
    
    # Calculate max items per slide (with font_size=17, approximately 7-8 items fit in 5.5 inches)
    max_items_per_slide = 8
    
    # Split items into chunks
    item_chunks = [items[i:i + max_items_per_slide] for i in range(0, len(items), max_items_per_slide)]
    
    for chunk_idx, chunk_items in enumerate(item_chunks):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        set_slide_bg(slide, CONTENT_BG)
        
        # Title bar: deep-blue filled rectangle behind title
        add_rect(slide, Inches(0), Inches(0), Inches(13.33), Inches(1.15), HEADING_BG)
        
        # Modify title to show continuation if multiple slides
        display_title = title if chunk_idx == 0 else f"{title} (continued)"
        add_textbox(slide, Inches(0.5), Inches(0.12), Inches(12.33), Inches(0.9),
                    display_title, font_size=30, color=HEADING_TEXT, bold=True,
                    alignment=PP_ALIGN.LEFT, font_name=FONT_TITLE)
        
        # Gold underline
        add_line(slide, Inches(0), Inches(1.15), Inches(13.33), ACCENT_GOLD, 3)
        
        # Bullet card
        add_rect(slide, Inches(0.5), Inches(1.35), Inches(12.33), Inches(5.8), CARD_BG)
        
        # Add bullet list for this chunk
        add_bullet_list(slide, Inches(0.9), Inches(1.55), Inches(11.73), Inches(5.5),
                        chunk_items, font_size=17)
        
        add_logo(slide)


def build_diagram_slide(prs, title, bullets, search_query):
    """Build diagram slides with image support - creates new slides if needed for overflow."""
    # Parse bullets into list
    if isinstance(bullets, list):
        items = bullets
    else:
        items = [l.strip('- •▸').strip() for l in str(bullets).split('\n') if l.strip()]
        if len(items) <= 1:
            items = [s.strip() for s in str(bullets).split('.') if s.strip()]
    
    # Calculate max items per slide (with font_size=16, approximately 5-6 items fit in 5.5 inches on left)
    max_items_per_slide = 6
    
    # Split items into chunks
    item_chunks = [items[i:i + max_items_per_slide] for i in range(0, len(items), max_items_per_slide)]
    
    for chunk_idx, chunk_items in enumerate(item_chunks):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        set_slide_bg(slide, CONTENT_BG)
        add_rect(slide, Inches(0), Inches(0), Inches(13.33), Inches(1.15), HEADING_BG)
        
        # Modify title to show continuation if multiple slides
        display_title = title if chunk_idx == 0 else f"{title} (continued)"
        add_textbox(slide, Inches(0.5), Inches(0.12), Inches(12.33), Inches(0.9),
                    display_title, font_size=28, color=HEADING_TEXT, bold=True,
                    alignment=PP_ALIGN.LEFT, font_name=FONT_TITLE)
        add_line(slide, Inches(0), Inches(1.15), Inches(13.33), ACCENT_GOLD, 3)
        
        # Only show image on the first slide (chunk_idx == 0)
        if chunk_idx == 0:
            # Left: bullet points with image on right (smaller left panel)
            add_rect(slide, Inches(0.5), Inches(1.35), Inches(5.6), Inches(5.8), CARD_BG)
            add_bullet_list(slide, Inches(0.9), Inches(1.55), Inches(5.0), Inches(5.5),
                            chunk_items, font_size=16)
            
            # Right: image panel
            img_stream = scrape_image(search_query)
            add_rect(slide, Inches(6.7), Inches(1.35), Inches(6.13), Inches(5.8), CARD_BG)
            if img_stream:
                slide.shapes.add_picture(img_stream, Inches(6.85), Inches(1.5),
                                         width=Inches(5.8), height=Inches(5.4))
            else:
                add_textbox(slide, Inches(6.85), Inches(3.6), Inches(5.8), Inches(1),
                            "[Diagram: " + search_query + "]",
                            font_size=14, color=CONTENT_BODY, alignment=PP_ALIGN.CENTER)
        else:
            # Continuation slides: full width bullet list (no image)
            add_rect(slide, Inches(0.5), Inches(1.35), Inches(12.33), Inches(5.8), CARD_BG)
            add_bullet_list(slide, Inches(0.9), Inches(1.55), Inches(11.73), Inches(5.5),
                            chunk_items, font_size=16)
        
        add_logo(slide)


def build_summary_slide(prs, title, points):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, CONTENT_BG)
    add_rect(slide, Inches(0), Inches(0), Inches(13.33), Inches(1.15), HEADING_BG)
    add_textbox(slide, Inches(0.5), Inches(0.12), Inches(12.33), Inches(0.9),
                title, font_size=30, color=HEADING_TEXT, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_line(slide, Inches(0), Inches(1.15), Inches(13.33), ACCENT_GOLD, 3)
    mid = (len(points) + 1) // 2
    left_items, right_items = points[:mid], points[mid:]
    add_rect(slide, Inches(0.5), Inches(1.35), Inches(5.9), Inches(5.8), CARD_BG)
    add_bullet_list(slide, Inches(0.9), Inches(1.55), Inches(5.3), Inches(5.5),
                    left_items, font_size=17)
    if right_items:
        add_rect(slide, Inches(6.9), Inches(1.35), Inches(5.9), Inches(5.8), CARD_BG)
        add_bullet_list(slide, Inches(7.3), Inches(1.55), Inches(5.3), Inches(5.5),
                        right_items, font_size=17)
    add_logo(slide)


def build_thank_you_slide(prs, class_num, subject, title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, HEADING_BG)
    add_textbox(slide, Inches(1), Inches(2.4), Inches(11.33), Inches(1.6),
                "Thank You!", font_size=52, color=HEADING_TEXT, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_line(slide, Inches(4), Inches(4.2), Inches(5.33), ACCENT_GOLD, 4)
    footer_text = title if title else (f"Class {class_num}  |  {subject}  |  Presentation" if class_num else "Thank You for Learning")
    add_textbox(slide, Inches(1), Inches(4.5), Inches(11.33), Inches(0.8),
                footer_text,
                font_size=18, color=HEADING_SUB, alignment=PP_ALIGN.CENTER)
    add_logo(slide)

# ==========================================
# PROMPTS  (50 – 200 slides, school-friendly)
# ==========================================
GEMINI_PROMPT_TEMPLATE = """You are an expert {subject} teacher creating a school presentation for Class {class_num} students on the chapter "{chapter}".

Your goal is to produce a VERY COMPREHENSIVE, school-exam-ready presentation.

Return a JSON array of 80 to 120 slide objects. Each object must have EXACTLY these keys:
- "type"           : one of "title" | "section" | "content" | "diagram" | "summary"
- "title"          : clear, specific slide heading (not generic)
- "body"           : array of 5-7 bullet strings for content/diagram/summary slides; empty array [] for title/section
- "search_query"   : short Bing image search query ONLY for "diagram" slides (e.g. "labeled diagram of human heart"); empty string for all others
- "section_number" : integer for "section" slides; null for all others

STRUCTURE RULES:
1. Slide 1  → type "title"  (chapter name, subtitle = "Class {class_num} — {subject}")
2. Organise into 8-12 logical sections that cover the ENTIRE chapter syllabus
3. Every section opens with a "section" slide
4. Within each section include:
   - 2-3 "content" slides: definitions, explanations, examples, key facts
   - 1-2 "diagram" slides: only where a visual truly helps understanding
5. After all sections add 2-3 "summary" slides reviewing key points
6. Final slide → type "title" (Thank You)
7. Bullet point rules:
   - Write complete, meaningful sentences (not fragments)
   - Use simple language appropriate for Class {class_num} students
   - Each bullet should teach ONE clear fact, definition, or example
   - Include real-life examples, mnemonics, and comparisons where helpful
   - Mark important terms with CAPS, e.g. "PHOTOSYNTHESIS is the process by which..."
8. Include at least 15 diagram slides total (spread across sections)

CONTENT QUALITY:
- Cover EVERY sub-topic in the chapter — do not skip anything
- Explain concepts step-by-step as a good teacher would
- Include "did you know" facts, common misconceptions to avoid, and exam tips
- Ensure the presentation is self-sufficient — a student should be able to study from it alone

Return ONLY the JSON array. No markdown, no explanation."""

GEMINI_PROMPT_TEXT_ONLY = """You are an expert {subject} teacher creating a comprehensive school presentation for Class {class_num} students on the chapter "{chapter}".

Return a JSON array of 80 to 120 slide objects. Each object must have EXACTLY these keys:
- "type"           : one of "title" | "section" | "content" | "summary"
- "title"          : clear, specific slide heading
- "body"           : array of 6-8 detailed bullet strings for content/summary; [] for title/section
- "search_query"   : empty string always (text-only mode)
- "section_number" : integer for "section" slides; null for all others

BULLET POINT RULES:
- Write in full sentences (25-40 words each)
- Simple language for Class {class_num} students
- Each bullet teaches ONE complete idea: definition, explanation, example, or fact
- Include real-world examples, comparisons, and easy-to-remember explanations
- Mark key terms in CAPS on first use, e.g. "OSMOSIS is the movement of water..."
- Add exam tips labelled [EXAM TIP:] and memory tricks labelled [REMEMBER:]
- Never write vague bullets like "Important concept" — be specific and educational

STRUCTURE:
1. Slide 1: "title" slide
2. 8-12 sections covering EVERY syllabus topic
3. Each section: 1 "section" slide + 4-6 "content" slides
4. 2-3 "summary" slides at the end
5. Final "title" slide (Thank You)

Cover ALL sub-topics. A student studying ONLY these slides should be fully prepared for their exam.

Return ONLY the JSON array. No markdown."""

# ==========================================
# NOTES PROMPTS
# ==========================================
NOTES_THEORY_PROMPT = """You are an expert {subject} teacher writing a COMPLETE, DETAILED study guide on "{chapter}" for Class {class_num} students.

Write thorough theory notes in clean Markdown covering ALL sections below. Do NOT skip or shorten any section.

# {chapter} — Complete Study Guide (Class {class_num} {subject})

## 1. Chapter Overview
(150-200 word introduction: scope, importance, what students will learn.)

## 2. Key Concepts & Detailed Explanations
(Use ### sub-headings for each concept. Explain in simple language with real-life examples. Cover at least 8-12 concepts.)

## 3. Important Definitions & Glossary
(Bold each term. Full clear definition. At least 20 definitions.)

## 4. Formulas, Laws & Equations
(Every formula with symbol meanings. Derivations where possible.)

## 5. Solved Examples & Worked Problems
(8-10 fully worked step-by-step problems.)

## 6. Diagrams & Visual Concepts
(Describe 8-10 key diagrams. For each: title, what it shows, label all parts, why it matters.)

## 7. Comparison Tables
(5-6 markdown tables comparing related concepts.)

## 8. Important Facts, Figures & Real-Life Applications
(Notable facts, historical context, statistics, real-world uses.)

## 9. Common Student Mistakes & Misconceptions
(List at least 10 mistakes students commonly make, with corrections.)

## 10. Quick Revision Bullets
(30-35 short, exam-ready bullet points.)

## 11. Exam Tips & How to Score Full Marks
(What examiners look for, marking schemes, how to write answers.)

## 12. Memory Tricks & Mnemonics
(Helpful acronyms, rhymes, or visual tricks for key facts.)

## 13. Chapter Summary
(150-word paragraph for last-minute revision.)

Rules:
- **Bold** all key terms on first use.
- Markdown tables for comparisons.
- Do NOT truncate — be exhaustive.
- Return Markdown ONLY."""

NOTES_THEORY_PROMPT_WITH_DIAGRAMS = """You are an expert {subject} teacher writing a COMPLETE, DETAILED study guide on "{chapter}" for Class {class_num} students.

Write thorough theory notes in clean Markdown.

# {chapter} — Complete Study Guide (Class {class_num} {subject})

## 1. Chapter Overview
(150-200 word introduction.)

## 2. Key Concepts & Detailed Explanations
(### sub-headings for each concept. Simple language, real-life examples. At least 8-12 concepts.)

## 3. Important Definitions & Glossary
(Bold terms, full definitions. At least 20 definitions.)

## 4. Formulas, Laws & Equations
(Every formula with symbol meanings and derivations.)

## 5. Solved Examples & Worked Problems
(8-10 fully worked step-by-step problems.)

## 6. Diagram Guide
(For each of 10 key diagrams:
- **Diagram:** Title
- **Description:** Detailed description, all parts labelled.
- **Key Insight:** Why it matters for understanding the concept.)

## 7. Comparison Tables
(5-6 markdown tables comparing related concepts.)

## 8. Important Facts, Figures & Real-Life Applications

## 9. Common Student Mistakes & Misconceptions
(10+ mistakes with corrections.)

## 10. Quick Revision Bullets
(30-35 exam-ready bullets.)

## 11. Exam Tips & How to Score Full Marks

## 12. Memory Tricks & Mnemonics

## 13. Chapter Summary
(150-word paragraph.)

Rules: **bold** key terms. Markdown tables. Be exhaustive. Return Markdown ONLY."""

NOTES_QUESTIONS_PROMPT = """You are an expert {subject} teacher. Generate a COMPLETE question bank for "{chapter}" for Class {class_num} students.

Return clean Markdown. Do NOT skip or shorten any section.

---

# Question Bank — {chapter} (Class {class_num} {subject})

## Section A: Multiple Choice Questions (MCQs)
(25 MCQs. 4 options each: (a)(b)(c)(d). Mark correct answer with a one-line explanation.)

Format:
**Q1.** Question
(a) Option A  (b) Option B  (c) Option C  (d) Option D
**Answer:** (x) — Explanation.

---

## Section B: Very Short Answer (1 Mark)
(20 questions. 1-2 sentence answers.)

**Q1.** Question
**Ans:** Answer

---

## Section C: Short Answer (2-3 Marks)
(15 questions. 4-6 sentence answers with key terms.)

**Q1.** Question
**Ans:** Answer

---

## Section D: Long Answer / Essay (5 Marks)
(10 questions. Thorough answers with sub-headings or bullet points. Exam-ready quality.)

**Q1.** Question
**Ans:**
Full detailed answer...

---

## Section E: Previous Year Questions (PYQs)
(20 PYQ-style questions in CBSE board exam format. Include [CBSE Year — Marks]. Full model answers.)

**PYQ Q1.** [CBSE 2023 — 3 Marks] Question
**Ans:** Model answer

---

## Section F: Assertion-Reason Questions
(10 questions. Use standard instructions. Give correct option with explanation.)

**Q1.**
**Assertion (A):** Statement
**Reason (R):** Statement
**Answer:** Option (X) — explanation

---

## Section G: Case Study / Source-Based Questions
(5 case studies. Short passage + 4 sub-questions with answers.)

**Case Study 1:**
Passage...
**(i)** Question  **Ans:** Answer
**(ii)** Question  **Ans:** Answer
**(iii)** Question  **Ans:** Answer
**(iv)** Question  **Ans:** Answer

---

## Section H: Fill in the Blanks
(25 statements with answers in brackets.)

**Q1.** The process of _______ occurs when...
**Ans:** [keyword]

---

## Section I: True or False (with Justification)
(20 statements. True/False + one-sentence justification.)

**Q1.** Statement.
**Ans:** True/False — Justification.

---

## Section J: Match the Following
(4 matching exercises, Column A and Column B with 5 items each. Answers provided.)

---

## Section K: One-Word / One-Line Answers
(20 questions.)

---

## Section L: Diagram-Based Questions
(10 questions asking students to draw/label or interpret a diagram. Full model answers.)

---

## Section M: HOTS (Higher Order Thinking Skills)
(10 questions requiring analysis, application, or evaluation. Full answers.)

---

Rules:
- ALL answers must be complete and exam-quality. Never write "answer not provided".
- Use exact format specified. Return Markdown ONLY."""

NOTES_PROMPT = NOTES_THEORY_PROMPT
NOTES_PROMPT_WITH_IMAGES = NOTES_THEORY_PROMPT_WITH_DIAGRAMS

# ==========================================
# PPT GENERATION FROM OUTLINE
# ==========================================
def parse_outline_to_slides(outline_text):
    """Parse user outline into slide structure."""
    slides = []
    lines = outline_text.strip().split('\n')
    current_slide = None
    
    for line in lines:
        line = line.rstrip()
        if line.startswith('# '):
            # New slide
            if current_slide and current_slide['title']:
                slides.append(current_slide)
            current_slide = {
                'title': line[2:].strip(),
                'bullets': [],
                'has_visual': False
            }
        elif line.startswith('## '):
            # Section slide
            if current_slide and current_slide['title']:
                slides.append(current_slide)
            current_slide = {
                'title': line[3:].strip(),
                'is_section': True,
                'bullets': [],
                'has_visual': False
            }
        elif line.startswith('- ') or line.startswith('* '):
            if current_slide:
                bullet_text = line[2:].strip()
                current_slide['bullets'].append(bullet_text)
                if '[VISUAL]' in bullet_text or '[DIAGRAM]' in bullet_text:
                    current_slide['has_visual'] = True
    
    if current_slide and current_slide['title']:
        slides.append(current_slide)
    
    return slides


def expand_slides_with_gemini(slides, presentation_title, use_images):
    """Expand outline slides with detailed content using Gemini."""
    prompt = f"""You are an expert educator creating a comprehensive presentation from a user-provided outline.

The user has provided this presentation outline with {len(slides)} slides:

{json.dumps(slides, indent=2)}

Your task:
1. Expand each slide's bullets with detailed, educational content
2. For slides marked with [VISUAL] or [DIAGRAM], provide a search_query for finding relevant images
3. Maintain the original slide titles and structure
4. Create clear, complete bullet points (3-5 sentences each) suitable for classroom use
5. Mark important terms in CAPS
6. If there are fewer than 5 slides, generate additional related content slides between existing ones

Return a JSON array of expanded slide objects with these keys:
- "title": slide title (from outline or enhanced)
- "body": array of 5-8 detailed bullet points
- "search_query": image search query if visual needed, empty string otherwise
- "type": one of "title", "content", or "diagram"

Ensure the presentation flows logically and covers all outlined topics thoroughly.

Return ONLY the JSON array, no markdown."""

    response = gemini_generate(prompt)
    try:
        expanded = parse_json_response(response.text)
    except ValueError:
        try:
            expanded = repair_slides_json(response.text)
        except Exception as e:
            raise ValueError(f"Could not parse expanded slides. Error: {e}")
    
    return expanded


def generate_ppt_from_outline(outline_text, presentation_title, use_images):
    """Generate PPT from user-provided outline."""
    # Parse outline
    slides = parse_outline_to_slides(outline_text)
    
    if not slides:
        raise ValueError("No valid slides found in outline. Ensure slides start with '# ' or '## '")
    
    # Expand with Gemini
    expanded_slides = expand_slides_with_gemini(slides, presentation_title, use_images)
    
    # Normalize
    normalized = []
    for i, slide in enumerate(expanded_slides):
        s_type = str(slide.get('type', 'content')).strip().lower()
        if s_type not in ('title', 'section', 'content', 'diagram', 'summary'):
            # If has search query and images enabled, mark as diagram
            if use_images and slide.get('search_query', '').strip():
                s_type = 'diagram'
            else:
                s_type = 'content'
        
        title = str(slide.get('title', f"Slide {i+1}")).strip()
        body = slide.get('body', [])
        if isinstance(body, str):
            body = [b.strip() for b in re.split(r'[\n\r]+', body) if b.strip()]
        elif not isinstance(body, list):
            body = []
        body = [str(b).strip() for b in body if str(b).strip()]
        
        search_query = str(slide.get('search_query', '') or '').strip()
        
        if not body and s_type in ('content', 'diagram', 'summary'):
            body = [f"Key points about {title}"]
        
        if s_type == 'diagram' and not search_query:
            search_query = f"diagram of {title}"
        
        normalized.append({
            'type': s_type,
            'title': title,
            'body': body,
            'search_query': search_query,
            'section_number': None,
        })
    
    # Add title slide if not present
    if not normalized or normalized[0]['type'] != 'title':
        normalized.insert(0, {
            'type': 'title',
            'title': presentation_title,
            'body': ["Comprehensive Learning Presentation"],
            'search_query': '',
            'section_number': None,
        })
    
    # Build presentation
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    
    for data in normalized:
        slide_type = data.get('type', 'content')
        title = data.get('title', '')
        body = data.get('body', []) or [f"Key points about {title}"]
        query = data.get('search_query', '')
        
        if slide_type == 'title':
            subtitle = body[0] if body else "Comprehensive Learning Presentation"
            build_title_slide(prs, title, subtitle)
        elif slide_type == 'section':
            build_section_slide(prs, None, title)
        elif slide_type == 'diagram':
            if use_images:
                build_diagram_slide(prs, title, body, query)
            else:
                build_content_slide(prs, title, body)
        elif slide_type == 'summary':
            build_summary_slide(prs, title, body if isinstance(body, list) else [body])
        else:
            build_content_slide(prs, title, body)
    
    build_thank_you_slide(prs, "", "", presentation_title)
    
    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)
    safe_name = re.sub(r'[^\w\s-]', '', presentation_title).strip().replace(' ', '_')
    return buf, f"{safe_name}_Presentation.pptx", normalized


def generate_notes_from_outline(outline_text, presentation_title, use_images=False, slides_data=None):
    """Generate notes from outline."""
    prompt = f"""You are an expert educator creating comprehensive study notes from this presentation outline:

{outline_text}

Create detailed, exam-ready study notes in Markdown format covering:

# {presentation_title} — Complete Study Guide

## 1. Overview
(150-200 words introducing the topic)

## 2. Key Concepts & Detailed Explanations
(Use ### sub-headings. Cover all concepts from the outline with examples.)

## 3. Important Definitions & Glossary
(Define at least 15 key terms)

## 4. Summary of Key Points
(Bullet list of main takeaways)

## 5. Exam Preparation
(Common questions, exam tips, memory aids)

---

## QUESTION BANK

### Section A: Multiple Choice Questions (MCQ)
(20 questions with 4 options each and answers)

### Section B: Very Short Answer (1 Mark)
(20 questions)

### Section C: Short Answer (2-3 Marks)
(15 questions)

### Section D: Long Answer (5 Marks)
(10 questions)

---

Ensure comprehensive coverage of all topics in the outline. Return Markdown ONLY."""

    notes_text = gemini_generate_text(prompt)
    
    # Create PDF from notes
    safe_name = re.sub(r'[^\w\s-]', '', presentation_title).strip().replace(' ', '_')
    filename = f"{safe_name}_StudyNotes.pdf"
    
    pdf = FPDF(format='A4', unit='mm')
    pdf.add_page()
    pdf.set_font('Helvetica', '', 11)
    
    for line in notes_text.split('\n'):
        if line.strip():
            pdf.multi_cell(0, 5, line[:100])
    
    buf = BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    
    return buf, filename


# ==========================================
# PPT GENERATION
# ==========================================
def generate_ppt(class_num, subject, chapter, use_images):
    prompt = (GEMINI_PROMPT_TEMPLATE if use_images else GEMINI_PROMPT_TEXT_ONLY).format(
        class_num=class_num, subject=subject, chapter=chapter
    )
    response = gemini_generate(prompt)
    try:
        slides_data = parse_json_response(response.text)
    except ValueError:
        try:
            slides_data = repair_slides_json(response.text)
        except Exception as repair_err:
            raise ValueError(f"Could not parse slide JSON. Repair failed: {repair_err}")

    slides_data = normalize_slides_data(slides_data, class_num, subject, chapter, use_images)

    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    for data in slides_data:
        slide_type = data.get('type', 'content')
        title = data.get('title', '')
        body = data.get('body', []) or [f"Key points about {title}"]
        query = data.get('search_query', '')
        sec_num = data.get('section_number', '')

        if slide_type == 'title':
            subtitle = body[0] if body else f"Class {class_num} — {subject}"
            build_title_slide(prs, title, subtitle)
        elif slide_type == 'section':
            build_section_slide(prs, sec_num, title)
        elif slide_type == 'diagram':
            if use_images:
                build_diagram_slide(prs, title, body, query)
            else:
                build_content_slide(prs, title, body)
        elif slide_type == 'summary':
            build_summary_slide(prs, title, body if isinstance(body, list) else [body])
        else:
            build_content_slide(prs, title, body)

    build_thank_you_slide(prs, class_num, subject, chapter)

    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)
    safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
    return buf, f"Class{class_num}_{subject}_{safe_name}.pptx", slides_data


# ==========================================
# PDF GENERATION
# ==========================================
def generate_notes(class_num, subject, chapter, use_images=False, slides_data=None):
    theory_tmpl = NOTES_THEORY_PROMPT_WITH_DIAGRAMS if use_images else NOTES_THEORY_PROMPT
    theory_text = gemini_generate_text(theory_tmpl.format(
        class_num=class_num, subject=subject, chapter=chapter
    ))
    questions_text = gemini_generate_text(NOTES_QUESTIONS_PROMPT.format(
        class_num=class_num, subject=subject, chapter=chapter
    ))
    notes_text = theory_text.strip() + "\n\n---\n\n" + questions_text.strip()

    safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
    filename = f"Class{class_num}_{subject}_{safe_name}_Notes.pdf"

    # ── PDF colour scheme ──────────────────────────────────────────
    # Heading blocks: deep blue bg, gold text
    PDF_HEAD_BG   = (10, 25, 80)
    PDF_GOLD      = (255, 196, 61)
    PDF_CYAN      = (100, 150, 220)   # subtle blue for sub-headings
    # Body: white bg (default), near-black text
    PDF_BODY      = (30, 30, 30)
    PDF_LIGHT_CARD= (240, 244, 255)
    PDF_GRAY      = (110, 120, 140)
    PDF_ANS_BG    = (230, 240, 255)
    PDF_ANS_TEXT  = (10, 25, 80)

    # Font setup
    font_family = 'Arial'
    unicode_fonts = True
    try:
        win_font = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        if not os.path.exists(os.path.join(win_font, 'arial.ttf')):
            raise ValueError()
    except Exception:
        font_family = 'Helvetica'
        unicode_fonts = False

    class NotesPDF(FPDF):
        def header(self):
            if self.page_no() > 1:
                # Header bar: deep blue
                self.set_fill_color(*PDF_HEAD_BG)
                self.rect(0, 0, self.w, 13, 'F')
                self.set_font(font_family, 'B', 8)
                self.set_text_color(*PDF_GOLD)
                self.set_xy(10, 3)
                self.cell(0, 7,
                    normalize_pdf_text(f"Class {class_num}  |  {subject}  |  {chapter}", unicode_fonts),
                    align='R')
                if os.path.exists(LOGO_PATH):
                    self.image(LOGO_PATH, x=10, y=1.5, h=10)
                # Gold underline of header
                self.set_draw_color(*PDF_GOLD)
                self.set_line_width(0.5)
                self.line(0, 13, self.w, 13)
                self.set_y(18)

    pdf = NotesPDF()
    pdf.set_auto_page_break(auto=True, margin=22)

    if unicode_fonts:
        try:
            win_font = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
            pdf.add_font('Arial', '',  os.path.join(win_font, 'arial.ttf'),   uni=True)
            pdf.add_font('Arial', 'B', os.path.join(win_font, 'arialbd.ttf'), uni=True)
            pdf.add_font('Arial', 'I', os.path.join(win_font, 'ariali.ttf'),  uni=True)
        except Exception:
            unicode_fonts = False
            font_family = 'Helvetica'

    # ── Cover page ────────────────────────────────────────────────
    pdf.add_page()
    pdf.set_fill_color(*PDF_HEAD_BG)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')

    if os.path.exists(LOGO_PATH):
        pdf.image(LOGO_PATH, x=15, y=12, w=30)

    # Gold lines
    pdf.set_draw_color(*PDF_GOLD)
    pdf.set_line_width(1.2)
    pdf.line(30, 82, pdf.w - 30, 82)

    pdf.set_y(92)
    pdf.set_font(font_family, 'B', 30)
    pdf.set_text_color(*PDF_GOLD)
    pdf.multi_cell(0, 15, normalize_pdf_text(chapter, unicode_fonts), align='C')

    pdf.ln(6)
    pdf.set_font(font_family, '', 17)
    pdf.set_text_color(220, 235, 255)
    pdf.multi_cell(0, 9, normalize_pdf_text(f"Class {class_num}  |  {subject}", unicode_fonts), align='C')

    pdf.set_draw_color(*PDF_GOLD)
    pdf.line(30, pdf.h - 38, pdf.w - 30, pdf.h - 38)

    pdf.set_font(font_family, 'I', 10)
    pdf.set_text_color(*PDF_GRAY)
    pdf.set_y(pdf.h - 28)
    pdf.multi_cell(0, 6, normalize_pdf_text("Complete Study Guide & Question Bank", unicode_fonts), align='C')

    # ── Content pages ─────────────────────────────────────────────
    pdf.add_page()
    in_table = False

    for line in notes_text.split('\n'):
        stripped = line.strip()

        if pdf.get_y() > pdf.h - 28:
            pdf.add_page()

        # ── Main heading (# ) → blue block, gold text ────────────
        if re.match(r'^# ', stripped) and not re.match(r'^## ', stripped):
            pdf.ln(6)
            text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[2:]), unicode_fonts)
            y = pdf.get_y()
            pdf.set_fill_color(*PDF_HEAD_BG)
            pdf.rect(pdf.l_margin - 2, y, pdf.w - pdf.l_margin - pdf.r_margin + 4, 14, 'F')
            pdf.set_xy(pdf.l_margin + 4, y + 1.5)
            pdf.set_font(font_family, 'B', 16)
            pdf.set_text_color(*PDF_GOLD)
            pdf.multi_cell(0, 11, text)
            pdf.ln(4)

        # ── Section heading (## ) → blue block, gold text ─────────
        elif stripped.startswith('## '):
            pdf.ln(5)
            text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[3:]), unicode_fonts)
            y = pdf.get_y()
            pdf.set_fill_color(*PDF_HEAD_BG)
            pdf.rect(pdf.l_margin - 2, y, pdf.w - pdf.l_margin - pdf.r_margin + 4, 12, 'F')
            # Gold left bar
            pdf.set_fill_color(*PDF_GOLD)
            pdf.rect(pdf.l_margin - 2, y, 4, 12, 'F')
            pdf.set_xy(pdf.l_margin + 6, y + 1.5)
            pdf.set_font(font_family, 'B', 13)
            pdf.set_text_color(*PDF_GOLD)
            pdf.cell(0, 9, text)
            pdf.ln(15)

        # ── Sub-section heading (### ) → blue text, no block ──────
        elif stripped.startswith('### '):
            pdf.ln(3)
            text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[4:]), unicode_fonts)
            pdf.set_font(font_family, 'B', 12)
            pdf.set_text_color(*PDF_ANS_TEXT)
            pdf.multi_cell(0, 7, text)
            pdf.set_draw_color(*PDF_CYAN)
            pdf.set_line_width(0.25)
            y = pdf.get_y()
            pdf.line(pdf.l_margin, y, pdf.l_margin + 55, y)
            pdf.ln(3)

        # ── Horizontal rule (---) ─────────────────────────────────
        elif stripped == '---':
            pdf.ln(3)
            y = pdf.get_y()
            pdf.set_draw_color(*PDF_GOLD)
            pdf.set_line_width(0.5)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(4)

        # ── Question lines ─────────────────────────────────────────
        elif re.match(r'^\*\*(PYQ\s+)?Q\d+\.\*\*', stripped):
            pdf.ln(3)
            if pdf.get_y() > pdf.h - 35:
                pdf.add_page()
            raw = re.sub(r'\*\*(.+?)\*\*', r'\1', stripped)
            m = re.match(r'((PYQ\s+)?Q\d+\.)\s*(.*)', raw, re.DOTALL)
            if m:
                qnum = normalize_pdf_text(m.group(1), unicode_fonts)
                qbody = normalize_pdf_text(m.group(3), unicode_fonts)
                pdf.set_font(font_family, 'B', 11)
                pdf.set_text_color(*PDF_ANS_TEXT)
                nw = pdf.get_string_width(qnum + ' ') + 2
                pdf.set_x(pdf.l_margin)
                pdf.cell(nw, 7, qnum)
                pdf.set_font(font_family, '', 11)
                pdf.set_text_color(*PDF_BODY)
                pdf.multi_cell(0, 7, qbody)
            else:
                pdf.set_font(font_family, '', 11)
                pdf.set_text_color(*PDF_BODY)
                pdf.multi_cell(0, 7, normalize_pdf_text(raw, unicode_fonts))
            pdf.ln(1)

        # ── MCQ options (a)(b)(c)(d) ──────────────────────────────
        elif re.match(r'^\([a-d]\)', stripped):
            m = re.match(r'^(\([a-d]\))\s*(.*)', stripped)
            if m:
                pdf.set_x(pdf.l_margin + 10)
                pdf.set_font(font_family, 'B', 10)
                pdf.set_text_color(*PDF_ANS_TEXT)
                pdf.cell(10, 6, m.group(1))
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*PDF_BODY)
                pdf.multi_cell(0, 6, normalize_pdf_text(
                    re.sub(r'\*\*(.+?)\*\*', r'\1', m.group(2)), unicode_fonts))
            pdf.ln(0.5)

        # ── Answer lines ───────────────────────────────────────────
        elif re.match(r'^\*\*Ans(wer)?:', stripped, re.IGNORECASE):
            pdf.ln(1)
            if pdf.get_y() > pdf.h - 28:
                pdf.add_page()
            ans_raw = re.sub(r'^\*\*Ans(wer)?:\*\*\s*', '', stripped, flags=re.IGNORECASE)
            clean_ans = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', ans_raw), unicode_fonts)
            y = pdf.get_y()
            # Light blue answer card
            pdf.set_fill_color(*PDF_ANS_BG)
            pdf.rect(pdf.l_margin, y, pdf.w - pdf.l_margin - pdf.r_margin, 9, 'F')
            pdf.set_xy(pdf.l_margin + 3, y + 1)
            pdf.set_font(font_family, 'B', 10)
            pdf.set_text_color(*PDF_ANS_TEXT)
            pdf.cell(14, 7, 'Ans:')
            pdf.set_font(font_family, '', 10)
            pdf.set_text_color(*PDF_BODY)
            pdf.multi_cell(0, 7, clean_ans)
            pdf.ln(2)

        # ── Bullet points ─────────────────────────────────────────
        elif stripped.startswith('- ') or stripped.startswith('* '):
            bullet_text = stripped[2:]
            clean_text = normalize_pdf_text(
                re.sub(r'\*\*(.+?)\*\*', r'\1', bullet_text), unicode_fonts)
            pdf.set_x(pdf.l_margin + 4)
            pdf.set_font(font_family, 'B', 11)
            pdf.set_text_color(*PDF_ANS_TEXT)
            pdf.cell(6, 6, chr(149) if not unicode_fonts else '\u2022')
            pdf.set_font(font_family, '', 11)
            pdf.set_text_color(*PDF_BODY)
            pdf.multi_cell(0, 6, '  ' + clean_text)
            pdf.ln(1.5)

        # ── Table rows ────────────────────────────────────────────
        elif stripped.startswith('|') and '|' in stripped[1:]:
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if all(set(c) <= {'-', ':', ' '} for c in cells):
                continue
            col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / max(len(cells), 1)
            if not in_table:
                # Header row: blue bg, gold text
                pdf.set_fill_color(*PDF_HEAD_BG)
                pdf.set_font(font_family, 'B', 10)
                pdf.set_text_color(*PDF_GOLD)
                for cell in cells:
                    clean = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', cell), unicode_fonts)
                    pdf.cell(col_w, 8, clean, border=1, fill=True, align='C')
                pdf.ln()
                in_table = True
            else:
                # Data rows: light blue bg, dark text
                pdf.set_fill_color(*PDF_ANS_BG)
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*PDF_BODY)
                for cell in cells:
                    clean = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', cell), unicode_fonts)
                    pdf.cell(col_w, 7, clean, border=1, fill=True, align='C')
                pdf.ln()

        # ── Blank line ────────────────────────────────────────────
        elif stripped == '':
            in_table = False
            pdf.ln(2.5)

        # ── Normal body text ──────────────────────────────────────
        else:
            in_table = False
            pdf.set_font(font_family, '', 11)
            pdf.set_text_color(*PDF_BODY)
            clean_text = normalize_pdf_text(
                re.sub(r'\*\*(.+?)\*\*', r'\1', stripped), unicode_fonts)
            pdf.multi_cell(0, 6, clean_text)
            pdf.ln(1.5)

    # ── Diagram image pages (when image mode ON) ──────────────────
    if use_images and isinstance(slides_data, list):
        diagram_slides = [s for s in slides_data if s.get('type') == 'diagram'][:12]
        if diagram_slides:
            pdf.add_page()
            pdf.set_font(font_family, 'B', 18)
            pdf.set_text_color(*PDF_ANS_TEXT)
            # Heading block
            y = pdf.get_y()
            pdf.set_fill_color(*PDF_HEAD_BG)
            pdf.rect(0, y, pdf.w, 14, 'F')
            pdf.set_xy(pdf.l_margin, y + 1.5)
            pdf.set_text_color(*PDF_GOLD)
            pdf.cell(0, 11, normalize_pdf_text("Diagram Reference Pages", unicode_fonts), align='C')
            pdf.ln(18)

        for idx, s in enumerate(diagram_slides, 1):
            if pdf.get_y() > pdf.h - 110:
                pdf.add_page()
            title_d = normalize_pdf_text(str(s.get('title', f"Diagram {idx}")), unicode_fonts)
            query = str(s.get('search_query', '') or f"labeled diagram of {title_d}")

            pdf.set_font(font_family, 'B', 12)
            pdf.set_text_color(*PDF_ANS_TEXT)
            pdf.multi_cell(0, 7, normalize_pdf_text(f"{idx}. {title_d}", unicode_fonts))

            img_stream = scrape_image(query)
            if img_stream:
                img_bytes = img_stream.getvalue()
                tmp_name = f"_tmp_note_img_{idx}.png"
                with open(tmp_name, 'wb') as f:
                    f.write(img_bytes)
                try:
                    x, y = pdf.l_margin, pdf.get_y() + 2
                    w = min(160, pdf.w - pdf.l_margin - pdf.r_margin)
                    pdf.image(tmp_name, x=x, y=y, w=w)
                    pdf.set_y(y + 92)
                except Exception:
                    pdf.set_font(font_family, '', 10)
                    pdf.set_text_color(*PDF_GRAY)
                    pdf.multi_cell(0, 6, normalize_pdf_text(
                        f"[Could not render image: {query}]", unicode_fonts))
                finally:
                    try:
                        os.remove(tmp_name)
                    except Exception:
                        pass
            else:
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*PDF_GRAY)
                pdf.multi_cell(0, 6, normalize_pdf_text(
                    f"[No image found for: {query}]", unicode_fonts))
            pdf.ln(4)

    # ── Footer ────────────────────────────────────────────────────
    pdf.ln(8)
    y = pdf.get_y()
    pdf.set_draw_color(*PDF_GOLD)
    pdf.set_line_width(0.4)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    pdf.set_font(font_family, 'I', 9)
    pdf.set_text_color(*PDF_GRAY)
    pdf.multi_cell(0, 5,
        normalize_pdf_text(f"Complete Study Guide — Class {class_num} {subject} — {chapter}", unicode_fonts),
        align='C')

    buf = BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf, filename

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="AI Education Generator", page_icon="📚", layout="centered")

st.markdown("""
<style>
    .stApp { background-color: #03002e; }
    h1, h2, h3 { color: #ffc43d; }
    .stMarkdown p { color: #c8d2e1; }
    .stTextInput label, .stMultiSelect label, .stCheckbox label { color: #c8d2e1 !important; }
    .stButton > button { background-color: #ffc43d; color: #03002e; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

if os.path.exists(LOGO_PATH):
    st.image(LOGO_PATH, width=200)

st.title("📚 AI Education Generator")
st.markdown(
    "Generates comprehensive **80–120 slide** presentations and detailed study notes "
    "powered by AI — designed for school students."
)
st.divider()

st.markdown("### 📋 Enter Your Presentation Outline")
st.markdown(
    "Provide a slide-by-slide outline (up to 60 slides). Each slide should have:\n"
    "- A **title** (line starting with `#`)\n"
    "- **Bullet points** for content\n"
    "- Use `[VISUAL]` or `[DIAGRAM]` in bullet points to mark visual representations"
)

outline_text = st.text_area(
    "Paste your outline here:",
    placeholder="""# Slide 1: Introduction
- Key point 1
- Key point 2
- [VISUAL: Diagram showing concept]

# Slide 2: Main Content
- Point A
- Point B
- Point C
- [DIAGRAM: Compare with previous topic]

# Slide 3: Summary
- Recap point 1
- Recap point 2""",
    height=350
)

col1, col2 = st.columns(2)
with col1:
    presentation_title = st.text_input("Presentation Title", placeholder="e.g. Human Life Processes")
with col2:
    output_types = st.multiselect(
        "What would you like to generate?",
        ["Presentation (PPTX)", "Interactive HTML"],
        default=["Presentation (PPTX)", "Interactive HTML"]
    )

use_images = st.checkbox("Include images / diagrams", value=False)
include_quiz = st.checkbox("Include interactive quiz in HTML", value=True) if "Interactive HTML" in output_types else False
elaborate_content = st.checkbox("Elaborate on bullet points (generate detailed content)", value=True)

st.divider()

if st.button("🚀 Generate Content", type="primary", use_container_width=True):
    if not outline_text or not outline_text.strip():
        st.error("Please paste your presentation outline.")
    elif not presentation_title:
        st.error("Please enter a presentation title.")
    elif not output_types:
        st.error("Please select at least one output type.")
    elif not API_KEY:
        st.error("GEMINI_API_KEY environment variable not set.")
    else:
        with st.status("Generating your content… this may take a few minutes.", expanded=True) as status:
            ppt_buf = ppt_filename = slides_data = None
            html_buf = html_filename = None
            notes_buf = notes_filename = None

            if "Presentation (PPTX)" in output_types:
                st.write("🤖 Processing outline and expanding content…")
                try:
                    ppt_buf, ppt_filename, slides_data = generate_ppt_from_outline(
                        outline_text, presentation_title, use_images)
                except Exception as e:
                    status.update(label="❌ Presentation generation failed", state="error")
                    st.error(f"PPT Error: {e}")
                    st.stop()

            if "Interactive HTML" in output_types:
                st.write("🌐 Generating interactive HTML presentation…")
                try:
                    if generate_html_from_outline:
                        html_buf, html_filename = generate_html_from_outline(
                            outline_text, presentation_title, include_quiz=include_quiz, elaborate=elaborate_content)
                        st.success(f"✅ HTML generated: {html_filename}")
                    else:
                        st.warning("⚠️ HTML generation module not available - please check dependencies")
                except Exception as e:
                    st.error(f"❌ HTML Error: {type(e).__name__}: {e}")
                    import traceback
                    st.write("Debug traceback:")
                    st.code(traceback.format_exc())

            if "Study Notes (PDF)" in output_types:
                st.write("📝 Generating study notes + question bank…")
                try:
                    notes_buf, notes_filename = generate_notes_from_outline(
                        outline_text, presentation_title,
                        use_images=use_images,
                        slides_data=slides_data)
                except Exception as e:
                    status.update(label="❌ Notes generation failed", state="error")
                    st.error(f"Notes Error: {e}")
                    st.stop()

            status.update(label="✅ Done! Your content is ready.", state="complete")

        # Download buttons
        if ppt_buf or html_buf or notes_buf:
            # Build output zip if multiple files
            if (ppt_buf and notes_buf) or (ppt_buf and html_buf) or (html_buf and notes_buf):
                st.success("Your content is ready for download!")
                zip_buf = BytesIO()
                with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                    if ppt_buf:
                        zf.writestr("Presentation.pptx", ppt_buf.getvalue())
                    if html_buf:
                        zf.writestr(html_filename if html_filename else "Presentation.html", html_buf.getvalue())
                    if notes_buf:
                        zf.writestr("StudyNotes.pdf", notes_buf.getvalue())
                zip_buf.seek(0)
                st.download_button(
                    "📥 Download All Files (ZIP)",
                    data=zip_buf,
                    file_name=f"{presentation_title}.zip",
                    mime="application/zip",
                    use_container_width=True
                )
                st.divider()

            # Individual download buttons
            if ppt_buf:
                st.download_button(
                    "📊 Download Presentation (PPTX)",
                    data=ppt_buf,
                    file_name=ppt_filename,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    use_container_width=True
                )

            if html_buf:
                st.download_button(
                    "🌐 Download Interactive HTML",
                    data=html_buf,
                    file_name=html_filename if html_filename else "Presentation.html",
                    mime="text/html",
                    use_container_width=True
                )
                st.info("💡 Open the HTML file in any web browser. Use arrow keys or buttons to navigate slides. Complete the quiz for instant feedback!")

            if notes_buf:
                st.download_button(
                    "📚 Download Study Notes (PDF)",
                    data=notes_buf,
                    file_name=notes_filename,
                    mime="application/pdf",
                    use_container_width=True
                )
