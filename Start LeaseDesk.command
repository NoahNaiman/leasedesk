#!/bin/bash
# Double-click this file to start LeaseDesk. Keep the window open while you use the app.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Download it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."
  exit 1
fi

if [ ! -x .venv/bin/streamlit ]; then
  echo "Setting up LeaseDesk for the first time. This takes a minute or two..."
  python3 -m venv .venv && .venv/bin/pip install --quiet --upgrade pip \
    && .venv/bin/pip install --quiet -r requirements.txt
  if [ $? -ne 0 ]; then
    rm -rf .venv
    echo "Setup failed. Check your internet connection and try again."
    read -r -p "Press Enter to close."
    exit 1
  fi
fi

echo "Starting LeaseDesk. Your browser will open in a moment."
echo "To stop LeaseDesk, close this window."
exec .venv/bin/streamlit run app/main.py --server.headless false
