import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
from email.message import Message

spec = importlib.util.spec_from_file_location('verifier', Path(__file__).parents[1] / 'scripts/verify-release-artifact.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class RegistryVerification(unittest.TestCase):
    def test_rejects_incomplete_digests(self):
        with self.assertRaises(ValueError):
            verifier.validate_digest('sha256:ef4dcd4278c46d3efcd28c8db273d5957d68efda')
        with self.assertRaises(ValueError):
            verifier.validate_hex('ef4dcd4278c46d3efcd28c8db273d5957d68efda0286')

    def test_hashes_complete_download_and_rejects_wrong_bytes(self):
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime')
        data = b'complete immutable bytes'
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        registry.request = lambda *args: io.BytesIO(data)
        self.assertEqual(registry.blob(digest, len(data))['size'], len(data))
        with self.assertRaises(ValueError):
            registry.blob('sha256:' + '0' * 64, len(data))
        with self.assertRaises(ValueError):
            registry.blob(digest, len(data) + 1)

    def test_ranges_cover_all_bytes_and_validate_exact_offsets(self):
        data = b'a' * (16 * 1024 * 1024) + b'final bytes'
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        class Response(io.BytesIO):
            status = 206
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime', jobs=2)
        def request(kind, reference, headers):
            begin, end = map(int, headers['Range'][6:].split('-'))
            response = Response(data[begin:end + 1])
            response.headers = {'Content-Range': f'bytes {begin}-{end}/{len(data)}'}
            return response
        registry.request = request
        self.assertEqual(registry.blob(digest, len(data))['digest'], digest)
        def wrong_range(kind, reference, headers):
            response = request(kind, reference, headers)
            response.headers = {'Content-Range': 'bytes 1-8/10'}
            return response
        registry.request = wrong_range
        with self.assertRaisesRegex(ValueError, 'exact range'):
            registry.blob(digest, len(data))

    def test_credentials_never_follow_an_insecure_token_realm(self):
        registry = verifier.PublicRegistry('https://registry.example', 'private/runtime', ('robot', 'secret'))
        headers = Message()
        headers['WWW-Authenticate'] = 'Bearer realm="http://registry.example/service/token",service="ghcr.io"'
        error = urllib.error.HTTPError('https://registry.example/v2/', 401, 'Unauthorized', headers, None)
        with patch.object(verifier.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'token realm'):
                registry.request('manifests', 'sha256:' + '0' * 64)

    def test_single_manifest_checks_actual_config_platform_and_all_blobs(self):
        config = json.dumps({'os': 'linux', 'architecture': 'arm64'}).encode()
        layer = b'layer'
        digest = lambda b: 'sha256:' + hashlib.sha256(b).hexdigest()
        descriptor = lambda b: {'digest': digest(b), 'size': len(b)}
        manifest = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                               'config': descriptor(config), 'layers': [descriptor(layer)]}).encode()
        data = {digest(config): config, digest(layer): layer, digest(manifest): manifest}
        class Response(io.BytesIO):
            headers = {}
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime')
        registry.request = lambda kind, ref, *args: Response(data[ref])
        with self.assertRaisesRegex(ValueError, 'platform'):
            registry.manifest(digest(manifest), 'linux/amd64')
        result = registry.manifest(digest(manifest), 'linux/arm64')
        self.assertEqual(result['platform'], 'linux/arm64')
        data[digest(layer)] = b'corrupt'
        with self.assertRaisesRegex(ValueError, 'identity'):
            registry.manifest(digest(manifest), 'linux/arm64')

    def test_metadata_mode_never_downloads_layers_but_validates_config(self):
        config = json.dumps({'os': 'linux', 'architecture': 'amd64'}).encode()
        digest = lambda b: 'sha256:' + hashlib.sha256(b).hexdigest()
        manifest = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'digest': digest(config), 'size': len(config)},
            'layers': [{'digest': 'sha256:' + 'a' * 64, 'size': 1000000000}]}).encode()
        class Response(io.BytesIO):
            headers = {}
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime')
        reads = []
        def request(kind, ref, *args):
            reads.append(ref)
            return Response({digest(manifest): manifest, digest(config): config}[ref])
        registry.request = request
        result = registry.manifest(digest(manifest), 'linux/amd64', metadata_only=True)
        self.assertEqual(reads, [digest(manifest), digest(config)])
        self.assertEqual(result['verification'], 'metadata-only')
        with self.assertRaisesRegex(ValueError, 'platform'):
            registry.manifest(digest(manifest), 'linux/arm64', metadata_only=True)

    def test_head_verification_checks_every_descriptor_without_layer_gets(self):
        config = json.dumps({'os': 'linux', 'architecture': 'amd64'}).encode()
        digest = lambda b: 'sha256:' + hashlib.sha256(b).hexdigest()
        layer_digest = 'sha256:' + 'a' * 64
        manifest = json.dumps({'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'digest': digest(config), 'size': len(config)},
            'layers': [{'digest': layer_digest, 'size': 1000000000}]}).encode()
        class Response(io.BytesIO):
            headers = {}
            status = 200
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime')
        heads = []
        def request(kind, ref, extra_headers=None, method='GET'):
            if method == 'HEAD':
                heads.append(ref)
                response = Response()
                response.headers = {'Docker-Content-Digest': ref, 'Content-Length': str(len(config) if ref == digest(config) else 1000000000)}
                return response
            return Response({digest(manifest): manifest, digest(config): config}[ref])
        registry.request = request
        result = registry.manifest(digest(manifest), blob_heads=True)
        self.assertEqual(result['verification'], 'registry-heads')
        self.assertEqual(heads, [digest(config), layer_digest])

    def test_blob_head_fails_closed_on_wrong_or_missing_headers_status_or_request(self):
        digest = 'sha256:' + 'a' * 64
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime')
        class Response(io.BytesIO):
            status = 200
            headers = {'Docker-Content-Digest': digest, 'Content-Length': '5'}
        registry.request = lambda *a, **kw: Response()
        self.assertEqual(registry.blob_head(digest, 5)['size'], 5)
        for headers, status in [({}, 200), ({'Docker-Content-Digest': digest, 'Content-Length': '6'}, 200),
                ({'Docker-Content-Digest': 'sha256:' + 'b' * 64, 'Content-Length': '5'}, 200),
                ({'Docker-Content-Digest': digest, 'Content-Length': '5'}, 404)]:
            Response.headers, Response.status = headers, status
            with self.assertRaises(ValueError): registry.blob_head(digest, 5)
        def fail(*args, **kwargs): raise urllib.error.URLError('unavailable')
        registry.request = fail
        with self.assertRaises(urllib.error.URLError): registry.blob_head(digest, 5)

    def test_manifest_digest_binds_descriptors(self):
        class Response(io.BytesIO):
            headers = {}
        registry = verifier.PublicRegistry('https://registry.example', 'public/runtime')
        registry.request = lambda *args: Response(b'{"schemaVersion":2}')
        with self.assertRaisesRegex(ValueError, 'Manifest digest mismatch'):
            registry.manifest('sha256:' + '0' * 64)


class AssetVerification(unittest.TestCase):
    def test_verifies_complete_download(self):
        data = b'release asset bytes'
        sha = hashlib.sha256(data).hexdigest()
        with patch.object(verifier.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
            with patch('builtins.print'):
                verifier.verify_asset('https://github.com/org/repo/releases/download/v1/x.tar', sha, len(data))
        with patch.object(verifier.urllib.request, 'urlopen', return_value=io.BytesIO(data + b'x')):
            with self.assertRaises(ValueError):
                verifier.verify_asset('https://github.com/org/repo/releases/download/v1/x.tar', sha, len(data))
        with patch.object(verifier.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
            with self.assertRaises(ValueError):
                verifier.verify_asset('https://github.com/org/repo/releases/download/v1/x.tar', '0' * 64)

    def test_rejects_non_https_or_credentialed_urls(self):
        with self.assertRaises(ValueError):
            verifier.verify_asset('http://example.com/x.tar', '0' * 64)
        with self.assertRaises(ValueError):
            verifier.verify_asset('https://user:pass@example.com/x.tar', '0' * 64)


if __name__ == '__main__':
    unittest.main()
