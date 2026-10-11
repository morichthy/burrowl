"""Reconstruct the saved round-3 ordering using its original sampling recipe.

Uses Python's local random.Random(20260909), resetting the 30-item base list
before each of 30 shuffles while preserving the generator state. No original
input is overwritten. Exit 0 requires exact string-array equality with every
saved case; this is reproducibility of sampling, not a statistical guarantee.
"""
import argparse
import hashlib
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Saved round-3 inputs.json')
    parser.add_argument('--output', type=Path, required=True, help='New JSON verification file')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Refusing to replace an earlier verification')
    raw = args.input.read_bytes()
    original = json.loads(raw.decode('utf-8'))
    rng = random.Random(20260909)
    cases = []
    for index in range(30):
        values = ['1e16', '1', '-1e16'] * 10
        rng.shuffle(values)
        cases.append({'name': f'shuffle-{index + 1:02}', 'values': values})
    if cases != original['cases']:
        raise AssertionError('Generated ordering differs from saved inputs')
    result = {'checked_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
              'input_sha256': hashlib.sha256(raw).hexdigest(), 'python': sys.version,
              'seed': 20260909, 'case_count': 30, 'values_per_case': 30,
              'exact_saved_order_match': True, 'generated_cases': cases}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'case_count': 30, 'values_per_case': 30, 'exact_saved_order_match': True}))


if __name__ == '__main__':
    main()
