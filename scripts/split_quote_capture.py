"""Split a recorded quote tape around a known wall-clock interruption."""

import argparse
import csv
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path


def micros(stamp):
    value = datetime.fromisoformat(stamp)
    if value.tzinfo is None:
        raise ValueError('Use an offset-aware timestamp')
    return round(value.timestamp() * 1_000_000)


def split(source, destination, start, end):
    if start >= end:
        raise ValueError('Quarantine start must precede end')
    captures = sorted(source.glob('*.csv.gz'))
    if not captures:
        raise ValueError(f'No quote tapes in {source}')
    before, after = (destination / name for name in ('before', 'after'))
    destination.mkdir(parents=True)
    before.mkdir()
    after.mkdir()
    audit = []
    for path in captures:
        counts = [0, 0]
        discarded = 0
        previous = [-1, -1]
        with (gzip.open(path, 'rt', newline='') as stream,
              gzip.open(before / path.name, 'xt', newline='') as old,
              gzip.open(after / path.name, 'xt', newline='') as new):
            reader = csv.DictReader(stream)
            writers = [csv.DictWriter(out, fieldnames=reader.fieldnames) for out in (old, new)]
            for writer in writers:
                writer.writeheader()
            for row in reader:
                receipt = int(row['local_timestamp'])
                if start <= receipt < end:
                    discarded += 1
                    continue
                index = int(receipt >= end)
                if receipt < previous[index]:
                    raise ValueError(f'Clock disorder remains outside the quarantine in {path.name}')
                writers[index].writerow(row)
                previous[index] = receipt
                counts[index] += 1
        if not all(counts):
            raise ValueError(f'No quotes in one side of the quarantine in {path.name}')
        audit.append({'file': path.name, 'original_sha256': sha256(path.read_bytes()).hexdigest(),
                      'before_rows': counts[0], 'quarantined_rows': discarded, 'after_rows': counts[1]})
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('start')
    parser.add_argument('end')
    args = parser.parse_args()
    audit = split(args.source, args.destination, micros(args.start), micros(args.end))
    (args.destination / 'quarantine.json').write_text(json.dumps(
        {'quarantine_start': args.start, 'quarantine_end': args.end, 'files': audit}, indent=2) + '\n')


if __name__ == '__main__':
    main()
