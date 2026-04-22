from html_generator import generate_html_from_outline
import os
from dotenv import load_dotenv

# Load env
load_dotenv()
print("API Keys loaded:")
print(f"  GEMINI_API_KEY: {len(os.getenv('GEMINI_API_KEY', ''))} chars")
print(f"  Mistral_Api: {len(os.getenv('Mistral_Api', ''))} chars")

# Test the function
test_outline = """# Test Slide 1
- Point 1
- Point 2
- [VISUAL: Test image]
"""

try:
    print("\nTesting generate_html_from_outline...")
    result = generate_html_from_outline(test_outline, "Test Presentation", include_quiz=False, elaborate=False)
    print(f"✓ Success! Result type: {type(result)}")
    print(f"  Buffer: {type(result[0])}")
    print(f"  Filename: {result[1]}")
    print(f"  HTML size: {len(result[0].getvalue())} bytes")
except Exception as e:
    print(f"✗ Failed: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
