#!/usr/bin/env python3
"""Verify released image bytes anonymously: OCI registries (GHCR default) and plain HTTPS release assets."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import concurrent.futures
import time
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = ', '.join(['application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json', 'application/vnd.docker.distribution.manifest.v2+json'])


def validate_digest(digest):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Expected a complete sha256 digest (64 lowercase hex characters)')
    return digest


def validate_hex(value):
    if not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Expected a bare sha256 hash (64 lowercase hex characters)')
    return value


class PublicRegistry:
    def __init__(self, host, repository, credentials=None, jobs=8):
        self.host = host.rstrip('/')
        self.repository = repository
        self.token = None
        self.credentials = credentials
        self.jobs = jobs
        self.results = []

    def request(self, kind, reference, extra_headers=None):
        url = f'{self.host}/v2/{self.repository}/{kind}/{reference}'
        headers = {'Accept': ACCEPT, 'Cache-Control': 'no-cache'}
        headers.update(extra_headers or {})
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)
        except urllib.error.HTTPError as error:
            if error.code != 401 or self.token:
                raise
            challenge = error.headers.get('WWW-Authenticate', '')
            values = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            realm = values.get('realm', '')
            if urllib.parse.urlparse(realm).scheme != 'https' or urllib.parse.urlparse(realm).netloc != urllib.parse.urlparse(self.host).netloc:
                raise ValueError('Registry token realm must remain on the configured registry host')
            query = urllib.parse.urlencode({'service': values.get('service', 'registry'), 'scope': f'repository:{self.repository}:pull'})
            token_headers = {}
            if self.credentials:
                token_headers['Authorization'] = 'Basic ' + base64.b64encode((self.credentials[0] + ':' + self.credentials[1]).encode()).decode()
            with urllib.request.urlopen(urllib.request.Request(realm + '?' + query, headers=token_headers), timeout=120) as response:
                body = json.load(response)
                self.token = body.get('token') or body['access_token']
            return self.request(kind, reference, extra_headers)

    def blob(self, digest, size=None):
        validate_digest(digest)
        start = time.monotonic()
        h = hashlib.sha256()
        count = 0
        if size is not None and size > 16 * 1024 * 1024:
            # Bounded parallel ranges avoid reverse-proxy per-connection throttling.
            # Hash in byte order, including every byte exactly once.
            step = 8 * 1024 * 1024
            def read_range(offset):
                end = min(size, offset + step) - 1
                for attempt in range(3):
                    try:
                        with self.request('blobs', digest, {'Range': f'bytes={offset}-{end}'}) as response:
                            data = response.read(step + 1)
                            if response.status != 206 or response.headers.get('Content-Range') != f'bytes {offset}-{end}/{size}' or len(data) != end - offset + 1:
                                raise ValueError('Registry did not honor exact range')
                            return data
                    except (OSError, urllib.error.URLError):
                        if attempt == 2:
                            raise
            # Warm the anonymous token before starting concurrent requests.
            first = read_range(0)
            h.update(first)
            count += len(first)
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as pool:
                for batch in range(step, size, self.jobs * step):
                    for data in pool.map(read_range, range(batch, min(size, batch + self.jobs * step), step)):
                        h.update(data)
                        count += len(data)
        else:
            with self.request('blobs', digest) as response:
                while data := response.read(8 * 1024 * 1024):
                    count += len(data)
                    h.update(data)
        actual = 'sha256:' + h.hexdigest()
        if actual != digest or (size is not None and count != size):
            raise ValueError(f'Blob identity mismatch: expected {digest}/{size}, received {actual}/{count}')
        result = {'digest': actual, 'size': count, 'seconds': round(time.monotonic() - start, 3)}
        self.results.append(result)
        return result

    def manifest(self, reference, platform='linux/amd64'):
        with self.request('manifests', reference) as response:
            raw = response.read(16 * 1024 * 1024)
            declared = response.headers.get('Docker-Content-Digest')
        actual = 'sha256:' + hashlib.sha256(raw).hexdigest()
        if reference.startswith('sha256:') and actual != validate_digest(reference):
            raise ValueError('Manifest digest mismatch')
        if declared and declared != actual:
            raise ValueError('Manifest response digest mismatch')
        body = json.loads(raw)
        result = {'digest': actual, 'mediaType': body['mediaType'], 'size': len(raw)}
        if 'manifests' in body:
            parts = platform.split('/')
            os_name, arch = parts[:2]
            matches = [m for m in body['manifests'] if m.get('platform', {}).get('os') == os_name and m.get('platform', {}).get('architecture') == arch and (len(parts) == 2 or m.get('platform', {}).get('variant') == parts[2])]
            if len(matches) != 1:
                raise ValueError('Expected exactly one manifest for ' + platform)
            result['platformManifest'] = self.manifest(matches[0]['digest'], platform)
            result['platform'] = result['platformManifest']['platform']
        else:
            config_descriptor = body['config']
            validate_digest(config_descriptor['digest'])
            with self.request('blobs', config_descriptor['digest']) as response:
                raw_config = response.read(16 * 1024 * 1024 + 1)
            if (len(raw_config) > 16 * 1024 * 1024
                    or len(raw_config) != config_descriptor['size']
                    or 'sha256:' + hashlib.sha256(raw_config).hexdigest() != config_descriptor['digest']):
                raise ValueError('Image config identity mismatch')
            config = json.loads(raw_config)
            parts = platform.split('/')
            if (config.get('os') != parts[0] or config.get('architecture') != parts[1]
                    or (len(parts) == 3 and config.get('variant') != parts[2])):
                raise ValueError('Image config platform mismatch')
            result['platform'] = platform
            descriptors = [body['config'], *body.get('layers', [])]
            for descriptor in descriptors:
                self.blob(descriptor['digest'], descriptor['size'])
            result.update(configDigest=body['config']['digest'], downloadSize=sum(d['size'] for d in descriptors), layerCount=len(body.get('layers', [])))
        return result


def https_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query:
        raise ValueError('Expected an HTTPS URL without credentials or query')
    return parsed


def verify_asset(url, sha256, size=None, output=None):
    validate_hex(sha256)
    https_url(url)
    start = time.monotonic()
    h = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Cache-Control': 'no-cache'}), timeout=120) as response:
        while data := response.read(8 * 1024 * 1024):
            count += len(data)
            h.update(data)
    actual = h.hexdigest()
    if actual != sha256 or (size is not None and count != size):
        raise ValueError(f'Asset mismatch: expected {sha256}/{size}, received {actual}/{count}')
    evidence = {
        'schemaVersion': 1,
        'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'url': url,
        'authentication': 'anonymous-https',
        'identity': {'sha256': actual, 'size': count, 'seconds': round(time.monotonic() - start, 3)},
    }
    encoded = json.dumps(evidence, indent=2) + '\n'
    if output:
        with open(output, 'w') as file:
            file.write(encoded)
    print(encoded, end='')


def docker_credentials(host):
    config = json.loads((Path(os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker'))) / 'config.json').read_text())
    entry = config.get('auths', {}).get(host, {})
    helper = config.get('credHelpers', {}).get(host) or config.get('credsStore')
    if helper:
        result = subprocess.run(['docker-credential-' + helper, 'get'], input=host + '\n', text=True, capture_output=True, check=True)
        value = json.loads(result.stdout)
        return value['Username'], value['Secret']
    if entry.get('auth'):
        return tuple(base64.b64decode(entry['auth']).decode().split(':', 1))
    raise ValueError('No existing Docker credential for the requested private registry')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    image = commands.add_parser('image', help='Verify a /v2/<repository>/manifests|blobs/<digest> URL anonymously (default host: ghcr.io)')
    image.add_argument('url')
    image.add_argument('--size', type=int)
    image.add_argument('--platform', default='linux/amd64')
    image.add_argument('--jobs', type=int, choices=range(1, 33), default=8, metavar='1..32')
    image.add_argument('--output')
    image.add_argument('--docker-credentials', action='store_true', help='Authenticate a private image using existing Docker credentials; never emit credentials')

    asset = commands.add_parser('asset', help='Verify a plain HTTPS release asset against its sha256')
    asset.add_argument('url')
    asset.add_argument('--sha256', required=True)
    asset.add_argument('--size', type=int)
    asset.add_argument('--output')

    args = parser.parse_args()
    if args.command == 'asset':
        verify_asset(args.url, args.sha256, args.size, args.output)
        return
    url = urllib.parse.urlparse(args.url)
    match = re.fullmatch(r'/v2/(.+)/(blobs|manifests)/([^/]+)', url.path)
    if url.scheme != 'https' or not match or url.username or url.password or url.query:
        parser.error('Expected an HTTPS registry URL without credentials or query')
    repo, kind, reference = match.groups()
    validate_digest(reference)
    registry = PublicRegistry(f'{url.scheme}://{url.netloc}', repo, docker_credentials(url.netloc) if args.docker_credentials else None, args.jobs)
    identity = registry.blob(reference, args.size) if kind == 'blobs' else registry.manifest(reference, args.platform)
    evidence = {'schemaVersion': 1, 'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'url': args.url, 'authentication': 'docker-registry-bearer' if args.docker_credentials else 'anonymous-bearer', 'identity': identity, 'blobs': registry.results}
    encoded = json.dumps(evidence, indent=2) + '\n'
    if args.output:
        with open(args.output, 'w') as file:
            file.write(encoded)
    print(encoded, end='')


if __name__ == '__main__':
    main()
