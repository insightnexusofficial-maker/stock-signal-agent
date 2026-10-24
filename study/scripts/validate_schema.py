#!/usr/bin/env python3
"""Validate public cycle contracts with JSON Schema draft 2020-12."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def validate(document):
    schema = json.loads((ROOT / 'schemas/cycle-report.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    return sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document), key=lambda error: str(error.path))


if __name__ == '__main__':
    errors = validate(json.loads((ROOT / 'public/data/cycle-latest.json').read_text()))
    for error in errors:
        print('Cycle schema:', '/'.join(map(str, error.path)), error.message)
    if errors:
        raise SystemExit(1)
    print('Cycle JSON Schema: valid')
