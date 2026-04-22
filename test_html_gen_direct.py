#!/usr/bin/env python
"""Test html_generator import directly"""
try:
    from html_generator import generate_html_from_outline
    if callable(generate_html_from_outline):
        print("✓ SUCCESS: HTML generation function is available and callable!")
    else:
        print("✗ ERROR: HTML generation function is None")
except Exception as e:
    print(f"✗ ERROR: Failed to import - {type(e).__name__}: {e}")
