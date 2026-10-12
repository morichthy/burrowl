"""Recheck saved summation cases and expose auditable intermediate arithmetic.

The numerical part uses only the Python standard library. Optional PNG plotting
uses a separately supplied Matplotlib environment; it does not alter the
workspace's installed runtime. Inputs and old results are checked by SHA-256
before reading. The output directory must not exist, preserving earlier runs.

Exit 0 means every final result matched its saved value and exact error. It is
not a claim of accuracy for every summation method or arbitrary floating input.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
from fractions import Fraction
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_checked(root, item):
    """Resolve only the declared files, not an entire source directory tree."""
    path = root / item['path']
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('Input must be a plain file inside the workspace')
    if digest(path) != item['sha256']:
        raise ValueError('Source fingerprint changed: ' + item['path'])
    return json.loads(path.read_text(encoding='utf-8'))


def trace(values, method):
    """Store each rounded binary64 operation and its exact prefix error.

    All quantities here are dimensionless. Exact prefixes use Fraction of the
    represented floats, independent of the accumulated floating approximation.
    Kahan's lost value is subtracted on the next iteration; Neumaier's
    correction is added at the end. Keeping both signs avoids a common error
    when explaining or reimplementing these algorithms.
    """
    total, correction = 0.0, 0.0
    exact = Fraction()
    steps = []
    for index, value in enumerate(values, 1):
        before, old_correction = total, correction
        exact += Fraction.from_float(value)
        adjusted = None
        if method == 'sequential':
            total = total + value
        elif method == 'kahan':
            adjusted = value - correction
            total = before + adjusted
            correction = (total - before) - adjusted
        elif method == 'neumaier':
            total = before + value
            if abs(before) >= abs(value):
                correction += (before - total) + value
            else:
                correction += (value - total) + before
        else:
            raise ValueError('Unsupported explicit trace algorithm')
        returned = total + correction if method == 'neumaier' else total
        steps.append({'i': index, 'x': value, 'x_hex': value.hex(),
                      's_before': before, 'c_before': old_correction,
                      'adjusted': adjusted, 's_after': total, 'c_after': correction,
                      's_after_hex': total.hex(), 'c_after_hex': correction.hex(),
                      'returned_prefix': returned, 'exact_prefix': str(exact),
                      'signed_error_exact': str(Fraction.from_float(returned) - exact)})
    return (total + correction if method == 'neumaier' else total), steps


def plot(rounds, output, packages):
    """Render from computed rows, with the exact data also saved as JSON.

    Matplotlib is a research authoring tool here. It is loaded from an explicit
    optional directory and never installed into the framework runtime by this
    script. Existing PNGs are delivered for reading without any plot package.
    """
    if packages:
        sys.path.insert(0, str(packages.resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'figure.facecolor': '#ffffff', 'axes.facecolor': '#ffffff',
                         'savefig.facecolor': '#ffffff'})
    methods = ['sequential', 'kahan', 'neumaier', 'math.fsum']
    labels = ['Sequential', 'Kahan', 'Neumaier', 'math.fsum']
    colors = ['#64748b', '#d97706', '#0f766e', '#2563eb']
    for item in rounds:
        number = item['round']
        n = len(item['cases'])
        rows = item['rows']
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), layout='constrained')
        exact = [sum(row['exact_match'] for row in rows if row['method'] == name) for name in methods]
        max_error = [max(row['absolute_error'] for row in rows if row['method'] == name) for name in methods]
        axes[0].bar(labels, exact, color=colors, width=0.64)
        axes[0].set_ylim(0, n * 1.17)
        axes[0].set_ylabel('Exactly correct cases / total cases')
        axes[0].yaxis.set_major_locator(MaxNLocator(integer=True))
        axes[0].set_title('Exact agreement with rational reference', loc='left')
        for i, value in enumerate(exact):
            axes[0].text(i, value + n * .025, f'{value}/{n}', ha='center', fontweight='bold')
        axes[1].bar(labels, max_error, color=colors, width=0.64)
        top = max(max_error, default=0) or 1
        axes[1].set_ylim(0, top * 1.2)
        axes[1].set_ylabel('Maximum absolute error (dimensionless)')
        axes[1].set_title('Magnitude of the worst observed error', loc='left')
        for i, value in enumerate(max_error):
            axes[1].text(i, value + top * .025, f'{value:g}', ha='center', fontweight='bold')
        for ax in axes:
            ax.grid(axis='y', alpha=.15)
            ax.set_axisbelow(True)
            ax.tick_params(axis='x', labelrotation=12)
        fig.suptitle(f'Round {number} | fixed-input summation experiment', fontsize=16, fontweight='bold')
        fig.savefig(output / f'round-{number}-summary.png', dpi=170,
                    metadata={'Description': 'Generated from trace-results.json; no inferred data'})
        plt.close(fig)
    third = next(item for item in rounds if item['round'] == 3)
    fig, ax = plt.subplots(figsize=(11.5, 4.5), layout='constrained')
    for method, label, color in zip(methods, labels, colors):
        values = [row['absolute_error'] for row in third['rows'] if row['method'] == method]
        # Plot the complete saved permutation sequence, not a selected subset.
        ax.plot(range(1, len(values) + 1), values, marker='o', markersize=3.5,
                linewidth=1.4, label=label, color=color, alpha=.85)
    ax.set(xlabel='Saved permutation index (seed 20260909)',
           ylabel='Absolute error (dimensionless)',
           title='Round 3 | all 30 saved permutations')
    ax.grid(alpha=.18)
    ax.legend(ncol=4, loc='upper right')
    fig.savefig(output / 'round-3-permutations.png', dpi=170)
    plt.close(fig)
    return {'matplotlib': matplotlib.__version__,
            'packages': {name: importlib.metadata.version(name) for name in
                         ('matplotlib', 'numpy', 'pillow', 'contourpy', 'cycler', 'fonttools',
                          'kiwisolver', 'packaging', 'pyparsing', 'python-dateutil', 'six')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Workspace containing the fixed input paths')
    parser.add_argument('--manifest', type=Path, required=True, help='Explicit old inputs/results and SHA-256 list')
    parser.add_argument('--output', type=Path, required=True, help='New output directory; existing path is refused')
    parser.add_argument('--plot-dependencies', type=Path, help='Optional isolated Matplotlib package directory')
    parser.add_argument('--no-plots', action='store_true', help='Recheck numbers with only the Python standard library')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.output.exists():
        raise FileExistsError('Refusing to overwrite a previous output directory')
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    rounds, all_steps = [], []
    checked = 0
    for definition in manifest['rounds']:
        inputs = load_checked(root, definition['inputs'])
        saved = load_checked(root, definition['results'])
        rows = []
        expected = {(row['case'], row['method']): row for row in saved['rows']}
        for case in inputs['cases']:
            values = [float(v) for v in case['values']]
            if not all(math.isfinite(value) and value.is_integer() for value in values):
                raise ValueError('This integer-input study does not cover nonfinite or fractional inputs')
            reference = sum((Fraction.from_float(value) for value in values), Fraction())
            if reference != sum(int(value) for value in values):
                raise AssertionError('Independent exact reference mismatch')
            for method in inputs['methods']:
                if method == 'math.fsum':
                    result = math.fsum(values)
                    # Internal fsum partials are not exposed. Do not invent a
                    # step trace; record only the actual library result.
                else:
                    result, steps = trace(values, method)
                    all_steps.append({'round': inputs['round'], 'case': case['name'],
                                      'method': method, 'steps': steps})
                error = abs(Fraction.from_float(result) - reference)
                old = expected[(case['name'], method)]
                if result.hex() != old['result_hex'] or str(error) != old['absolute_error_exact']:
                    raise AssertionError(f'Saved result mismatch: round {inputs["round"]} {case["name"]} {method}')
                rows.append({'case': case['name'], 'method': method, 'result': result,
                             'result_hex': result.hex(), 'absolute_error_exact': str(error),
                             'absolute_error': float(error), 'exact_match': error == 0})
                checked += 1
        rounds.append({'round': inputs['round'], 'cases': inputs['cases'], 'rows': rows,
                       'saved_run_id': definition['run_id']})
    args.output.mkdir(parents=True)
    output = {'schema_version': 1, 'python': sys.version, 'platform': platform.platform(),
              'source_manifest_sha256': digest(args.manifest), 'checked_results': checked,
              'all_saved_results_match': True, 'unit': 'dimensionless',
              'ulp_at_1e16': math.ulp(1e16), 'rounds': rounds}
    (args.output / 'trace-results.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'operation-traces.json').write_text(json.dumps(all_steps, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not args.no_plots:
        output['plot_environment'] = plot(rounds, args.output, args.plot_dependencies)
    (args.output / 'verification.json').write_text(json.dumps({key: value for key, value in output.items() if key != 'rounds'},
                                                              ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'checked_results': checked, 'all_saved_results_match': True,
                      'explicit_trace_groups': len(all_steps), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
