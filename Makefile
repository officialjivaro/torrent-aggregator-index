.PHONY: install test build build-offline validate clean

install:
	python -m pip install -r requirements.txt

test:
	python -m pytest -q

build:
	python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json

build-offline:
	python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json --offline

validate:
	python scripts/validate_v3_backend.py --public-dir public --minimum-items 30000 --max-bytes 73400320

clean:
	rm -rf public .pytest_cache **/__pycache__
