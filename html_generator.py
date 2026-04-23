import os, json, re, time
import random
from urllib.parse import quote_plus
from dotenv import load_dotenv
from google import genai
from google.genai import types
from io import BytesIO
import base64
import requests
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from mistralai.client import Mistral

# Load environment variables from .env file
load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

MISTRAL_API_KEY = os.getenv('Mistral_Api')
mistral_client = Mistral(api_key=MISTRAL_API_KEY)

# Image cache to avoid repeated fetches
IMAGE_CACHE = {}
IMAGE_CACHE_LOCK = threading.Lock()

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

MISTRAL_MODELS = [
    "mistral-large-latest",
    "mistral-medium-latest",
]

# ==========================================
# IMAGE PLACEMENT LOGIC
# ==========================================
def get_random_image_placement():
    """Return a random image placement: 'left', 'right', or 'bottom'.
    Slightly favors left/right (60%) over bottom (40%) for better engagement."""
    placement = random.choices(['left', 'right', 'bottom'], weights=[30, 30, 40], k=1)[0]
    return placement

# ==========================================
# GEMINI HELPERS
# ==========================================
def parse_json_response(text):
    """Parse JSON from Gemini response."""
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\[.*\]|\{.*\}', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from response:\n{text[:500]}")


def gemini_generate_text(prompt):
    """Generate text response from Gemini."""
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                resp = gemini_client.models.generate_content(model=model, contents=prompt)
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


def gemini_generate_json(prompt):
    """Generate JSON response from Gemini."""
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                return gemini_client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type='application/json')
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


def mistral_generate_text(prompt):
    """Generate text response from Mistral."""
    for model in MISTRAL_MODELS:
        for attempt in range(3):
            try:
                response = mistral_client.chat.complete(
                    model=model,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.choices[0].message.content
            except Exception as e:
                err = str(e)
                if '429' in err or 'rate' in err.lower():
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))
                        continue
                else:
                    raise
    raise RuntimeError("All Mistral models exhausted")


def mistral_generate_json(prompt):
    """Generate JSON response from Mistral."""
    for model in MISTRAL_MODELS:
        for attempt in range(3):
            try:
                response = mistral_client.chat.complete(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}
                )
                return response.choices[0].message.content
            except Exception as e:
                err = str(e)
                if '429' in err or 'rate' in err.lower():
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))
                        continue
                else:
                    raise
    raise RuntimeError("All Mistral models exhausted")


# ==========================================
# IMAGE FETCHING
# ==========================================
def simplify_visual_description(description):
    """Convert detailed visual description into 2-3 word search-friendly terms.
    
    Examples:
    - "3D computer rendering of crystal lattice structure with magnifying glass" 
      → "crystal lattice"
    - "Tug-of-war analogy between magnet pulling together and flame shaking apart"
      → "magnet atoms"
    - "Bulleted list with icons of stacked spheres, measuring scale, and math formula"
      → "atoms measurement"
    """
    # Extract keywords from description
    # Remove common non-searchable words
    description = re.sub(r'\b(with|from|and|the|that|this|of|for|by|to|or|flat-design|computer rendering|analogy|bulleted list|icons|showing|depicting|representing)\b', ' ', description, flags=re.IGNORECASE)
    
    # Keep only meaningful words (> 3 chars)
    words = description.split()
    words = [w.lower() for w in words if len(w) > 3 and w.isalpha()]
    
    # Remove duplicate/similar words
    seen = set()
    unique_words = []
    for w in words:
        if w not in seen:
            unique_words.append(w)
            seen.add(w)
    
    # Return top 2-3 most meaningful keywords
    result = ' '.join(unique_words[:2]) if unique_words else description.strip()[:20]
    return result if result else "presentation"


def generate_image_with_gemini_base64(query):
    """Generate image with Gemini and convert to base64 for HTML embedding."""
    try:
        print(f"  🎨 Generating image with Gemini: {query[:35]}...")
        
        # Create a refined prompt for better results
        refined_prompt = f"Create a clear, professional, educational diagram or illustration of: {query}. Style: clean, simple, high-quality, suitable for students."
        
        # Try to generate image
        try:
            print(f"    📤 Calling gemini-3.1-flash-image-preview model...")
            response = gemini_client.models.generate_images(
                model="gemini-3.1-flash-image-preview",
                prompt=refined_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    safety_filter_level="block_only_high",
                    aspect_ratio="1:1",
                )
            )
            print(f"    ✓ Model responded successfully")
        except Exception as e:
            err_msg = str(e)
            print(f"    ✗ Model error: {err_msg[:100]}")
            # Only fallback if model truly doesn't exist
            if 'not found' in err_msg.lower() or 'invalid model' in err_msg.lower():
                print(f"    ℹ️ Model unavailable, trying fallback...")
                return generate_fallback_image_base64(query)
            # Otherwise re-raise to log the real error
            raise
        
        # Extract image data from response
        if response and hasattr(response, 'generated_images') and response.generated_images:
            img_obj = response.generated_images[0]
            
            # Try to get the image URL from various possible locations
            img_url = None
            if hasattr(img_obj, 'image'):
                if hasattr(img_obj.image, 'display_url') and img_obj.image.display_url:
                    img_url = img_obj.image.display_url
                elif hasattr(img_obj.image, 'gcs_uri') and img_obj.image.gcs_uri:
                    img_url = img_obj.image.gcs_uri
            elif hasattr(img_obj, 'display_url') and img_obj.display_url:
                img_url = img_obj.display_url
            elif hasattr(img_obj, 'gcs_uri') and img_obj.gcs_uri:
                img_url = img_obj.gcs_uri
            
            if img_url:
                try:
                    # Download and convert to base64
                    print(f"    📥 Downloading generated image from {img_url[:50]}...")
                    img_resp = requests.get(img_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
                    if img_resp.status_code == 200:
                        # Convert to base64 JPEG
                        img = Image.open(BytesIO(img_resp.content))
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        # Resize for optimal HTML display
                        img.thumbnail((400, 300), Image.Resampling.LANCZOS)
                        
                        img_byte_arr = BytesIO()
                        img.save(img_byte_arr, format='JPEG', quality=85)
                        img_byte_arr.seek(0)
                        img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
                        
                        base64_data = f"data:image/jpeg;base64,{img_base64}"
                        
                        # Cache the result
                        with IMAGE_CACHE_LOCK:
                            IMAGE_CACHE[query] = base64_data
                        
                        print(f"    ✓ Image generated and converted to base64 ({len(base64_data)} chars)")
                        return base64_data
                    else:
                        print(f"    ✗ Failed to download image: HTTP {img_resp.status_code}")
                except Exception as e:
                    print(f"    ✗ Download error: {str(e)[:50]}")
                    raise
            else:
                print(f"    ✗ No image URL in response - response structure: {type(img_obj)}")
                raise ValueError("No image URL in response")
        else:
            print(f"    ✗ No generated images in response")
            raise ValueError("No generated_images in response")
            
    except Exception as e:
        print(f"  ⚠️ Gemini generation error: {str(e)[:100]}")
        return generate_fallback_image_base64(query)


def generate_fallback_image_base64(query):
    """Generate a simple colored placeholder image as fallback."""
    try:
        print(f"    🎨 Creating fallback placeholder...")
        # Create a simple colored rectangle with text
        from PIL import ImageDraw, ImageFont
        
        img = Image.new('RGB', (400, 300), color=(220, 240, 255))  # Light blue
        draw = ImageDraw.Draw(img)
        
        # Add a border
        border_color = (100, 150, 200)
        draw.rectangle([5, 5, 395, 295], outline=border_color, width=3)
        
        # Add centered text
        text = "📊 Diagram"
        try:
            # Try to use a default font with size
            font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        # Calculate text position for centering
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (400 - text_width) // 2
        y = (300 - text_height) // 2
        
        draw.text((x, y), text, fill=(50, 100, 150), font=font)
        
        # Convert to base64
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=85)
        img_byte_arr.seek(0)
        img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
        
        base64_data = f"data:image/jpeg;base64,{img_base64}"
        print(f"    ✓ Fallback placeholder created")
        return base64_data
        
    except Exception as e:
        print(f"    ✗ Fallback creation failed: {str(e)[:50]}")
        return None



def fetch_image_from_google(query, retries=3):
    """Fetch image URL from multiple search engines.
    Uses simple 2-3 word queries for better success.
    
    Args:
        query: Search prompt
        retries: Number of retry attempts
    """
    with IMAGE_CACHE_LOCK:
        if query in IMAGE_CACHE:
            cached_url = IMAGE_CACHE[query]
            print(f"✓ Cached: {query[:40]}...")
            return cached_url
    
    # Simplify the query dramatically (to 2-3 words max)
    simplified_query = simplify_visual_description(query)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    print(f"🔍 Searching for: {simplified_query}...")
    
    # Strategy 1: Try Bing Image Search
    try:
        url = "https://www.bing.com/images/search"
        params = {'q': simplified_query, 'count': 15}
        
        response = requests.get(url, params=params, headers=headers, timeout=8)
        response.raise_for_status()
        
        img_urls = re.findall(r'"murl":"([^"]+)"', response.text)
        
        if img_urls:
            for img_url in img_urls[:2]:
                try:
                    test_response = requests.head(img_url, headers=headers, timeout=3)
                    if test_response.status_code == 200:
                        with IMAGE_CACHE_LOCK:
                            IMAGE_CACHE[query] = img_url
                        print(f"✓ Found: {simplified_query}")
                        return img_url
                except:
                    continue
    except Exception as e:
        pass
    
    # Strategy 2: Try Google Images
    try:
        url = "https://www.google.com/search"
        params = {'q': simplified_query, 'tbm': 'isch'}
        
        response = requests.get(url, params=params, headers=headers, timeout=8)
        response.raise_for_status()
        
        img_urls = re.findall(r'"ou":"([^"]+)"', response.text)
        
        if img_urls:
            for img_url in img_urls[:2]:
                try:
                    test_response = requests.head(img_url, headers=headers, timeout=3)
                    if test_response.status_code == 200:
                        with IMAGE_CACHE_LOCK:
                            IMAGE_CACHE[query] = img_url
                        print(f"✓ Found: {simplified_query}")
                        return img_url
                except:
                    continue
    except Exception as e:
        pass
    
    # Strategy 3: Try DuckDuckGo
    try:
        url = "https://api.duckduckgo.com/"
        params = {'q': simplified_query, 'format': 'json'}
        
        response = requests.get(url, params=params, headers=headers, timeout=8)
        response.raise_for_status()
        
        data = response.json()
        if 'Image' in data and data['Image']:
            img_url = data['Image']
            with IMAGE_CACHE_LOCK:
                IMAGE_CACHE[query] = img_url
            print(f"✓ Found: {simplified_query}")
            return img_url
    except Exception as e:
        pass
    
    print(f"✗ No images: {simplified_query}")
    return None


def download_image_as_base64(image_url, max_size=(400, 300)):
    """Download image and convert to base64 for embedding in HTML."""
    if not image_url:
        return None
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.google.com/',
        }
        
        response = requests.get(image_url, headers=headers, timeout=8, allow_redirects=True)
        response.raise_for_status()
        
        # Open and resize image
        img = Image.open(BytesIO(response.content))
        
        # Convert to RGB if needed
        if img.mode != 'RGB':
            if img.mode == 'RGBA':
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            else:
                img = img.convert('RGB')
        
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Convert to base64 JPEG
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=80)
        img_byte_arr.seek(0)
        img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
        
        return f"data:image/jpeg;base64,{img_base64}"
    except Exception as e:
        print(f"✗ Download failed: {str(e)[:60]}")
        return None


def scrape_image_url(query):
    """Fetch image URL from web (same as PPT). Uses multiple search engines for reliability."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    
    # Strategy 1: Try Bing
    try:
        print(f"  🔍 Bing: Searching {query[:40]}...")
        url = f"https://www.bing.com/images/search?q={quote_plus(query)}&first=1"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', resp.text)
            if not img_urls:
                img_urls = re.findall(r'src2?="(https?://[^"]+\.(?:jpg|jpeg|png|webp|gif))', resp.text)
            
            for img_url in img_urls[:10]:
                try:
                    r = requests.get(img_url, headers=headers, timeout=6)
                    if r.status_code == 200 and len(r.content) > 500:  # Reduced from 2000 to catch more
                        # Validate it's a real image
                        try:
                            img = Image.open(BytesIO(r.content))
                            img.load()
                            print(f"    ✓ Bing found: {img_url[:50]}...")
                            return img_url
                        except Exception:
                            continue
                except Exception:
                    continue
    except Exception as e:
        pass
    
    # Strategy 2: Try Google Images  
    try:
        print(f"  🔍 Google: Searching {query[:40]}...")
        url = "https://www.google.com/search"
        params = {'q': query, 'tbm': 'isch'}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            img_urls = re.findall(r'"ou":"([^"]+)"', resp.text)
            
            for img_url in img_urls[:10]:
                if not img_url.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                    continue
                try:
                    r = requests.get(img_url, headers=headers, timeout=6)
                    if r.status_code == 200 and len(r.content) > 500:
                        try:
                            img = Image.open(BytesIO(r.content))
                            img.load()
                            print(f"    ✓ Google found: {img_url[:50]}...")
                            return img_url
                        except Exception:
                            continue
                except Exception:
                    continue
    except Exception as e:
        pass
    
    # Strategy 3: Try DuckDuckGo
    try:
        print(f"  🔍 DuckDuckGo: Searching {query[:40]}...")
        url = "https://api.duckduckgo.com/"
        params = {'q': query, 'format': 'json'}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('Image'):
                img_url = data['Image']
                try:
                    r = requests.get(img_url, headers=headers, timeout=6)
                    if r.status_code == 200 and len(r.content) > 500:
                        try:
                            img = Image.open(BytesIO(r.content))
                            img.load()
                            print(f"    ✓ DuckDuckGo found: {img_url[:50]}...")
                            return img_url
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception as e:
        pass
    
    # Strategy 4: Simplified keyword search (try Bing again with simpler query)
    try:
        simplified = ' '.join(query.split()[:2])  # Use only first 2 words for simpler search
        if simplified != query and len(simplified) > 3:
            print(f"  🔍 Retry: Simplified {simplified[:40]}...")
            url = f"https://www.bing.com/images/search?q={quote_plus(simplified)}"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', resp.text)
                if not img_urls:
                    img_urls = re.findall(r'src2?="(https?://[^"]+\.(?:jpg|jpeg|png|webp))', resp.text)
                
                for img_url in img_urls[:8]:
                    try:
                        r = requests.get(img_url, headers=headers, timeout=6)
                        if r.status_code == 200 and len(r.content) > 500:
                            try:
                                img = Image.open(BytesIO(r.content))
                                img.load()
                                print(f"    ✓ Simplified found: {img_url[:50]}...")
                                return img_url
                            except Exception:
                                continue
                    except Exception:
                        continue
    except Exception as e:
        pass
    
    return None


def get_image_for_visual(visual_description):
    """Fetch and return image URL for visual description - connects PPT and HTML.
    Uses smart fallback strategies if primary search fails."""
    if not visual_description or not visual_description.strip():
        return None
    
    try:
        # Check cache first (only use if we actually found an image, not failures)
        with IMAGE_CACHE_LOCK:
            if visual_description in IMAGE_CACHE:
                cached = IMAGE_CACHE[visual_description]
                if cached:  # Only return if cache has actual URL, not None
                    print(f"  ✓ Cache hit: {visual_description[:40]}...")
                    return cached
        
        print(f"  🔍 Fetching: {visual_description[:40]}...")
        
        # Try web scraping first (same as PPT uses)
        img_url = scrape_image_url(visual_description)
        if img_url:
            with IMAGE_CACHE_LOCK:
                IMAGE_CACHE[visual_description] = img_url
            return img_url
        
        # Fallback: Try simplified keywords (remove adjectives/descriptions)
        print(f"    💡 Trying simplified search...")
        words = visual_description.split()
        
        # Strategy 1: Try just nouns/key terms (first 2 words usually work best)
        for keyword_count in [2]:
            if len(words) > keyword_count:
                simplified_query = ' '.join(words[:keyword_count])
                try:
                    print(f"    🔍 Attempt: '{simplified_query}'...")
                    img_url = scrape_image_url(simplified_query)
                    if img_url:
                        with IMAGE_CACHE_LOCK:
                            IMAGE_CACHE[visual_description] = img_url
                        return img_url
                except Exception as e:
                    print(f"      ⚠️ Failed: {str(e)[:30]}")
        
        # Strategy 2: Try with key single words from description
        if len(words) >= 1:
            for word in words[:3]:  # Try first 3 words individually
                if len(word) > 4:  # Only try meaningful words
                    try:
                        print(f"    🔍 Single word: '{word}'...")
                        img_url = scrape_image_url(word)
                        if img_url:
                            with IMAGE_CACHE_LOCK:
                                IMAGE_CACHE[visual_description] = img_url
                            return img_url
                    except Exception:
                        pass
        
        # No image found after all attempts
        print(f"    ❌ All attempts failed: {visual_description[:40]}")
            
    except Exception as e:
        print(f"  ✗ Error: {str(e)[:60]}")
    
    return None

# ==========================================
# CONTENT GENERATION FROM GEMINI (PARALLEL)
# ==========================================
def generate_content_for_bullet(bullet_text, slide_title, elaborate=True):
    """Generate comprehensive content from Gemini (with Mistral fallback) for a bullet point.
    
    Args:
        bullet_text: The bullet point/instruction text
        slide_title: The slide title for context
        elaborate: If True, generate detailed content; if False, return as-is
    """
    if '[VISUAL:' in bullet_text or '[DIAGRAM:' in bullet_text:
        # Skip visual markers
        return None
    
    # If not elaborating, just return the bullet text as-is
    if not elaborate:
        return bullet_text
    
    prompt = f"""You are an expert educator creating detailed content for a learning presentation.

Slide Title: {slide_title}

The following is a bullet point instruction/prompt for content:
"{bullet_text}"

Generate a clear, detailed, and engaging explanation (2-3 sentences) suitable for a presentation slide. 
Make it educational, concise, and compelling. Do not include bullet points or numbering.
Just provide the direct explanation text."""

    try:
        # Try Gemini first
        print(f"    📡 Gemini: Elaborating '{bullet_text[:40]}'...")
        response = gemini_generate_text(prompt)
        print(f"    ✓ Gemini succeeded")
        return response.strip()
    except Exception as e:
        print(f"    ⚠️ Gemini failed: {str(e)[:40]}")
        
        # Fallback to Mistral
        try:
            print(f"    📡 Mistral: Fallback elaborating '{bullet_text[:40]}'...")
            response = mistral_generate_text(prompt)
            print(f"    ✓ Mistral succeeded")
            return response.strip()
        except Exception as e2:
            print(f"    ❌ Mistral also failed: {str(e2)[:40]}")
            return bullet_text  # Fall back to original bullet


def generate_content_for_bullets_parallel(bullets_with_titles, max_workers=4, elaborate=True):
    """Generate content for multiple bullets in parallel.
    
    Args:
        bullets_with_titles: List of (bullet_text, slide_title) tuples
        max_workers: Number of parallel threads
        elaborate: If True, generate detailed content; if False, return bullets as-is
    
    Returns:
        List of generated content in same order as input
    """
    results = [None] * len(bullets_with_titles)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for idx, (bullet_text, slide_title) in enumerate(bullets_with_titles):
            if '[VISUAL:' in bullet_text or '[DIAGRAM:' in bullet_text:
                results[idx] = None
            else:
                future = executor.submit(generate_content_for_bullet, bullet_text, slide_title, elaborate)
                futures[future] = idx
        
        # Collect results as they complete
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                print(f"Error generating content: {e}")
                results[idx] = None
    
    return results


def get_images_for_visuals_parallel(visual_descriptions, max_workers=3):
    """Fetch and convert images for multiple visuals in parallel.
    
    Args:
        visual_descriptions: List of image descriptions
        max_workers: Number of parallel threads
    
    Returns:
        Dict mapping description -> image URL (or None if not found)
    """
    images = {}
    found_count = 0
    failed_count = 0
    total_count = len(visual_descriptions)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for desc in visual_descriptions:
            if desc and desc not in images:
                future = executor.submit(get_image_for_visual, desc)
                futures[future] = desc
        
        # Collect results as they complete
        for future in as_completed(futures):
            desc = futures[future]
            try:
                img_data = future.result()
                if img_data:
                    images[desc] = img_data
                    found_count += 1
                    print(f"  ✅ Image {found_count}: {desc[:35]}...")
                else:
                    failed_count += 1
                    print(f"  ❌ Failed {failed_count}: {desc[:35]}...")
                    images[desc] = None
            except Exception as e:
                failed_count += 1
                print(f"  ❌ Error: {desc[:35]}... - {str(e)[:30]}")
                images[desc] = None
    
    print(f"\n📸 Image Summary: {found_count} found / {failed_count} failed / {total_count} total")
    
    return images


def parse_outline_to_slides(outline_text, elaborate=True):
    """Parse user outline into slide structure with parallel Gemini-generated content.
    
    Args:
        outline_text: The outline text to parse
        elaborate: If True, generate detailed content; if False, return bullets as-is
    """
    slides = []
    lines = outline_text.strip().split('\n')
    current_slide = None
    
    # First pass: parse structure
    for line in lines:
        line = line.rstrip()
        if line.startswith('# '):
            if current_slide and current_slide['title']:
                slides.append(current_slide)
            current_slide = {
                'title': line[2:].strip(),
                'bullets': [],
                'generated_content': [],
                'has_visual': False
            }
        elif line.startswith('## '):
            if current_slide and current_slide['title']:
                slides.append(current_slide)
            current_slide = {
                'title': line[3:].strip(),
                'is_section': True,
                'bullets': [],
                'generated_content': [],
                'has_visual': False
            }
        elif line.startswith('- ') or line.startswith('* '):
            if current_slide:
                bullet_text = line[2:].strip()
                current_slide['bullets'].append(bullet_text)
                if '[VISUAL:' in bullet_text or '[DIAGRAM:' in bullet_text:
                    current_slide['has_visual'] = True
    
    if current_slide and current_slide['title']:
        slides.append(current_slide)
    
    # Collect all bullets that need content generation + visual descriptions
    bullets_to_generate = []  # List of (bullet_text, slide_title)
    visual_descriptions = []  # List of visual descriptions
    
    for slide in slides:
        for bullet in slide['bullets']:
            if '[VISUAL:' in bullet or '[DIAGRAM:' in bullet:
                # Extract visual description
                visual_match = re.search(r'\[(?:VISUAL|DIAGRAM):\s*(.+?)\]', bullet)
                if visual_match:
                    desc = visual_match.group(1).strip()
                    visual_descriptions.append(desc)
            else:
                bullets_to_generate.append((bullet, slide['title']))
    
    # Generate content in parallel
    if elaborate:
        print(f"Generating content for {len(bullets_to_generate)} bullet points in parallel...")
    else:
        print(f"Using bullet points as-is (elaboration disabled)...")
    generated_contents = generate_content_for_bullets_parallel(bullets_to_generate, max_workers=4, elaborate=elaborate)
    
    # Fetch images in parallel
    if visual_descriptions:
        print(f"Fetching {len(set(visual_descriptions))} images in parallel...")
        images_map = get_images_for_visuals_parallel(list(set(visual_descriptions)), max_workers=3)
    else:
        images_map = {}
    
    # Map generated content back to slides
    content_idx = 0
    for slide in slides:
        for bullet in slide['bullets']:
            if '[VISUAL:' in bullet or '[DIAGRAM:' in bullet:
                slide['generated_content'].append(None)
            else:
                if content_idx < len(generated_contents):
                    slide['generated_content'].append(generated_contents[content_idx])
                    content_idx += 1
    
    # Add image data to slides
    for slide in slides:
        if slide['has_visual']:
            visual_text = " ".join([b for b in slide['bullets'] if '[VISUAL' in b or '[DIAGRAM' in b])
            visual_text = re.sub(r'\[VISUAL:\s*|\[DIAGRAM:\s*|\]', '', visual_text).strip()
            if visual_text in images_map:
                slide['image_data'] = images_map[visual_text]
    
    return slides


# ==========================================
# QUIZ GENERATION
# ==========================================
def generate_quiz_questions(slides_content, presentation_title):
    """Generate quiz questions from slides using Mistral."""
    # Create content summary with generated content
    content_summary = []
    for slide in slides_content:
        slide_info = f"Slide: {slide['title']}\n"
        if 'generated_content' in slide:
            for content in slide['generated_content']:
                if content:
                    slide_info += f"- {content}\n"
        else:
            for bullet in slide.get('bullets', []):
                cleaned = re.sub(r'\[VISUAL:.*?\]|\[DIAGRAM:.*?\]', '', bullet).strip()
                if cleaned:
                    slide_info += f"- {cleaned}\n"
        content_summary.append(slide_info)
    
    prompt = f"""You are an expert educator creating a comprehensive quiz based on this presentation content:

Presentation Title: {presentation_title}

Slide Content:
{json.dumps(content_summary, indent=2)}

Create a JSON array of quiz questions covering all major topics from the slides. 

For each question, include:
- "slide_number": which slide(s) it covers
- "question": the quiz question text
- "options": array of 4 multiple choice options
- "correct_answer": index of correct option (0-3)
- "explanation": detailed explanation of the answer

Create at least 1-2 questions per 3 slides. Make questions at different difficulty levels:
- Basic recall questions (30%)
- Application/understanding questions (50%)
- Analysis/higher-order thinking questions (20%)

Return ONLY the JSON array, no markdown."""

    try:
        print(f"  📡 Mistral: Generating quiz questions...")
        response = mistral_generate_json(prompt)
        questions = parse_json_response(response)
        print(f"  ✓ Mistral: Generated {len(questions)} quiz questions")
        return questions
    except Exception as e:
        print(f"  ❌ Quiz generation failed: {str(e)[:60]}")
        return []


# ==========================================
# HTML GENERATION
# ==========================================
def generate_html_presentation(outline_text, presentation_title, include_quiz=True, elaborate=True):
    """Generate interactive HTML presentation with optional quiz (parallelized).
    
    Args:
        outline_text: The outline text
        presentation_title: Title of the presentation
        include_quiz: Whether to generate quiz questions
        elaborate: If True, generate detailed content; if False, use bullets as-is
    """
    
    print("Step 1/4: Parsing outline...")
    # Parse outline (just structure, no content generation yet)
    lines = outline_text.strip().split('\n')
    slides_structure = []
    current_slide = None
    
    for line in lines:
        line = line.rstrip()
        if line.startswith('# '):
            if current_slide and current_slide['title']:
                slides_structure.append(current_slide)
            current_slide = {
                'title': line[2:].strip(),
                'bullets': [],
                'has_visual': False
            }
        elif line.startswith('## '):
            if current_slide and current_slide['title']:
                slides_structure.append(current_slide)
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
                if '[VISUAL:' in bullet_text or '[DIAGRAM:' in bullet_text:
                    current_slide['has_visual'] = True
    
    if current_slide and current_slide['title']:
        slides_structure.append(current_slide)
    
    if not slides_structure:
        raise ValueError("No valid slides found in outline.")
    
    # Collect all bullets and visuals for parallel processing
    bullets_to_generate = []
    visual_descriptions = []
    
    for slide in slides_structure:
        for bullet in slide['bullets']:
            if '[VISUAL:' in bullet or '[DIAGRAM:' in bullet:
                visual_match = re.search(r'\[(?:VISUAL|DIAGRAM):\s*(.+?)\]', bullet)
                if visual_match:
                    desc = visual_match.group(1).strip()
                    visual_descriptions.append(desc)
            else:
                bullets_to_generate.append((bullet, slide['title']))
    
    # Run content generation and image fetching in parallel
    if elaborate:
        print(f"Step 2/4: Generating content for {len(bullets_to_generate)} bullets in parallel...")
    else:
        print(f"Step 2/4: Processing {len(bullets_to_generate)} bullets (elaboration disabled)...")
    with ThreadPoolExecutor(max_workers=6) as executor:
        # Submit content generation and image fetching tasks
        content_future = executor.submit(generate_content_for_bullets_parallel, bullets_to_generate, max_workers=4, elaborate=elaborate)
        image_future = executor.submit(get_images_for_visuals_parallel, list(set(visual_descriptions)), max_workers=3) if visual_descriptions else None
        
        # Get results
        generated_contents = content_future.result()
        images_map = image_future.result() if image_future else {}
    
    # Map generated content back to slides
    content_idx = 0
    for slide in slides_structure:
        slide['generated_content'] = []
        for bullet in slide['bullets']:
            if '[VISUAL:' in bullet or '[DIAGRAM:' in bullet:
                slide['generated_content'].append(None)
            else:
                if content_idx < len(generated_contents):
                    slide['generated_content'].append(generated_contents[content_idx])
                    content_idx += 1
        
        # Add image data to slides
        if slide['has_visual']:
            visual_text = " ".join([b for b in slide['bullets'] if '[VISUAL' in b or '[DIAGRAM' in b])
            visual_text = re.sub(r'\[VISUAL:\s*|\[DIAGRAM:\s*|\]', '', visual_text).strip()
            if visual_text in images_map:
                slide['image_data'] = images_map[visual_text]
    
    # Generate quiz questions in parallel (while other tasks complete)
    print("Step 3/4: Generating quiz questions...")
    quiz_questions = []
    if include_quiz:
        try:
            quiz_questions = generate_quiz_questions(slides_structure, presentation_title)
        except Exception as e:
            print(f"Warning: Quiz generation failed: {e}")
            quiz_questions = []
    
    print("Step 4/4: Building HTML...")
    # Now build the HTML with all content ready
    slides = slides_structure
    
    # Create HTML content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{presentation_title} - Interactive Presentation</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        
        .container {{
            width: 100%;
            max-width: 1000px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #0a1950 0%, #1a2a6f 100%);
            color: #ffc43d;
            padding: 30px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
        }}
        
        .header .subtitle {{
            font-size: 1.1em;
            color: #c8d2e1;
        }}
        
        .content {{
            padding: 40px;
            min-height: 500px;
        }}
        
        .slide {{
            display: none;
            animation: fadeIn 0.5s ease-in;
        }}
        
        .slide.active {{
            display: block;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
        }}
        
        .slide h2 {{
            color: #0a1950;
            font-size: 2em;
            margin-bottom: 20px;
            border-bottom: 3px solid #ffc43d;
            padding-bottom: 15px;
        }}
        
        .slide-number {{
            color: #999;
            font-size: 0.9em;
            margin-bottom: 10px;
        }}
        
        .slide ul {{
            margin-left: 30px;
        }}
        
        .slide li {{
            color: #333;
            font-size: 1.1em;
            line-height: 1.8;
            margin-bottom: 15px;
            list-style: none;
            position: relative;
            padding-left: 25px;
        }}
        
        .slide li:before {{
            content: "▸";
            position: absolute;
            left: 0;
            color: #0a1950;
            font-size: 1.3em;
        }}
        
        /* Image Placement Styles */
        .slide-content-wrapper {{
            display: flex;
            gap: 20px;
            align-items: flex-start;
        }}
        
        .slide-text {{
            flex: 1;
            min-width: 0;
        }}
        
        .slide-image {{
            flex-shrink: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
        }}
        
        .slide-image img {{
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            object-fit: cover;
            transition: transform 0.3s ease;
        }}
        
        .slide-image img:hover {{
            transform: scale(1.05);
        }}
        
        .slide-image-caption {{
            margin-top: 10px;
            color: #666;
            font-size: 0.85em;
            font-style: italic;
            text-align: center;
            max-width: 280px;
        }}
        
        .image-left .slide-content-wrapper {{
            flex-direction: row;
        }}
        
        .image-left .slide-image {{
            order: -1;
        }}
        
        .image-left .slide-image {{
            width: 320px;
        }}
        
        .image-right .slide-content-wrapper {{
            flex-direction: row;
        }}
        
        .image-right .slide-image {{
            width: 320px;
        }}
        
        .image-bottom .slide-content-wrapper {{
            flex-direction: column;
        }}
        
        .image-bottom .slide-image {{
            width: 100%;
            justify-content: center;
        }}
        
        .image-bottom .slide-image img {{
            max-width: 100%;
            max-height: 300px;
        }}
        
        @media (max-width: 768px) {{
            .slide-content-wrapper {{
                flex-direction: column !important;
            }}
            
            .slide-image {{
                width: 100% !important;
                order: 0 !important;
            }}
            
            .image-left .slide-image,
            .image-right .slide-image {{
                width: 100% !important;
            }}
        }}
        
        .visual-note {{
            background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 100%);
            border: 2px dashed #ffc43d;
            border-radius: 8px;
            padding: 30px 20px;
            margin: 25px 0;
            text-align: center;
            color: #ff6b6b;
            font-size: 0.95em;
            min-height: 200px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 10px;
        }}
        
        .visual-note::before {{
            content: "🖼️";
            font-size: 3em;
            opacity: 0.3;
        }}
        
        .controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 15px;
            padding: 20px 40px;
            background: #f5f5f5;
            flex-wrap: wrap;
        }}
        
        button {{
            background: #0a1950;
            color: white;
            border: none;
            padding: 12px 25px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s ease;
            font-weight: bold;
        }}
        
        button:hover {{
            background: #1a2a6f;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
        }}
        
        button:active {{
            transform: translateY(0);
        }}
        
        button:disabled {{
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }}
        
        .slide-counter {{
            color: #0a1950;
            font-weight: bold;
            min-width: 100px;
            text-align: center;
        }}
        
        .progress-bar {{
            width: 100%;
            height: 8px;
            background: #e0e0e0;
            border-radius: 10px;
            overflow: hidden;
            margin: 10px 0;
        }}
        
        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: 0%;
            transition: width 0.3s ease;
        }}
        
        /* Quiz Styles */
        .quiz-section {{
            display: none;
            animation: fadeIn 0.5s ease-in;
        }}
        
        .quiz-section.active {{
            display: block;
        }}
        
        .quiz-question {{
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 10px;
            border: 2px solid #e0e0e0;
        }}
        
        .quiz-question h3 {{
            color: #0a1950;
            margin-bottom: 15px;
            font-size: 1.2em;
        }}
        
        .quiz-question .question-text {{
            font-size: 1.1em;
            color: #333;
            margin-bottom: 20px;
        }}
        
        .quiz-options {{
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        
        .quiz-option {{
            background: #f9f9f9;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            padding: 15px;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 1em;
        }}
        
        .quiz-option:hover {{
            border-color: #667eea;
            background: #f0f4ff;
            transform: translateX(5px);
        }}
        
        .quiz-option input[type="radio"] {{
            margin-right: 10px;
            cursor: pointer;
        }}
        
        .quiz-option.selected {{
            border-color: #667eea;
            background: #e8ecff;
        }}
        
        .quiz-option.correct {{
            border-color: #4caf50;
            background: #e8f5e9;
        }}
        
        .quiz-option.incorrect {{
            border-color: #f44336;
            background: #ffebee;
        }}
        
        .quiz-feedback {{
            margin-top: 15px;
            padding: 15px;
            border-radius: 8px;
            display: none;
        }}
        
        .quiz-feedback.show {{
            display: block;
        }}
        
        .quiz-feedback.correct {{
            background: #e8f5e9;
            border-left: 4px solid #4caf50;
            color: #2e7d32;
        }}
        
        .quiz-feedback.incorrect {{
            background: #ffebee;
            border-left: 4px solid #f44336;
            color: #c62828;
        }}
        
        .quiz-results {{
            text-align: center;
            padding: 40px 20px;
        }}
        
        .quiz-results h2 {{
            color: #0a1950;
            font-size: 2em;
            margin-bottom: 20px;
        }}
        
        .quiz-results .score {{
            font-size: 3em;
            font-weight: bold;
            color: #667eea;
            margin: 20px 0;
        }}
        
        .quiz-results .score-text {{
            font-size: 1.2em;
            color: #666;
        }}
        
        .tab-buttons {{
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid #e0e0e0;
        }}
        
        .tab-button {{
            background: none;
            border: none;
            color: #666;
            padding: 15px 20px;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            font-weight: bold;
            transition: all 0.3s ease;
            font-size: 1em;
        }}
        
        .tab-button:hover {{
            color: #0a1950;
            background: none;
            transform: none;
            box-shadow: none;
        }}
        
        .tab-button.active {{
            color: #667eea;
            border-bottom-color: #667eea;
        }}
        
        .tabs {{
            display: flex;
        }}
        
        .tab-content {{
            flex: 1;
        }}
        
        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 1.8em;
            }}
            
            .slide h2 {{
                font-size: 1.5em;
            }}
            
            .slide li {{
                font-size: 1em;
            }}
            
            .controls {{
                flex-direction: column;
            }}
            
            button {{
                width: 100%;
            }}
            
            .tab-buttons {{
                flex-wrap: wrap;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{presentation_title}</h1>
            <div class="subtitle">Interactive Learning Presentation</div>
        </div>
        
        <div class="progress-bar">
            <div class="progress-fill" id="progressFill"></div>
        </div>
        
        <div class="tabs">
            <div class="tab-content">
                <div class="tab-buttons">
                    <button class="tab-button active" onclick="switchTab('slides')">📊 Slides</button>
"""

    if quiz_questions:
        html_content += '                    <button class="tab-button" onclick="switchTab(\'quiz\')">❓ Quiz (' + str(len(quiz_questions)) + ' Questions)</button>\n'
    
    html_content += """                </div>
                
                <!-- SLIDES TAB -->
                <div id="slides-tab" class="tab-content">
                    <div class="content">
"""
    
    # Add slides with images and generated content
    slide_number = 1
    for idx, slide in enumerate(slides, 1):
        # Determine image placement if this slide has an image
        image_placement = get_random_image_placement() if slide.get('image_data') else None
        placement_class = f"image-{image_placement}" if image_placement else ""
        
        html_content += f"""                        <div class="slide {placement_class} {'active' if idx == 1 else ''}" data-slide="{slide_number}">
                            <div class="slide-number">Slide {slide_number} of {len(slides) + len(quiz_questions)}</div>
                            <h2>{slide['title']}</h2>
"""
        
        # Add image if present (use pre-fetched image data) with smart placement
        if slide.get('image_data'):
            visual_text = " ".join([b for b in slide['bullets'] if '[VISUAL' in b or '[DIAGRAM' in b])
            visual_text = re.sub(r'\[VISUAL:\s*|\[DIAGRAM:\s*|\]', '', visual_text).strip()
            
            html_content += """                            <div class="slide-content-wrapper">
"""
            
            # Add image wrapper
            html_content += f"""                                <div class="slide-image">
                                    <img src="{slide['image_data']}" alt="{visual_text}" style="max-width: 100%; max-height: 280px;">
                                    <div class="slide-image-caption">{visual_text}</div>
                                </div>
"""
            
            # Add text wrapper
            html_content += """                                <div class="slide-text">
                                    <ul>
"""
            
            # Use generated content instead of raw bullets
            for i, bullet in enumerate(slide['bullets']):
                # Get generated content or fall back to cleaned bullet
                if i < len(slide.get('generated_content', [])) and slide['generated_content'][i]:
                    content = slide['generated_content'][i]
                else:
                    # Remove visual markers for display
                    content = re.sub(r'\[VISUAL:.*?\]|\[DIAGRAM:.*?\]', '', bullet).strip()
                
                if content:
                    html_content += f"                                        <li>{content}</li>\n"
            
            html_content += """                                    </ul>
                                </div>
                            </div>
"""
        elif slide['has_visual']:
            # Fallback for missing images
            visual_text = " ".join([b for b in slide['bullets'] if '[VISUAL' in b or '[DIAGRAM' in b])
            visual_text = re.sub(r'\[VISUAL:\s*|\[DIAGRAM:\s*|\]', '', visual_text).strip()
            
            html_content += f"""                            <div class="slide-content-wrapper">
                                <div class="slide-text">
                                    <ul>
"""
            
            # Use generated content instead of raw bullets
            for i, bullet in enumerate(slide['bullets']):
                # Get generated content or fall back to cleaned bullet
                if i < len(slide.get('generated_content', [])) and slide['generated_content'][i]:
                    content = slide['generated_content'][i]
                else:
                    # Remove visual markers for display
                    content = re.sub(r'\[VISUAL:.*?\]|\[DIAGRAM:.*?\]', '', bullet).strip()
                
                if content:
                    html_content += f"                                        <li>{content}</li>\n"
            
            html_content += f"""                                    </ul>
                                </div>
                                <div class="visual-note">📸 Visual: {visual_text}</div>
                            </div>
"""
        else:
            # No image - just show bullet points
            html_content += """                            <ul>
"""
            
            # Use generated content instead of raw bullets
            for i, bullet in enumerate(slide['bullets']):
                # Get generated content or fall back to cleaned bullet
                if i < len(slide.get('generated_content', [])) and slide['generated_content'][i]:
                    content = slide['generated_content'][i]
                else:
                    # Remove visual markers for display
                    content = re.sub(r'\[VISUAL:.*?\]|\[DIAGRAM:.*?\]', '', bullet).strip()
                
                if content:
                    html_content += f"                                <li>{content}</li>\n"
            
            html_content += """                            </ul>
"""
        
        html_content += """                        </div>
"""
        slide_number += 1
    
    # Add quiz questions as inline slides at the end
    if quiz_questions:
        for q_idx, question in enumerate(quiz_questions, 1):
            html_content += f"""                        <div class="slide" data-slide="{slide_number}">
                            <div class="slide-number">Quiz Question {q_idx} of {len(quiz_questions)} (Slide {slide_number} of {len(slides) + len(quiz_questions)})</div>
                            <h2 style="color: #667eea;">Question {q_idx}</h2>
                            <div class="question-text" style="font-size: 1.2em; margin: 20px 0; color: #333;">
                                {question.get('question', '')}
                            </div>
                            <div class="quiz-options" style="margin-top: 20px;">
"""
            
            for opt_idx, option in enumerate(question.get('options', [])):
                correct_idx = question.get('correct_answer', 0)
                html_content += f"""                                <label class="quiz-option" style="margin: 10px 0;">
                                    <input type="radio" name="q{q_idx}" value="{opt_idx}" onchange="checkAnswerInline({q_idx}, {opt_idx}, {correct_idx})">
                                    {option}
                                </label>
"""
            
            explanation = question.get('explanation', '')
            html_content += f"""                            </div>
                            <div class="quiz-feedback" id="feedback-{q_idx}" style="margin-top: 20px;">
                                <strong>Explanation:</strong> {explanation}
                            </div>
                        </div>
"""
            slide_number += 1
    
    html_content += """                    </div>
                </div>
                
"""
    
    # Keep the old quiz tab as backup if needed
    if quiz_questions:
        html_content += """                <!-- QUIZ TAB (Legacy) -->
                <div id="quiz-tab" class="tab-content" style="display:none;">
                    <div class="content">
                        <div id="quiz-questions">
"""
        
        for q_idx, question in enumerate(quiz_questions, 1):
            html_content += f"""                            <div class="quiz-question" data-question="{q_idx}">
                                <h3>Question {q_idx} of {len(quiz_questions)}</h3>
                                <div class="question-text">{question.get('question', '')}</div>
                                <div class="quiz-options">
"""
            
            for opt_idx, option in enumerate(question.get('options', [])):
                correct_idx = question.get('correct_answer', 0)
                html_content += f"""                                    <label class="quiz-option">
                                        <input type="radio" name="q{q_idx}" value="{opt_idx}" onchange="checkAnswer({q_idx}, {opt_idx}, {correct_idx})">
                                        {option}
                                    </label>
"""
            
            explanation = question.get('explanation', '')
            html_content += f"""                                </div>
                                <div class="quiz-feedback" id="feedback-{q_idx}">
                                    <strong>Explanation:</strong> {explanation}
                                </div>
                            </div>
"""
        
        html_content += f"""                        </div>
                        <div id="quiz-results" style="display:none;">
                            <div class="quiz-results">
                                <h2>Quiz Complete! 🎉</h2>
                                <div class="score" id="final-score">0%</div>
                                <div class="score-text" id="score-text">0 out of {len(quiz_questions)} correct</div>
                                <button onclick="resetQuiz()" style="margin-top: 30px;">Retake Quiz</button>
                            </div>
                        </div>
                    </div>
                </div>
"""
    
    html_content += """            </div>
        </div>
        
        <div class="controls">
            <button id="prevBtn" onclick="previousSlide()">← Previous</button>
            <div class="slide-counter">
                <span id="slideNum">1</span> / <span id="totalSlides">""" + str(len(slides)) + """</span>
            </div>
            <button id="nextBtn" onclick="nextSlide()">Next →</button>
        </div>
    </div>
    
    <script>
        let currentSlide = 1;
        const totalSlides = """ + str(len(slides) + len(quiz_questions)) + """;
        const totalQuestions = """ + str(len(quiz_questions)) + """;
        let quizAnswers = {};
        
        function showSlide(n) {
            const slides = document.querySelectorAll('.slide');
            if (n > totalSlides) { currentSlide = totalSlides; }
            if (n < 1) { currentSlide = 1; }
            
            slides.forEach(slide => slide.classList.remove('active'));
            slides[currentSlide - 1].classList.add('active');
            
            document.getElementById('slideNum').textContent = currentSlide;
            document.getElementById('prevBtn').disabled = currentSlide === 1;
            document.getElementById('nextBtn').disabled = currentSlide === totalSlides;
            
            const progress = (currentSlide / totalSlides) * 100;
            document.getElementById('progressFill').style.width = progress + '%';
        }
        
        function nextSlide() {
            currentSlide++;
            showSlide(currentSlide);
        }
        
        function previousSlide() {
            currentSlide--;
            showSlide(currentSlide);
        }
        
        function switchTab(tab) {
            document.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            
            const slidesTab = document.getElementById('slides-tab');
            const quizTab = document.getElementById('quiz-tab');
            
            if (tab === 'slides') {
                slidesTab.style.display = 'block';
                if (quizTab) quizTab.style.display = 'none';
            } else {
                slidesTab.style.display = 'none';
                if (quizTab) quizTab.style.display = 'block';
            }
        }
        
        function checkAnswer(questionNum, selectedIdx, correctIdx) {
            quizAnswers[questionNum] = selectedIdx === correctIdx;
            
            const labels = document.querySelectorAll(`[data-question="${questionNum}"] .quiz-option`);
            
            labels.forEach((label, idx) => {
                label.classList.remove('selected', 'correct', 'incorrect');
                if (idx === selectedIdx) {
                    label.classList.add('selected');
                    label.classList.add(idx === correctIdx ? 'correct' : 'incorrect');
                }
            });
            
            document.getElementById(`feedback-${questionNum}`).classList.add('show');
            
            if (Object.keys(quizAnswers).length === totalQuestions) {
                showQuizResults();
            }
        }
        
        function showQuizResults() {
            document.getElementById('quiz-questions').style.display = 'none';
            document.getElementById('quiz-results').style.display = 'block';
            
            const correct = Object.values(quizAnswers).filter(v => v).length;
            const percentage = Math.round((correct / totalQuestions) * 100);
            
            document.getElementById('final-score').textContent = percentage + '%';
            document.getElementById('score-text').textContent = `${correct} out of ${totalQuestions} correct`;
        }
        
        function checkAnswerInline(questionNum, selectedIdx, correctIdx) {
            quizAnswers[questionNum] = selectedIdx === correctIdx;
            
            const labels = document.querySelectorAll(`[data-question-inline="${questionNum}"] .quiz-option, input[name="q${questionNum}"]`).parentElement;
            
            // Find all options for this question on this slide
            const currentSlideDiv = document.querySelector('.slide.active');
            if (!currentSlideDiv) return;
            
            const radioInputs = currentSlideDiv.querySelectorAll(`input[name="q${questionNum}"]`);
            const options = [];
            radioInputs.forEach(input => {
                if (input.parentElement && input.parentElement.classList.contains('quiz-option')) {
                    options.push(input.parentElement);
                }
            });
            
            options.forEach((opt, idx) => {
                opt.classList.remove('selected', 'correct', 'incorrect');
                if (idx === selectedIdx) {
                    opt.classList.add('selected');
                    opt.classList.add(idx === correctIdx ? 'correct' : 'incorrect');
                }
            });
            
            const feedbackDiv = currentSlideDiv.querySelector(`#feedback-${questionNum}`);
            if (feedbackDiv) {
                feedbackDiv.classList.add('show');
            }
        }
        
        function resetQuiz() {
            quizAnswers = {};
            document.getElementById('quiz-questions').style.display = 'block';
            document.getElementById('quiz-results').style.display = 'none';
            
            document.querySelectorAll('.quiz-option').forEach(opt => {
                opt.classList.remove('selected', 'correct', 'incorrect');
                opt.querySelector('input').checked = false;
            });
            
            document.querySelectorAll('.quiz-feedback').forEach(fb => {
                fb.classList.remove('show');
            });
        }
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            showSlide(currentSlide);
            document.addEventListener('keydown', function(event) {
                if (event.key === 'ArrowRight') nextSlide();
                if (event.key === 'ArrowLeft') previousSlide();
            });
        });
    </script>
</body>
</html>
"""
    
    return html_content


def save_html_presentation(html_content, presentation_title):
    """Save HTML presentation to file."""
    safe_name = re.sub(r'[^\w\s-]', '', presentation_title).strip().replace(' ', '_')
    filename = f"{safe_name}_Interactive.html"
    
    return html_content.encode('utf-8'), filename


def generate_html_from_outline(outline_text, presentation_title, include_quiz=True, elaborate=True):
    """Main function to generate HTML presentation from outline.
    
    Args:
        outline_text: The outline text
        presentation_title: Title of the presentation
        include_quiz: Whether to generate quiz questions
        elaborate: If True, generate detailed content; if False, use bullets as-is
    """
    html_content = generate_html_presentation(outline_text, presentation_title, include_quiz, elaborate)
    html_bytes, filename = save_html_presentation(html_content, presentation_title)
    
    return BytesIO(html_bytes), filename


# ==========================================
# TEST FUNCTION
# ==========================================
if __name__ == "__main__":
    # Test outline
    test_outline = """
# Slide 1: Introduction
- Welcome to the presentation
- Let's explore together
- [VISUAL: Introduction graphic]

# Slide 2: Main Topic
- Key concept 1
- Key concept 2
- Key concept 3

# Slide 3: Summary
- Important takeaway
- Remember this
"""
    
    try:
        html_buf, filename = generate_html_from_outline(test_outline, "Test Presentation")
        print(f"Generated: {filename}")
        print(f"Size: {len(html_buf.getvalue())} bytes")
    except Exception as e:
        print(f"Error: {e}")
