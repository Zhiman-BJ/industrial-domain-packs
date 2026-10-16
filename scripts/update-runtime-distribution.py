#!/usr/bin/env python3
"""Record one released image identity in provenance/runtime-distribution.json."""
import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[1] / 'provenance' / 'runtime-distribution.json'

DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
HEX = re.compile(r'[0-9a-f]{64}')
TAG = re.compile(r'(pcb|chip)-v[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][0-9A-Za-z.-]+)?')


def load(path):
    lock = json.loads(path.read_text())
    if lock.get('schemaVersion') != 1 or not isinstance(lock.get('images'), list):
        raise ValueError(f'Unsupported lock schema in {path}')
    return lock


def entry(lock, image_id):
    matches = [image for image in lock['images'] if image['id'] == image_id]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one lock entry for {image_id}, found {len(matches)}')
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=LOCK_PATH, help='Lock file to update (default: the repository lock)')
    parser.add_argument('--id', required=True, help='Image id from the lock, e.g. industrial-pcb-runtime')
    parser.add_argument('--tag', required=True, help='Release tag carrying the image, e.g. pcb-v1.0.0')
    parser.add_argument('--digest', required=True, help='Pushed image digest (manifest or index)')
    parser.add_argument('--archive', required=True, help='Release asset file name of the oci-archive tarball')
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--archive-bytes', required=True, type=int)
    parser.add_argument('--config-digest', help='linux/amd64 config digest, when the pushed reference is an index')
    parser.add_argument('--commit', help='Source commit the image was built from')
    args = parser.parse_args()

    if not TAG.fullmatch(args.tag):
        parser.error(f'Tag must match {TAG.pattern}')
    if not DIGEST.fullmatch(args.digest):
        parser.error('digest must be a complete sha256:… value')
    if args.config_digest and not DIGEST.fullmatch(args.config_digest):
        parser.error('config-digest must be a complete sha256:… value')
    if not HEX.fullmatch(args.archive_sha256):
        parser.error('archive-sha256 must be 64 lowercase hex characters')
    if args.archive_bytes <= 0:
        parser.error('archive-bytes must be positive')

    lock = load(args.lock)
    image = entry(lock, args.id)
    image['current'] = {
        'tag': args.tag,
        'imageDigest': args.digest,
        **({'configDigest': args.config_digest} if args.config_digest else {}),
        'ociArchive': args.archive,
        'ociArchiveSha256': args.archive_sha256,
        'ociArchiveBytes': args.archive_bytes,
        **({'sourceCommit': args.commit} if args.commit else {}),
        'releasedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }

    with tempfile.NamedTemporaryFile('w', dir=args.lock.parent, delete=False) as staged:
        staged.write(json.dumps(lock, indent=2, ensure_ascii=True) + '\n')
        staged.flush()
    os.replace(staged.name, args.lock)
    print(f'Updated {args.lock}: {args.id} @ {args.tag} ({args.digest})')


if __name__ == '__main__':
    main()
