"""
Layout optimization system for modern presentations.
Handles intelligent layout detection and PPT text overflow management.
"""
import re
from pptx.util import Pt, Inches

# ==========================================
# LAYOUT DETECTION
# ==========================================
def detect_best_layout(slide_title, bullets):
    """
    Intelligently detect the best layout for a slide based on content.
    
    Returns:
        'flowchart': Linear progression/steps
        'mind-map': Hierarchical/branching concepts
        'timeline': Chronological/sequential events
        'grid': Related/categorized concepts
    """
    
    # Analyze content patterns
    has_steps = any(re.search(r'step|stage|phase|process|flow|then|next', b, re.I) for b in bullets)
    has_hierarchy = any(re.search(r'type|category|kind|parent|child|sub|main', b, re.I) for b in bullets)
    has_time = any(re.search(r'time|date|period|year|era|then|before|after|sequence', b, re.I) for b in bullets)
    has_relate = any(re.search(r'and|with|relate|connect|link|associate', b, re.I) for b in bullets)
    
    title_lower = slide_title.lower()
    has_process_title = any(word in title_lower for word in ['process', 'flow', 'steps', 'journey'])
    has_hierarchy_title = any(word in title_lower for word in ['types', 'categories', 'structure', 'hierarchy'])
    has_time_title = any(word in title_lower for word in ['history', 'timeline', 'evolution', 'progression'])
    
    num_bullets = len(bullets)
    
    # Decision logic
    if has_process_title or (has_steps and num_bullets <= 5):
        return 'flowchart'
    elif has_hierarchy_title or has_hierarchy:
        return 'mind-map'
    elif has_time_title or has_time:
        return 'timeline'
    else:
        return 'grid'  # Default for general topics


def estimate_text_height(text, font_size_pt, width_inches, font_name='Calibri'):
    """
    Rough estimate of text height in inches.
    Used to detect PPT text overflow.
    """
    # Approximate chars per line based on font size
    chars_per_line = max(10, int(width_inches * 25 - font_size_pt))
    
    # Split into lines
    lines = []
    for paragraph in text.split('\n'):
        current = ""
        for word in paragraph.split():
            if len(current) + len(word) + 1 <= chars_per_line:
                current += word + " "
            else:
                if current:
                    lines.append(current)
                current = word + " "
        if current:
            lines.append(current)
    
    # Estimate height (roughly 1.2x font size per line + margins)
    line_height = font_size_pt * 1.2 / 72  # Convert to inches
    estimated_height = len(lines) * line_height + 0.5  # Add margin
    
    return estimated_height, len(lines)


def should_split_slide(text_content, font_size_pt, max_height_inches=5.5):
    """
    Determine if content should be split across multiple slides.
    
    Returns:
        (should_split: bool, estimated_height: float)
    """
    estimated_height, line_count = estimate_text_height(
        text_content, 
        font_size_pt, 
        width_inches=5.5
    )
    
    return estimated_height > max_height_inches, estimated_height, line_count


def split_bullets_intelligently(bullets, target_per_slide=4):
    """
    Split bullets into groups for multiple slides while keeping related content together.
    
    Returns:
        List of bullet groups (each group is a list of bullets)
    """
    if len(bullets) <= target_per_slide:
        return [bullets]
    
    groups = []
    current_group = []
    
    for bullet in bullets:
        current_group.append(bullet)
        if len(current_group) >= target_per_slide:
            groups.append(current_group)
            current_group = []
    
    if current_group:
        groups.append(current_group)
    
    return groups


def calculate_optimal_font_size(text_content, base_size_pt=24, min_size_pt=12):
    """
    Calculate optimal font size to fit content without overflow.
    
    Returns:
        Optimal font size in points
    """
    # Estimate height at base size
    estimated_height, _ = estimate_text_height(text_content, base_size_pt, 5.5)
    
    if estimated_height <= 5.5:
        return base_size_pt
    
    # Reduce font size proportionally
    reduction_factor = 5.5 / estimated_height
    optimal_size = max(min_size_pt, base_size_pt * reduction_factor)
    
    return int(optimal_size)
