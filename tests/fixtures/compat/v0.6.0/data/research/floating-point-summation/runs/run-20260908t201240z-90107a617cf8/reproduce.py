"""Reproduce finite binary64 summation experiments using only Python stdlib.

Usage: python experiment.py --input inputs.json --output results.json
Inputs are explicit numeric strings. The reference is the exact rational sum
of the represented floats, not an assumed exact decimal interpretation.
Exit 0 means the computation finished, not that every method was accurate.
Existing outputs are refused so earlier observations cannot be overwritten.
"""
import argparse
import hashlib
import json
import math
import platform
import sys
from fractions import Fraction
from pathlib import Path


def sequential(values):
    """One rounded addition per element; do not use version-dependent sum()."""
    total = 0.0
    for value in values:
        total = total + value
    return total


def kahan(values):
    """Carry the low-order loss forward into the next addend.

    This implementation is evaluated, not assumed to cure all cancellation.
    Inputs and compensation have the same dimensionless units.
    """
    total, lost = 0.0, 0.0
    for value in values:
        adjusted = value - lost
        updated = total + adjusted
        lost = (updated - total) - adjusted
        total = updated
    return total


def neumaier(values):
    """Accumulate a correction using the larger-magnitude addend as reference.

    The branch also handles an incoming value larger than the running total;
    that is the cancellation case investigated in round 2.
    """
    total, correction = 0.0, 0.0
    for value in values:
        updated = total + value
        if abs(total) >= abs(value):
            correction += (total - updated) + value
        else:
            correction += (value - updated) + total
        total = updated
    return total + correction


METHODS = {'sequential':sequential, 'kahan':kahan, 'neumaier':neumaier, 'math.fsum':math.fsum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; use a new output path to preserve history.')
    raw = args.input.read_bytes()
    spec = json.loads(raw)
    rows = []
    for case in spec['cases']:
        values = [float(v) for v in case['values']]
        if not all(math.isfinite(v) for v in values):
            parser.error('This experiment covers finite inputs only.')
        exact = sum((Fraction.from_float(v) for v in values), Fraction())
        # All chosen inputs are integers, allowing a second independent oracle.
        assert all(v.is_integer() for v in values)
        assert exact == sum(int(v) for v in values)
        for name in spec['methods']:
            actual = METHODS[name](values)
            if not math.isfinite(actual):
                parser.error('Nonfinite output outside the selected experiment scope.')
            error = abs(Fraction.from_float(actual)-exact)
            rows.append({'case':case['name'],'method':name,'n':len(values),
                         'result':actual,'result_hex':actual.hex(),'exact_sum':str(exact),
                         'absolute_error_exact':str(error),'absolute_error':float(error),
                         'exact_match':error==0})
    summary = {}
    for name in spec['methods']:
        subset = [r for r in rows if r['method']==name]
        summary[name] = {'cases':len(subset),'exact_matches':sum(r['exact_match'] for r in subset),
                         'max_absolute_error':max(r['absolute_error'] for r in subset)}
    output = {'round':spec['round'],'question':spec['question'],'input_sha256':hashlib.sha256(raw).hexdigest(),
              'python':sys.version,'platform':platform.platform(),'float_mantissa_bits':sys.float_info.mant_dig,
              'reference':'Exact rational sum of input floats, cross-checked with integer arithmetic',
              'summary':summary,'rows':rows,'limitations':['Constructed dimensionless finite inputs only.',
              'No overflow, NaN, infinity, timing or proof of universal correctness.']}
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
