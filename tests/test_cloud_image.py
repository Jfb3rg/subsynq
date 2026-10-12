import hashlib
import io
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/app')
import cloud_image


class RegistryPreflightTests(unittest.TestCase):
    def check(self, prepared='1', corrupt=False):
        config = json.dumps({'os': 'linux', 'architecture': 'amd64',
                             'config': {'Labels': {'org.subsynq.cloud.prepared': prepared}}}).encode()
        config_digest = 'sha256:' + hashlib.sha256(config).hexdigest()
        manifest = json.dumps({'config': {'digest': config_digest}}).encode()
        digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()
        calls = []

        def open_request(request, timeout):
            calls.append(request.full_url)
            data = config if '/blobs/' in request.full_url else manifest
            return io.BytesIO(data + b'x' if corrupt else data)

        with patch.object(cloud_image, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            result = cloud_image.verify_image('registry.test/team/cloud@' + digest)
        return result, calls

    def test_complete_prepared_image_is_checked_without_downloading_layers(self):
        result, calls = self.check()
        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertIn('/manifests/sha256:', calls[0])
        self.assertIn('/blobs/sha256:', calls[1])

    def test_stock_image_cannot_pass_as_prepared(self):
        with self.assertRaisesRegex(ValueError, 'inte en färdig'):
            self.check(prepared='0')

    def test_registry_content_must_match_pinned_digest(self):
        with self.assertRaisesRegex(ValueError, 'Fel digest'):
            self.check(corrupt=True)
