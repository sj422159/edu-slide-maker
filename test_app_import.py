#!/usr/bin/env python
from app import generate_html_from_outline
print(f"generate_html_from_outline: {'callable' if callable(generate_html_from_outline) else type(generate_html_from_outline)}")
if callable(generate_html_from_outline):
    print("✓ HTML generation function is available!")
else:
    print("✗ HTML generation function is None or not callable")
