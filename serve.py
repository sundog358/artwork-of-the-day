"""Production entry point.

Runs the Flask app under Waitress (a pure-Python WSGI server that works on
Windows and Linux), instead of the Flask development server. Honors the PORT
environment variable so it works locally and on most hosts.

    python serve.py
"""
import os

from waitress import serve

from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Serving Artwork of the Day on http://0.0.0.0:{port}")
    serve(app, host="0.0.0.0", port=port)
