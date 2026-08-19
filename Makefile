.PHONY: install test audit build build-offline validate verify-final clean

install:
	python -m pip install -r requirements.txt

test:
	python -m pytest -q

audit:
	python scripts/audit_governance.py --config config/sources.yml --policy config/search-policy.json

build:
	python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json

build-offline:
	python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json --offline

validate:
	python scripts/validate_v3_backend.py --public-dir public --minimum-items 30000 --max-bytes 73400320

verify-final:
	python scripts/verify_final_phase.py --generated-dir public

clean:
	rm -rf public .pytest_cache **/__pycache__
