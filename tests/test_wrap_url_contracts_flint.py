"""URL contract tests for the wrapping helpers in factory.core.tui.wrapping."""

import os
import sys
import unittest

_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

from factory.core.tui.wrapping import (  # noqa: E402
    is_domain_label,
    is_domain_name,
    is_ipv4,
    is_ordered_list_marker,
    is_tld,
    is_valid_port,
    split_host_and_port,
    split_host_port_and_trailer,
)


class TestWrappingUrlContracts(unittest.TestCase):
    """Real behavior contract tests for the URL helper family."""

    def test_is_valid_port_contract(self):
        """Port 0 and 65535 are valid; 65536, -1, empty string and abc are invalid."""
        with self.subTest(port="0"):
            self.assertTrue(is_valid_port("0"))
        with self.subTest(port="65535"):
            self.assertTrue(is_valid_port("65535"))
        with self.subTest(port="65536"):
            self.assertFalse(is_valid_port("65536"))
        with self.subTest(port="-1"):
            self.assertFalse(is_valid_port("-1"))
        with self.subTest(port=""):
            self.assertFalse(is_valid_port(""))
        with self.subTest(port="abc"):
            self.assertFalse(is_valid_port("abc"))

    def test_is_ipv4_contract(self):
        """IPv4 hosts like 127.0.0.1 and 255.255.255.255 are valid; malformed ones are not."""
        with self.subTest(host="127.0.0.1"):
            self.assertTrue(is_ipv4("127.0.0.1"))
        with self.subTest(host="255.255.255.255"):
            self.assertTrue(is_ipv4("255.255.255.255"))
        with self.subTest(host="256.0.0.1"):
            self.assertFalse(is_ipv4("256.0.0.1"))
        with self.subTest(host="1.2.3"):
            self.assertFalse(is_ipv4("1.2.3"))
        with self.subTest(host="abc"):
            self.assertFalse(is_ipv4("abc"))

    def test_is_domain_name_contract(self):
        """Domains like example.com and sub.example.org are valid; localhost and bad hosts are not."""
        with self.subTest(host="example.com"):
            self.assertTrue(is_domain_name("example.com"))
        with self.subTest(host="sub.example.org"):
            self.assertTrue(is_domain_name("sub.example.org"))
        with self.subTest(host="localhost"):
            self.assertFalse(is_domain_name("localhost"))
        with self.subTest(host="-bad.com"):
            self.assertFalse(is_domain_name("-bad.com"))
        with self.subTest(host="bad-.com"):
            self.assertFalse(is_domain_name("bad-.com"))

    def test_is_tld_contract(self):
        """TLDs like com and RU are valid; short, numeric and non-ASCII labels are not."""
        with self.subTest(tld="com"):
            self.assertTrue(is_tld("com"))
        with self.subTest(tld="RU"):
            self.assertTrue(is_tld("RU"))
        with self.subTest(tld="x"):
            self.assertFalse(is_tld("x"))
        with self.subTest(tld="123"):
            self.assertFalse(is_tld("123"))
        with self.subTest(tld="рф"):
            self.assertFalse(is_tld("рф"))

    def test_is_domain_label_contract(self):
        """Labels like abc, a-b and a1 are valid; empty, hyphen-edge and underscore labels are not."""
        with self.subTest(label="abc"):
            self.assertTrue(is_domain_label("abc"))
        with self.subTest(label="a-b"):
            self.assertTrue(is_domain_label("a-b"))
        with self.subTest(label="a1"):
            self.assertTrue(is_domain_label("a1"))
        with self.subTest(label=""):
            self.assertFalse(is_domain_label(""))
        with self.subTest(label="-a"):
            self.assertFalse(is_domain_label("-a"))
        with self.subTest(label="a-"):
            self.assertFalse(is_domain_label("a-"))
        with self.subTest(label="a_b"):
            self.assertFalse(is_domain_label("a_b"))

    def test_split_host_and_port_contract(self):
        """example.com:443 splits into host and port; [::1] stays a whole host without port."""
        with self.subTest():
            host, port = split_host_and_port("example.com:443")
            self.assertEqual(host, "example.com")
            self.assertEqual(port, "443")
        with self.subTest():
            host, port = split_host_and_port("[::1]")
            self.assertEqual(host, "[::1]")
            self.assertIsNone(port)

    def test_split_host_port_and_trailer_contract(self):
        """example.com/path and example.com?query carry a trailer; example.com does not."""
        with self.subTest(trailer="path"):
            host, has_trailer = split_host_port_and_trailer("example.com/path")
            self.assertEqual(host, "example.com")
            self.assertTrue(has_trailer)
        with self.subTest(trailer="query"):
            host, has_trailer = split_host_port_and_trailer("example.com?query")
            self.assertEqual(host, "example.com")
            self.assertTrue(has_trailer)
        with self.subTest(trailer=None):
            host, has_trailer = split_host_port_and_trailer("example.com")
            self.assertEqual(host, "example.com")
            self.assertFalse(has_trailer)

    def test_is_ordered_list_marker_contract(self):
        """Markers like 1. and 2) are ordered list markers; plain numbers and arabic digits are not."""
        with self.subTest(marker="1."):
            self.assertTrue(is_ordered_list_marker("1.", "1"))
        with self.subTest(marker="2)"):
            self.assertTrue(is_ordered_list_marker("2)", "2"))
        with self.subTest(marker="abc."):
            self.assertFalse(is_ordered_list_marker("abc.", "abc"))
        with self.subTest(marker="1"):
            self.assertFalse(is_ordered_list_marker("1", "1"))
        with self.subTest(marker="arabic"):
            self.assertFalse(is_ordered_list_marker("١.", "١"))


if __name__ == "__main__":
    unittest.main()
