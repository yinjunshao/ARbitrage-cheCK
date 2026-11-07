PY ?= .venv/Scripts/python.exe

setup:
	uv venv .venv --python 3.11
	uv pip install --python $(PY) -r requirements-frame.txt

test:
	$(PY) -m pytest tests/frame -q

demo:
	$(PY) -m app_frame.main --replay

live:
	$(PY) -m app_frame.main

replay-fixtures:
	$(PY) scripts/build_replay.py
