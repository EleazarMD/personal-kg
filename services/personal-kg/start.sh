#!/bin/bash
# Start Personal Context Graph (PCG) service

cd "$(dirname "$0")"
source venv/bin/activate

export PYTHONPATH=".:$PYTHONPATH"

echo "🚀 Starting Personal Context Graph (PCG) on port 8765..."
python3 -m uvicorn api:app --host 0.0.0.0 --port 8765 --reload
