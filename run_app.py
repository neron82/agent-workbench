#!/usr/bin/env python3
"""Minimal runner — avoids Werkzeug FD inheritance bug."""
from src.agent_workbench.web.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)