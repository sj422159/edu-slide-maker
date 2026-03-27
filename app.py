import streamlit as st
import os, json, requests, re, time, zipfile
import ast
from urllib.parse import quote_plus
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

# ==========================================
# CONFIGURATION
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

# Professional Color Palette
DARK_BG       = RGBColor(3, 0, 46)
ACCENT_BG     = RGBColor(30, 60, 114)
CARD_BG       = RGBColor(34, 40, 60)
WHITE         = RGBColor(255, 255, 255)
LIGHT_GRAY    = RGBColor(200, 210, 225)
ACCENT_GOLD   = RGBColor(255, 196, 61)
ACCENT_CYAN   = RGBColor(0, 188, 212)
SUBTLE_GRAY   = RGBColor(120, 130, 150)

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)
FONT_TITLE = "Segoe UI"
FONT_BODY  = "Segoe UI"

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

# ==========================================
# GEMINI HELPERS
# ==========================================
GEMINI_MODELS = [
"gemini-flash-latest",
   
    "gemini-3-flash-preview",
     "gemini-2.5-flash",
    "gemini-3.1-flash-lite-preview",
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
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]
        return None

    def _sanitize_json_like(raw):
        sanitized = raw
        sanitized = sanitized.replace('\u201c', '"').replace('\u201d', '"')
        sanitized = sanitized.replace('\u2018', "'").replace('\u2019', "'")
        sanitized = re.sub(r',\s*([}\]])', r'\1', sanitized)  # remove trailing commas
        sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', sanitized)
        return sanitized.strip()

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

    # Last fallback for Python-like list/dict output with single quotes.
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

    # Enforce first slide as title.
    if normalized[0]['type'] != 'title':
        normalized.insert(0, {
            'type': 'title',
            'title': chapter,
            'body': [f"Class {class_num} {subject}"],
            'search_query': '',
            'section_number': None,
        })

    # Enforce diagrams when image mode is enabled.
    if use_images:
        min_diagrams = 8
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
    cleaned = cleaned.replace('“', '"').replace('”', '"')
    cleaned = cleaned.replace('’', "'").replace('•', '-')
    cleaned = cleaned.replace('▸', '-').replace('✦', '*')
    
    # Math/LaTeX cleanup for FPDF compatibility
    cleaned = cleaned.replace('$', '')
    cleaned = cleaned.replace('\\implies', '=>')
    cleaned = cleaned.replace('\\sqrt', '√')
    cleaned = cleaned.replace('\\circ', '°')
    cleaned = cleaned.replace('\\alpha', 'α')
    cleaned = cleaned.replace('\\beta', 'β')
    cleaned = cleaned.replace('\\theta', 'θ')
    cleaned = cleaned.replace('\\pi', 'π')
    cleaned = cleaned.replace('\\mu', 'μ')
    cleaned = cleaned.replace('\\sigma', 'σ')
    cleaned = cleaned.replace('\\omega', 'ω')
    cleaned = cleaned.replace('\\Delta', 'Δ')
    cleaned = cleaned.replace('\\times', '×')
    cleaned = cleaned.replace('\\div', '÷')
    cleaned = cleaned.replace('\\pm', '±')
    cleaned = cleaned.replace('\\neq', '≠')
    cleaned = cleaned.replace('\\leq', '≤')
    cleaned = cleaned.replace('\\geq', '≥')
    cleaned = cleaned.replace('\\approx', '≈')
    cleaned = cleaned.replace('\\sin', 'sin')
    cleaned = cleaned.replace('\\cos', 'cos')
    cleaned = cleaned.replace('\\tan', 'tan')
    cleaned = cleaned.replace('\\cot', 'cot')
    cleaned = cleaned.replace('\\sec', 'sec')
    cleaned = cleaned.replace('\\csc', 'csc')
    cleaned = cleaned.replace('\\log', 'log')
    cleaned = cleaned.replace('\\ln', 'ln')
    
    # Handle fractions simple form
    cleaned = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)', cleaned)
    
    # Clean up excess backslashes and brackets
    cleaned = cleaned.replace('{', '(').replace('}', ')')
    cleaned = cleaned.replace('\\', '')
    
    if not unicode_fonts:
        cleaned = cleaned.encode('latin-1', 'ignore').decode('latin-1')
    return cleaned

def gemini_generate(prompt, response_mime='application/json'):
    """Try each model in GEMINI_MODELS; on rate limit move to the next."""
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
                    break  # move to next model
                else:
                    raise
    raise RuntimeError(f"All Gemini models exhausted. Tried: {', '.join(errors)}")

def gemini_generate_text(prompt):
    """Generate plain text (for notes) with model fallback."""
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
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
def scrape_image(query):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        url = f"https://www.bing.com/images/search?q={quote_plus(query)}&first=1"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', resp.text)
        if not img_urls:
            img_urls = re.findall(r'src2?="(https?://[^"]+\.(?:jpg|jpeg|png|webp))', resp.text)
        for img_url in img_urls[:5]:
            try:
                r = requests.get(img_url, headers=headers, timeout=10)
                if r.status_code == 200 and len(r.content) > 2000:
                    stream = BytesIO(r.content)
                    img = Image.open(stream)
                    if img.format not in ('BMP', 'GIF', 'JPEG', 'PNG', 'TIFF', 'WMF'):
                        buf = BytesIO()
                        img.convert('RGBA').save(buf, format='PNG')
                        buf.seek(0)
                        return buf
                    stream.seek(0)
                    return stream
            except Exception:
                continue
        return None
    except Exception:
        return None

# ==========================================
# SHAPE HELPERS
# ==========================================
def set_slide_bg(slide, color):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color

def add_textbox(slide, left, top, width, height, text, font_size=18,
                color=WHITE, bold=False, alignment=PP_ALIGN.LEFT,
                font_name=FONT_BODY, anchor=MSO_ANCHOR.TOP):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    try:
        tf.paragraphs[0].alignment = alignment
    except Exception:
        pass
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

def add_bullet_list(slide, left, top, width, height, items, font_size=16,
                    color=LIGHT_GRAY, bullet_char="▸"):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for idx, item in enumerate(items):
        if idx == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = f"{bullet_char}  {item}"
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = FONT_BODY
        p.space_after = Pt(6)
    return txBox

def add_accent_line(slide, left, top, width, color=ACCENT_CYAN):
    shape = slide.shapes.add_shape(1, left, top, width, Pt(3))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape

def add_rounded_card(slide, left, top, width, height, fill_color=CARD_BG):
    shape = slide.shapes.add_shape(5, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape

# ==========================================
# LOGO HELPER
# ==========================================
def _lock_shape(shape):
    cNvPicPr = shape._element.find('.//' + qn('p:cNvPicPr'))
    if cNvPicPr is not None:
        locks = cNvPicPr.find(qn('a:picLocks'))
        if locks is None:
            from lxml import etree
            locks = etree.SubElement(cNvPicPr, qn('a:picLocks'))
        locks.set('noSelect', '1')
        locks.set('noMove', '1')
        locks.set('noResize', '1')
        locks.set('noRot', '1')
        locks.set('noChangeAspect', '1')

def add_logo(slide):
    if not os.path.exists(LOGO_PATH):
        return
    pic = slide.shapes.add_picture(LOGO_PATH, Inches(0.3), Inches(0.2), height=Inches(0.5))
    _lock_shape(pic)

# ==========================================
# SLIDE BUILDERS
# ==========================================
def build_title_slide(prs, title, subtitle):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_accent_line(slide, Inches(1.5), Inches(2.5), Inches(10.33))
    add_textbox(slide, Inches(1.5), Inches(2.7), Inches(10.33), Inches(1.5),
                title, font_size=42, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_textbox(slide, Inches(1.5), Inches(4.3), Inches(10.33), Inches(1),
                subtitle, font_size=20, color=WHITE, alignment=PP_ALIGN.CENTER)
    add_accent_line(slide, Inches(1.5), Inches(5.2), Inches(10.33))
    add_logo(slide)

def build_section_slide(prs, section_number, section_title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(1), Inches(2.3), Inches(11.33), Inches(1),
                f"SECTION {section_number}", font_size=18, color=ACCENT_CYAN,
                bold=True, alignment=PP_ALIGN.CENTER)
    add_textbox(slide, Inches(1), Inches(3.1), Inches(11.33), Inches(1.5),
                section_title, font_size=38, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(5), Inches(4.7), Inches(3.33), ACCENT_GOLD)
    add_logo(slide)

def build_content_slide(prs, title, bullets):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(0.5), Inches(0.3), Inches(12.33), Inches(0.8),
                title, font_size=30, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(0.8), Inches(1.2), Inches(11.73))
    add_rounded_card(slide, Inches(0.8), Inches(1.5), Inches(11.73), Inches(5.5))
    if isinstance(bullets, list):
        add_bullet_list(slide, Inches(1.3), Inches(1.8), Inches(10.73), Inches(5),
                        bullets, font_size=18, color=WHITE)
    else:
        lines = [l.strip('- •▸').strip() for l in str(bullets).split('\n') if l.strip()]
        if len(lines) <= 1:
            lines = [s.strip() for s in str(bullets).split('.') if s.strip()]
        add_bullet_list(slide, Inches(1.3), Inches(1.8), Inches(10.73), Inches(5),
                        lines[:10], font_size=18, color=WHITE)
    add_logo(slide)

def build_diagram_slide(prs, title, bullets, search_query):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(0.5), Inches(0.3), Inches(12.33), Inches(0.8),
                title, font_size=28, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(0.8), Inches(1.2), Inches(11.73))
    add_rounded_card(slide, Inches(0.8), Inches(1.5), Inches(5.5), Inches(5.5))
    if isinstance(bullets, list):
        items = bullets
    else:
        items = [l.strip('- •▸').strip() for l in str(bullets).split('\n') if l.strip()]
        if len(items) <= 1:
            items = [s.strip() for s in str(bullets).split('.') if s.strip()]
    add_bullet_list(slide, Inches(1.2), Inches(1.8), Inches(4.8), Inches(5),
                    items[:8], font_size=18, color=WHITE)
    img_stream = scrape_image(search_query)
    if img_stream:
        add_rounded_card(slide, Inches(6.8), Inches(1.5), Inches(6.03), Inches(5.5),
                         fill_color=RGBColor(25, 30, 48))
        slide.shapes.add_picture(img_stream, Inches(7.0), Inches(1.7),
                                 width=Inches(5.6), height=Inches(5.0))
    else:
        add_rounded_card(slide, Inches(6.8), Inches(1.5), Inches(6.03), Inches(5.5),
                         fill_color=RGBColor(25, 30, 48))
        add_textbox(slide, Inches(7.0), Inches(3.5), Inches(5.5), Inches(1),
                    "[Diagram: " + search_query + "]",
                    font_size=16, color=SUBTLE_GRAY, alignment=PP_ALIGN.CENTER)
    add_logo(slide)

def build_summary_slide(prs, title, points):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(0.5), Inches(0.4), Inches(12.33), Inches(0.8),
                title, font_size=32, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(0.8), Inches(1.3), Inches(11.73), ACCENT_GOLD)
    mid = (len(points) + 1) // 2
    left_items = points[:mid]
    right_items = points[mid:]
    add_rounded_card(slide, Inches(0.8), Inches(1.6), Inches(5.6), Inches(5.3))
    add_bullet_list(slide, Inches(1.2), Inches(1.9), Inches(4.9), Inches(4.8),
                    left_items, font_size=18, color=WHITE, bullet_char="✦")
    if right_items:
        add_rounded_card(slide, Inches(6.9), Inches(1.6), Inches(5.6), Inches(5.3))
        add_bullet_list(slide, Inches(7.3), Inches(1.9), Inches(4.9), Inches(4.8),
                        right_items, font_size=18, color=WHITE, bullet_char="✦")
    add_logo(slide)

def build_thank_you_slide(prs, class_num, subject, chapter):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(1), Inches(2.5), Inches(11.33), Inches(1.5),
                "Thank You", font_size=48, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(5), Inches(4.2), Inches(3.33), ACCENT_GOLD)
    add_textbox(slide, Inches(1), Inches(4.5), Inches(11.33), Inches(1),
                f"Class {class_num} {subject} — {chapter}",
                font_size=18, color=WHITE, alignment=PP_ALIGN.CENTER)
    add_logo(slide)

# ==========================================
# PROMPTS
# ==========================================
GEMINI_PROMPT_TEMPLATE = """You are an expert {subject} teacher. Create a structured presentation on "{chapter}" for Class {class_num} students.

Return a JSON array of exactly 30 slide objects. Each slide must have these keys:
- "type": one of "title", "section", "content", "diagram", "summary"
- "title": slide heading
- "body": array of 4-6 concise bullet point strings (not needed for title/section)
- "search_query": a short image search query ONLY for "diagram" type slides (e.g. "labeled diagram of ..."). Leave empty string for other types.
- "section_number": integer, only for "section" type slides

STRUCTURE RULES:
- Slide 1: type "title" (main title slide)
- Then organize into 4-6 logical sections based on the chapter content
- Each section starts with a "section" slide
- Within each section: mix of "content" (text-only explanation) and "diagram" slides (where a labeled diagram is essential)
- Use "diagram" type ONLY for slides that genuinely need a visual. About 8-10 diagram slides total.
- Last slide: type "summary"
- Keep bullet points concise (max 15 words each)

Return ONLY the JSON array, no other text."""

GEMINI_PROMPT_TEXT_ONLY = """You are an expert {subject} teacher. Create a detailed, text-heavy presentation on "{chapter}" for Class {class_num} students.

Return a JSON array of exactly 25-50 slide objects. Each slide must have these keys:
- "type": one of "title", "section", "content", "summary"
- "title": slide heading
- "body": array of 6-8 detailed, informative bullet point strings. Each bullet should be a complete explanation (35 words) that teaches the concept clearly. NOT needed for title/section slides.
- "section_number": integer, only for "section" type slides

STRUCTURE RULES:
- Slide 1: type "title" (main title slide, body should have a one-line subtitle)
- Then organize into 4-6 logical sections based on the chapter content
- Each section starts with a "section" slide
- All other slides are "content" type — NO diagram slides. Every slide must have detailed text.
- Last slide: type "summary" with key revision points
- Make bullet points detailed and educational — students should be able to learn from reading the slides alone
- Cover definitions, processes, examples, comparisons, and important facts
- Include exam-relevant points, mnemonics, and key differences where applicable

Return ONLY the JSON array, no other text."""

# ==========================================
# NOTES PROMPT
# ==========================================
NOTES_THEORY_PROMPT = """You are an expert {subject} teacher writing a COMPLETE, DETAILED study guide on "{chapter}" for Class {class_num} students.

Write thorough theory notes in clean Markdown covering ALL of the following sections — do not skip any:

# {chapter} — Complete Study Guide

## 1. Chapter Overview
(Write a 150-200 word introduction covering the scope, importance, and what will be studied in this chapter.)

## 2. Key Concepts & In-Depth Explanations
(Explain EVERY core concept in this chapter in detail — at least 5-10 concepts. Use sub-headings (###) for each concept. Give real-life examples. Nothing should be left out.)

## 3. Important Definitions & Glossary
(Define ALL key terms in this chapter with bold term names and full explanations. Must have at least 15-20 definitions.)

## 4. Formulas, Laws & Equations
(List every formula, law, rule, or equation. State what each symbol means. Derive where applicable.)

## 5. Solved Examples & Worked Problems
(At least 6-8 fully solved problems or application examples with step-by-step working.)

## 6. Diagrams & Visual Concepts
(Describe at least 6-8 important diagrams clearly. Say what each part shows and why it matters.)

## 7. Comparison Tables
(At least 4-5 markdown tables comparing related concepts, e.g. A vs B, Type 1 vs Type 2.)

## 8. Important Facts, Dates & Statistics
(List notable facts, figures, historical context, or statistics relevant to this chapter.)

## 9. Quick Revision Bullets
(Write 25-30 short, exam-ready bullet points covering the most important points in the chapter.)

## 10. Exam Tips & Common Mistakes to Avoid
(What examiners expect. Common marking errors students make. How to score full marks.)

## 11. Chapter Summary
(A concise 150-word paragraph summarising the entire chapter — useful for last-minute revision.)

Rules:
- Use **bold** for all key terms on first use.
- Use markdown tables for all comparisons.
- Do NOT truncate any section — be exhaustive.
- Return Markdown only."""

NOTES_THEORY_PROMPT_WITH_DIAGRAMS = """You are an expert {subject} teacher writing a COMPLETE, DETAILED study guide on "{chapter}" for Class {class_num} students.

Write thorough theory notes in clean Markdown covering ALL of the following sections — do not skip any:

# {chapter} — Complete Study Guide

## 1. Chapter Overview
(Write a 150-200 word introduction covering the scope, importance, and what will be studied in this chapter.)

## 2. Key Concepts & In-Depth Explanations
(Explain EVERY core concept in detail with sub-headings (###) for each concept. Give real-life examples.)

## 3. Important Definitions & Glossary
(Define ALL key terms with bold term names and full explanations. Must have at least 15-20 definitions.)

## 4. Formulas, Laws & Equations
(Every formula, law, rule, or equation with symbol meanings.)

## 5. Solved Examples & Worked Problems
(At least 6-8 fully worked problems with step-by-step solutions.)

## 6. Diagram Guide
(For each diagram write:
- **Diagram:** <Title>
- **Description:** What it shows in detail — label all parts.
- **Key Insight:** Why it matters.
List at least 8 diagrams.)

## 7. Comparison Tables
(At least 4-5 markdown tables comparing related concepts.)

## 8. Important Facts, Dates & Statistics
(Notable facts, figures, historical context, or statistics.)

## 9. Quick Revision Bullets
(25-30 short, exam-ready bullet points.)

## 10. Exam Tips & Common Mistakes to Avoid
(What examiners expect. Common student errors. How to score full marks.)

## 11. Chapter Summary
(A concise 150-word paragraph summarising the entire chapter.)

Rules:
- Use **bold** for all key terms on first use.
- Use markdown tables for all comparisons.
- Do NOT truncate any section — be exhaustive.
- Return Markdown only."""

NOTES_QUESTIONS_PROMPT = """You are an expert {subject} teacher. Generate a COMPLETE question bank for "{chapter}" for Class {class_num} students.

Return clean Markdown with ALL of the following sections. Do NOT skip or shorten any section.

---

# Question Bank — {chapter}

## Section A: Multiple Choice Questions (MCQs)
(Write 20 MCQs. 4 options each labelled (a) (b) (c) (d). Mark the correct answer with reasoning.)

Use this EXACT format for every question:

**Q1.** Question text here
(a) Option A  (b) Option B  (c) Option C  (d) Option D
**Answer:** (x) — One-line explanation.

---

## Section B: Very Short Answer Questions (1 Mark)
(Write 15 questions. Each answer should be 1-2 sentences.)

Format:
**Q1.** Question
**Ans:** Answer

---

## Section C: Short Answer Questions (2–3 Marks)
(Write 15 questions. Each answer should be 4-6 sentences with key terms.)

Format:
**Q1.** Question
**Ans:** Detailed answer

---

## Section D: Long Answer / Essay Questions (5 Marks)
(Write 10 questions. Each answer must be a thorough explanation with bullet points, sub-headings, or diagrams described in text. Examiner-ready quality.)

Format:
**Q1.** Question
**Ans:**
Full detailed answer here...

---

## Section E: Previous Year Questions (PYQs)
(Write 20 PYQ-style questions based on actual board exam patterns. Include board name and year in brackets. Provide full model answers.)

Format:
**PYQ Q1.** [CBSE 2023 — 3 Marks] Question text
**Ans:** Model answer

---

## Section F: Assertion–Reason Questions
(Write 10 assertion-reason pairs. For each, provide the standard instruction:
"Choose: (A) Both A and R are true and R is the correct explanation of A. (B) Both A and R are true but R is NOT the correct explanation. (C) A is true but R is false. (D) A is false but R is true."
Then state the correct option with a brief reason.)

Format:
**Q1.**
**Assertion (A):** Statement
**Reason (R):** Statement
**Answer:** Option (X) — explanation

---

## Section G: Case Study / Source-Based Questions
(Write 4 case studies. Each case study is a short passage (3-5 sentences) followed by 4 sub-questions with answers.)

Format:
**Case Study 1:**
Passage text here...
**(i)** Question  **Ans:** Answer
**(ii)** Question  **Ans:** Answer
**(iii)** Question  **Ans:** Answer
**(iv)** Question  **Ans:** Answer

---

## Section H: Fill in the Blanks
(Write 20 fill-in-the-blank statements. Answers in brackets below each.)

Format:
**Q1.** The process of _______ occurs when...
**Ans:** [keyword]

---

## Section I: True or False (with Justification)
(Write 15 statements. State True or False and give a 1-sentence justification.)

Format:
**Q1.** Statement here.
**Ans:** True/False — Justification.

---

## Section J: Match the Following
(Write 3 matching exercises with Column A (5 items) and Column B (5 items). Provide answers.)

---

## Section K: One-Word / One-Line Answers
(Write 15 questions where the answer is a single word or phrase.)

---

## Section L: Diagram-Based Questions
(Write 8 questions that ask students to draw and label a diagram, or interpret a described diagram. Provide full model answers including what the diagram should show.)

---

Rules:
- ALL answers must be exam-quality and complete. Never write "answer not provided".
- Use the exact format specified for each section.
- Return Markdown ONLY — no preamble, no explanation."""

# Kept as aliases so both prompt names resolve.
NOTES_PROMPT = NOTES_THEORY_PROMPT
NOTES_PROMPT_WITH_IMAGES = NOTES_THEORY_PROMPT_WITH_DIAGRAMS

# ==========================================
# PPT GENERATION
# ==========================================
def generate_ppt(class_num, subject, chapter, use_images):
    if use_images:
        prompt = GEMINI_PROMPT_TEMPLATE.format(
            class_num=class_num, subject=subject, chapter=chapter
        )
    else:
        prompt = GEMINI_PROMPT_TEXT_ONLY.format(
            class_num=class_num, subject=subject, chapter=chapter
        )

    response = gemini_generate(prompt)
    try:
        slides_data = parse_json_response(response.text)
    except ValueError:
        try:
            slides_data = repair_slides_json(response.text)
        except Exception as repair_err:
            raise ValueError(
                f"Could not parse slide JSON from model output. Repair failed: {repair_err}"
            )

    slides_data = normalize_slides_data(slides_data, class_num, subject, chapter, use_images)

    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    slide_num = 0

    for data in slides_data:
        slide_type = data.get('type', 'content')
        title = data.get('title', '')
        body = data.get('body', [])
        if slide_type in ('content', 'diagram', 'summary') and not body:
            body = [f"Key points about {title}"]
        query = data.get('search_query', '')
        sec_num = data.get('section_number', '')

        if slide_type == 'title':
            subtitle = body[0] if isinstance(body, list) and body else f"Class {class_num} {subject}"
            build_title_slide(prs, title, subtitle)

        elif slide_type == 'section':
            build_section_slide(prs, sec_num, title)

        elif slide_type == 'diagram':
            slide_num += 1
            if use_images:
                build_diagram_slide(prs, title, body, query)
            else:
                build_content_slide(prs, title, body)

        elif slide_type == 'summary':
            items = body if isinstance(body, list) else [body]
            build_summary_slide(prs, title, items)

        else:
            slide_num += 1
            build_content_slide(prs, title, body)

    build_thank_you_slide(prs, class_num, subject, chapter)

    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)

    safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
    filename = f"Class{class_num}_{subject}_{safe_name}.pptx"
    return buf, filename, slides_data

def generate_notes(class_num, subject, chapter, use_images=False, slides_data=None):
    # --- Call 1: Theory Notes ---
    theory_tmpl = NOTES_THEORY_PROMPT_WITH_DIAGRAMS if use_images else NOTES_THEORY_PROMPT
    theory_text = gemini_generate_text(theory_tmpl.format(
        class_num=class_num, subject=subject, chapter=chapter
    ))

    # --- Call 2: Question Bank ---
    questions_text = gemini_generate_text(NOTES_QUESTIONS_PROMPT.format(
        class_num=class_num, subject=subject, chapter=chapter
    ))

    # Combine both into a single markdown document
    notes_text = theory_text.strip() + "\n\n---\n\n" + questions_text.strip()
    safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
    filename = f"Class{class_num}_{subject}_{safe_name}_Notes.pdf"

    # Colors
    NAVY = (3, 0, 46)
    GOLD = (255, 196, 61)
    CYAN = (0, 188, 212)
    DARK_CARD = (20, 24, 45)
    WHITE_TEXT = (240, 240, 250)
    BODY_TEXT = (210, 215, 230)
    BULLET_COL = (150, 160, 180)

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
            # Only draw background/header for content pages (page > 1)
            if self.page_no() > 1:
                self.set_fill_color(*NAVY)
                self.rect(0, 0, self.w, self.h, 'F')
                self.set_fill_color(*DARK_CARD)
                self.rect(0, 0, self.w, 14, 'F')
                self.set_font(font_family, 'B', 8)
                self.set_text_color(*CYAN)
                self.set_xy(10, 4)
                # Align text to right to make room for logo on left
                self.cell(0, 6, normalize_pdf_text(f"Class {class_num}  |  {subject}  |  {chapter}", unicode_fonts), align='R')
                if os.path.exists(LOGO_PATH):
                    self.image(LOGO_PATH, x=10, y=2, h=10)
                self.set_draw_color(*GOLD)
                self.set_line_width(0.4)
                self.line(10, 14, self.w - 10, 14)
                # Reset Y below header
                self.set_y(20)

    pdf = NotesPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    win_font = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')

    if unicode_fonts:
        try:
            pdf.add_font('Arial', '', os.path.join(win_font, 'arial.ttf'), uni=True)
            pdf.add_font('Arial', 'B', os.path.join(win_font, 'arialbd.ttf'), uni=True)
            pdf.add_font('Arial', 'I', os.path.join(win_font, 'ariali.ttf'), uni=True)
        except Exception:
            unicode_fonts = False
            font_family = 'Helvetica'

    # --- Cover Page ---
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')

    # Logo on cover (top-left)
    if os.path.exists(LOGO_PATH):
        pdf.image(LOGO_PATH, x=15, y=10, w=30)

    # Gold accent line
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(1)
    pdf.line(40, 85, pdf.w - 40, 85)

    # Title
    pdf.set_y(95)
    pdf.set_font(font_family, 'B', 28)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 14, normalize_pdf_text(chapter, unicode_fonts), align='C')

    # Subtitle
    pdf.ln(5)
    pdf.set_font(font_family, '', 16)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.multi_cell(0, 9, normalize_pdf_text(f"Class {class_num}  |  {subject}", unicode_fonts), align='C')

    # Bottom accent line
    pdf.set_draw_color(*CYAN)
    pdf.line(40, pdf.h - 40, pdf.w - 40, pdf.h - 40)

    pdf.set_font(font_family, 'I', 10)
    pdf.set_text_color(*BULLET_COL)
    pdf.set_y(pdf.h - 35)
    pdf.multi_cell(0, 6, normalize_pdf_text("Complete Study Guide", unicode_fonts), align='C')

    # --- Content Pages ---
    def new_content_page():
        pdf.add_page()

    new_content_page()
    in_table = False

    for line in notes_text.split('\n'):
        stripped = line.strip()

        # Check if we need a new page (leave room for content)
        if pdf.get_y() > pdf.h - 30:
            new_content_page()

        if stripped.startswith('### '):
            # Sub-sub heading - cyan accent
            pdf.ln(4)
            pdf.set_font(font_family, 'B', 13)
            pdf.set_text_color(*CYAN)
            text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[4:]), unicode_fonts)
            pdf.multi_cell(0, 7, text)
            # Thin cyan underline
            y = pdf.get_y()
            pdf.set_draw_color(*CYAN)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, y, pdf.l_margin + 50, y)
            pdf.ln(3)

        elif stripped.startswith('## '):
            # Section heading - gold with card background
            pdf.ln(6)
            y = pdf.get_y()
            pdf.set_fill_color(*DARK_CARD)
            pdf.rect(pdf.l_margin, y, pdf.w - pdf.l_margin - pdf.r_margin, 12, 'F')
            # Gold left accent bar
            pdf.set_fill_color(*GOLD)
            pdf.rect(pdf.l_margin, y, 3, 12, 'F')
            pdf.set_xy(pdf.l_margin + 6, y + 1)
            pdf.set_font(font_family, 'B', 15)
            pdf.set_text_color(*GOLD)
            text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[3:]), unicode_fonts)
            pdf.cell(0, 10, text)
            pdf.ln(16)

        elif stripped.startswith('# '):
            # Main heading - large gold
            pdf.ln(8)
            pdf.set_font(font_family, 'B', 20)
            pdf.set_text_color(*GOLD)
            text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[2:]), unicode_fonts)
            pdf.multi_cell(0, 11, text, align='C')
            # Gold line
            y = pdf.get_y() + 1
            pdf.set_draw_color(*GOLD)
            pdf.set_line_width(0.5)
            pdf.line(pdf.w * 0.25, y, pdf.w * 0.75, y)
            pdf.ln(5)

        elif stripped == '---':
            # Horizontal section divider
            pdf.ln(4)
            y = pdf.get_y()
            pdf.set_draw_color(*GOLD)
            pdf.set_line_width(0.5)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(5)

        elif re.match(r'^\*\*(PYQ\s+)?Q\d+\.\*\*', stripped):
            # Question line — gold bold label + question text
            pdf.ln(3)
            if pdf.get_y() > pdf.h - 35:
                new_content_page()
            # Strip bold markers and extract question number + body
            raw = re.sub(r'\*\*(.+?)\*\*', r'\1', stripped)
            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, 'B', 11)
            pdf.set_text_color(*GOLD)

            qnum_match = re.match(r'((PYQ\s+)?Q\d+\.)\s*(.*)', raw, re.DOTALL)
            if qnum_match:
                qnum = normalize_pdf_text(qnum_match.group(1), unicode_fonts)
                qbody = normalize_pdf_text(qnum_match.group(3), unicode_fonts)
                num_w = pdf.get_string_width(qnum + ' ') + 2
                pdf.cell(num_w, 7, qnum)
                pdf.set_font(font_family, '', 11)
                pdf.set_text_color(*WHITE_TEXT)
                pdf.multi_cell(0, 7, qbody)
            else:
                pdf.multi_cell(0, 7, normalize_pdf_text(raw, unicode_fonts))
            pdf.ln(1)

        elif re.match(r'^\([a-d]\)', stripped):
            # MCQ option line — indented with cyan option letter
            opt_match = re.match(r'^(\([a-d]\))\s*(.*)', stripped)
            if opt_match:
                letter = opt_match.group(1)
                opt_text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', opt_match.group(2)), unicode_fonts)
                pdf.set_x(pdf.l_margin + 12)
                pdf.set_font(font_family, 'B', 10)
                pdf.set_text_color(*CYAN)
                pdf.cell(10, 6, letter)
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*BODY_TEXT)
                pdf.multi_cell(0, 6, opt_text)
            else:
                pdf.set_x(pdf.l_margin + 12)
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*BODY_TEXT)
                pdf.multi_cell(0, 6, normalize_pdf_text(stripped, unicode_fonts))
            pdf.ln(0.5)

        elif re.match(r'^\*\*Ans(wer)?:', stripped, re.IGNORECASE):
            # Answer line — teal card background
            pdf.ln(1)
            if pdf.get_y() > pdf.h - 30:
                new_content_page()
            ans_raw = re.sub(r'^\*\*Ans(wer)?:\*\*\s*', '', stripped, flags=re.IGNORECASE)
            clean_ans = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', ans_raw), unicode_fonts)
            y = pdf.get_y()
            card_h = 8
            # Light teal card behind answer
            pdf.set_fill_color(0, 60, 70)
            pdf.rect(pdf.l_margin, y, pdf.w - pdf.l_margin - pdf.r_margin, card_h, 'F')
            pdf.set_xy(pdf.l_margin + 3, y + 0.5)
            pdf.set_font(font_family, 'B', 10)
            pdf.set_text_color(*CYAN)
            pdf.cell(16, 7, 'Ans:')
            pdf.set_font(font_family, '', 10)
            pdf.set_text_color(200, 240, 240)
            pdf.multi_cell(0, 7, clean_ans)
            pdf.ln(2)

        elif re.match(r'^\*\*\(i+v?|^\*\*\(vi*\)', stripped):
            # Sub-question (i) (ii) (iii) style — indented
            raw = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped), unicode_fonts)
            pdf.set_x(pdf.l_margin + 8)
            pdf.set_font(font_family, 'B', 10)
            pdf.set_text_color(*CYAN)
            pdf.multi_cell(0, 6, raw)
            pdf.ln(0.5)

        elif stripped.startswith('- ') or stripped.startswith('* '):
            # Bullet point with cyan dot
            pdf.set_x(pdf.l_margin + 5)
            bullet_text = stripped[2:]
            clean_text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', bullet_text), unicode_fonts)
            pdf.set_font(font_family, 'B', 11)
            pdf.set_text_color(*CYAN)
            pdf.cell(5, 6, '-' if not unicode_fonts else chr(9679))
            pdf.set_font(font_family, '', 11)
            pdf.set_text_color(*BODY_TEXT)
            pdf.multi_cell(0, 6, '  ' + clean_text)
            pdf.ln(1.5)

        elif stripped.startswith('|') and '|' in stripped[1:]:
            # Table row with styled cells
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if all(set(c) <= {'-', ':', ' '} for c in cells):
                continue
            col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / max(len(cells), 1)
            if not in_table:
                # First row = header
                pdf.set_fill_color(*DARK_CARD)
                pdf.set_font(font_family, 'B', 10)
                pdf.set_text_color(*GOLD)
                for cell in cells:
                    clean = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', cell), unicode_fonts)
                    pdf.cell(col_w, 8, clean, border=1, fill=True, align='C')
                pdf.ln()
                in_table = True
            else:
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*BODY_TEXT)
                pdf.set_fill_color(15, 18, 38)
                for cell in cells:
                    clean = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', cell), unicode_fonts)
                    pdf.cell(col_w, 7, clean, border=1, fill=True, align='C')
                pdf.ln()

        elif stripped == '':
            in_table = False
            pdf.ln(3)

        else:
            in_table = False
            pdf.set_font(font_family, '', 11)
            pdf.set_text_color(*BODY_TEXT)
            clean_text = normalize_pdf_text(re.sub(r'\*\*(.+?)\*\*', r'\1', stripped), unicode_fonts)
            pdf.multi_cell(0, 6, clean_text)
            pdf.ln(1.5)

    # Append diagram image pages when image mode is selected.
    if use_images and isinstance(slides_data, list):
        diagram_slides = [s for s in slides_data if s.get('type') == 'diagram'][:10]
        if diagram_slides:
            new_content_page()
            pdf.set_font(font_family, 'B', 18)
            pdf.set_text_color(*GOLD)
            pdf.multi_cell(0, 10, normalize_pdf_text("Diagram Reference Pages", unicode_fonts), align='C')
            pdf.ln(3)

        for idx, s in enumerate(diagram_slides, 1):
            if pdf.get_y() > pdf.h - 120:
                new_content_page()
            title = normalize_pdf_text(str(s.get('title', f"Diagram {idx}")), unicode_fonts)
            query = str(s.get('search_query', '') or f"labeled diagram of {title}")

            pdf.set_font(font_family, 'B', 12)
            pdf.set_text_color(*CYAN)
            pdf.multi_cell(0, 7, normalize_pdf_text(f"{idx}. {title}", unicode_fonts))

            img_stream = scrape_image(query)
            if img_stream:
                img_bytes = img_stream.getvalue()
                tmp_name = f"_tmp_note_img_{idx}.png"
                with open(tmp_name, 'wb') as f:
                    f.write(img_bytes)
                try:
                    x = pdf.l_margin
                    y = pdf.get_y() + 2
                    w = min(160, pdf.w - pdf.l_margin - pdf.r_margin)
                    pdf.image(tmp_name, x=x, y=y, w=w)
                    pdf.set_y(y + 90)
                except Exception:
                    pdf.set_font(font_family, '', 10)
                    pdf.set_text_color(*BULLET_COL)
                    pdf.multi_cell(0, 6, normalize_pdf_text(f"[Could not render image for query: {query}]", unicode_fonts))
                finally:
                    try:
                        os.remove(tmp_name)
                    except Exception:
                        pass
            else:
                pdf.set_font(font_family, '', 10)
                pdf.set_text_color(*BULLET_COL)
                pdf.multi_cell(0, 6, normalize_pdf_text(f"[No image found for query: {query}]", unicode_fonts))

            pdf.ln(4)

    # --- Footer on last page ---
    pdf.ln(10)
    y = pdf.get_y()
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)
    pdf.set_font(font_family, 'I', 9)
    pdf.set_text_color(*BULLET_COL)
    pdf.multi_cell(0, 5, normalize_pdf_text("Complete Study Guide", unicode_fonts), align='C')

    buf = BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf, filename

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="AI Presentation Maker", page_icon="📚", layout="centered")

st.markdown("""
<style>
    .stApp { background-color: #03002e; }
    h1, h2, h3 { color: #ffc43d; }
    .stMarkdown p { color: #c8d2e1; }
</style>
""", unsafe_allow_html=True)

# Logo at top
if os.path.exists(LOGO_PATH):
    st.image(LOGO_PATH, width=200)

st.title("📚 AI Education Generator")
st.markdown("Generate professional 30-slide presentations OR comprehensive study notes powered by AI")

st.divider()

col1, col2 = st.columns(2)
with col1:
    class_num = st.text_input("Class", placeholder="e.g. 10")
    subject = st.text_input("Subject", placeholder="e.g. Biology")
with col2:
    chapter = st.text_input("Chapter", placeholder="e.g. Human Life Processes")
    output_types = st.multiselect(
        "What would you like to generate?", 
        ["Presentation (PPTX)", "Study Notes (PDF)"],
        default=["Presentation (PPTX)"]
    )
    use_images = st.checkbox("Include images/diagrams", value=False)

st.divider()

button_label = "🚀 Generate Content"
if st.button(button_label, type="primary", use_container_width=True):
    if not class_num or not subject or not chapter:
        st.error("Please fill in all fields.")
    elif not output_types:
        st.error("Please select at least one output type to generate.")
    elif not API_KEY:
        st.error("GEMINI_API_KEY environment variable not set.")
    else:
        with st.status("Generating your content...", expanded=True) as status:
            ppt_buf, ppt_filename, slides_data = None, None, None
            notes_buf, notes_filename = None, None
            
            if "Presentation (PPTX)" in output_types:
                st.write("🤖 Generating 30-slide presentation...")
                try:
                    ppt_buf, ppt_filename, slides_data = generate_ppt(class_num, subject, chapter, use_images)
                except Exception as e:
                    status.update(label="❌ PPT generation failed", state="error")
                    st.error(f"PPT Error: {e}")
                    st.stop()
                    
            if "Study Notes (PDF)" in output_types:
                st.write("📝 Generating study notes...")
                try:
                    slides_data_for_notes = slides_data
                    if use_images and slides_data_for_notes is None:
                        st.write("🤖 Finding relevant diagrams...")
                        prompt = GEMINI_PROMPT_TEMPLATE.format(
                            class_num=class_num, subject=subject, chapter=chapter
                        )
                        try:
                            response = gemini_generate(prompt)
                            parsed_json = parse_json_response(response.text)
                            slides_data_for_notes = normalize_slides_data(parsed_json, class_num, subject, chapter, use_images=True)
                        except Exception as ignore_err:
                            pass

                    notes_buf, notes_filename = generate_notes(
                        class_num, subject, chapter, use_images=use_images, slides_data=slides_data_for_notes
                    )
                except Exception as e:
                    status.update(label="❌ Notes generation failed", state="error")
                    st.error(f"Notes Error: {e}")
                    st.stop()
                    
            status.update(label="✅ Content ready!", state="complete")
        
        if "Presentation (PPTX)" in output_types and "Study Notes (PDF)" in output_types:
            st.success("Your presentation and notes are ready!")

            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(ppt_filename, ppt_buf.getvalue())
                zf.writestr(notes_filename, notes_buf.getvalue())
            zip_buf.seek(0)

            safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
            zip_name = f"Class{class_num}_{subject}_{safe_name}.zip"

            st.download_button(
                label="📥 Download Presentation & Notes (ZIP)",
                data=zip_buf,
                file_name=zip_name,
                mime="application/zip",
                use_container_width=True
            )
        elif "Presentation (PPTX)" in output_types:
            st.success("Your presentation is ready!")
            st.download_button(
                label="📥 Download Presentation",
                data=ppt_buf,
                file_name=ppt_filename,
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                use_container_width=True
            )
        elif "Study Notes (PDF)" in output_types:
            st.success("Your study notes are ready!")
            st.download_button(
                label="📥 Download Notes",
                data=notes_buf,
                file_name=notes_filename,
                mime="application/pdf",
                use_container_width=True
            )
