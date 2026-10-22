from __future__ import annotations

import base64
import hashlib
import unittest
from html.parser import HTMLParser
from factory.web_ui.artifact_preview import MAX_PREVIEW_HTML_BYTES, prepare_html_preview


class ArtifactPreviewTest(unittest.TestCase):
    def test_pinned_three_library_is_embedded_without_network_or_version_substitution(self):
        for url in ["https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js", "https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js", "https://unpkg.com/three@0.128.0/build/three.min.js"]:
            with self.subTest(url=url):
                source = ('<!doctype html><script src="' + url + '"></script><script>window.test = THREE.REVISION;</script>').encode()
                output = prepare_html_preview(source)
                values = []
                class Parser(HTMLParser):
                    def handle_starttag(self, tag, attrs):
                        if tag == 'script':
                            values.extend(v for k, v in attrs if k == 'src')
                Parser().feed(output.decode())
                self.assertEqual(len(values), 1)
                data = base64.b64decode(values[0].split(',', 1)[1])
                self.assertEqual(hashlib.sha256(data).hexdigest(), '9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2')
                self.assertIn(b'window.test = THREE.REVISION;', output)
                self.assertIn(url.encode(), source)

    def test_unknown_versions_domains_and_markup_lookalikes_are_not_rewritten(self):
        samples = [
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r129/three.min.js"></script>',
            '<script src="https://unpkg.com.evil.invalid/three@0.128.0/build/three.min.js"></script>',
            '<script src="http://unpkg.com/three@0.128.0/build/three.min.js"></script>',
            '<script src="https://user@unpkg.com/three@0.128.0/build/three.min.js"></script>',
            '<script src="https://unpkg.com:443/three@0.128.0/build/three.min.js"></script>',
            '<!-- <script src="https://unpkg.com/three@0.128.0/build/three.min.js"></script> -->',
            '<script>const text = \'<script src="https://unpkg.com/three@0.128.0/build/three.min.js">\';</script>',
        ]
        for source in samples:
            self.assertEqual(prepare_html_preview(source.encode()), source.encode())

    def test_markup_attributes_unicode_and_original_download_bytes_are_preserved(self):
        source = '<p>Привет\u2028мир</p>\r\n<script async crossorigin="anonymous" src="https://unpkg.com/three@0.128.0/build/three.min.js"></script><p>after</p>'
        before = source.encode()
        after = prepare_html_preview(before).decode()
        self.assertTrue(after.startswith('<p>Привет\u2028мир</p>\r\n<script async crossorigin="anonymous" src="data:'))
        self.assertTrue(after.endswith('</script><p>after</p>'))
        self.assertEqual(before, source.encode())

    def test_plain_html_invalid_encoding_and_oversize_input_remain_unchanged(self):
        for source in [b'<html>plain</html>', b'\xff\xfe', b'<![malformed]>', b'x' * (MAX_PREVIEW_HTML_BYTES + 1)]:
            self.assertEqual(prepare_html_preview(source), source)

    def test_expansion_is_bounded_and_duplicate_src_is_not_reinterpreted(self):
        script = '<script src="https://unpkg.com/three@0.128.0/build/three.min.js"></script>'
        output = prepare_html_preview((script * 8).encode())
        self.assertEqual(output.count(b'data:text/javascript;base64,'), 4)
        self.assertEqual(output.count(b'https://unpkg.com'), 4)
        duplicate = '<script src="https://unpkg.com/three@0.128.0/build/three.min.js" src="https://other.invalid/a"></script>'
        self.assertEqual(prepare_html_preview(duplicate.encode()), duplicate.encode())

    def test_xhtml_self_closing_script_remains_well_formed(self):
        import xml.etree.ElementTree as ET
        source = b'<html xmlns="http://www.w3.org/1999/xhtml"><script src="https://unpkg.com/three@0.128.0/build/three.min.js" /></html>'
        output = prepare_html_preview(source)
        self.assertIn(b'data:text/javascript;base64,', output)
        ET.fromstring(output)
