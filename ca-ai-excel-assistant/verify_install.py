#!/usr/bin/env python3
"""
Step 2 verification: ensure all dependencies are installed and importable.
Run from ca-ai-excel-assistant:  python verify_install.py
"""
import sys

PACKAGES = [
    ("streamlit", "Streamlit"),
    ("fastapi", "FastAPI"),
    ("pandas", "pandas"),
    ("pymongo", "pymongo"),
    ("chromadb", "chromadb"),
    ("autogen", "autogen"),
    ("plotly", "plotly"),
    ("groq", "groq"),
    ("openpyxl", "openpyxl"),
    ("dotenv", "python-dotenv"),
]

def main():
    failed = []
    for module, name in PACKAGES:
        try:
            __import__(module)
            print(f"  OK  {name}")
        except ImportError as e:
            print(f"  FAIL {name}: {e}")
            failed.append(name)
    if failed:
        print("\nInstall missing packages:  pip install -r requirements.txt")
        sys.exit(1)
    print("\nAll dependencies OK. Step 2 verification passed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
