import sys, pathlib
rows = pathlib.Path(sys.argv[1]).read_text().splitlines()[1:]
print('rows', len(rows))
