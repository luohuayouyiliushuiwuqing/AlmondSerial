"""codes：番号提取与规范化（纯逻辑、无网络）。"""

import unittest

from app.codes import extract_code, normalize_code


class TestNormalize(unittest.TestCase):
    def test_upper_and_hyphen(self):
        self.assertEqual(normalize_code("abp-123"), "ABP-123")
        self.assertEqual(normalize_code("ssis_001"), "SSIS-001")
        self.assertEqual(normalize_code("  mide  999  "), "MIDE-999")
        self.assertEqual(normalize_code("FC2－PPV－1234567"), "FC2-PPV-1234567")

    def test_no_separator_inserts_hyphen(self):
        self.assertEqual(normalize_code("SSIS001"), "SSIS-001")
        self.assertEqual(normalize_code("MIDE999"), "MIDE-999")

    def test_numeric_code_kept(self):
        self.assertEqual(normalize_code("081720-001"), "081720-001")
        self.assertEqual(normalize_code("012218_001"), "012218-001")

    def test_empty(self):
        self.assertEqual(normalize_code(""), "")
        self.assertEqual(normalize_code(None), "")


class TestExtractCode(unittest.TestCase):
    def test_last_path_segment(self):
        self.assertEqual(extract_code("https://site.com/ABP-123"), "ABP-123")

    def test_path_with_extension(self):
        self.assertEqual(extract_code("https://site.com/detail/ssis001.html"), "SSIS-001")

    def test_query_value(self):
        self.assertEqual(extract_code("https://site.com/watch?v=MIDE999"), "MIDE-999")

    def test_multi_group_code(self):
        self.assertEqual(extract_code("https://site.com/FC2-PPV-1234567"), "FC2-PPV-1234567")

    def test_slug_suffix_trimmed(self):
        self.assertEqual(extract_code("https://missav.live/en/abp-123-english-subtitle"), "ABP-123")
        self.assertEqual(extract_code("https://site.com/ssis-001-uncensored-leaked"), "SSIS-001")
        self.assertEqual(extract_code("https://site.com/abp-123/english-subtitle"), "ABP-123")

    def test_numeric_date_serial_code(self):
        self.assertEqual(extract_code("https://site.com/081720-001"), "081720-001")
        self.assertEqual(extract_code("https://site.com/v/012218_001"), "012218-001")
        self.assertEqual(extract_code("081720-001"), "081720-001")

    def test_site_prefix_stripped(self):
        self.assertEqual(extract_code("caribbeancom-052626-001"), "052626-001")
        self.assertEqual(extract_code("pondo-041025_001"), "041025-001")
        self.assertEqual(extract_code("caribbeancom-052626-001-english-subtitle"), "052626-001")
        self.assertEqual(
            extract_code("https://www.caribbeancom.com/moviepages/052626-001/index.html"),
            "052626-001")
        self.assertEqual(
            extract_code("https://www.1pondo.tv/movies/041025_001/"), "041025-001")

    def test_site_prefix_rules_spare_normal_codes(self):
        self.assertEqual(extract_code("https://site.com/FC2-PPV-1234567"), "FC2-PPV-1234567")
        self.assertEqual(extract_code("https://site.com/SSIS-001"), "SSIS-001")
        self.assertEqual(extract_code("https://site.com/T28-123"), "T28-123")

    def test_bare_numeric_not_code(self):
        self.assertIsNone(extract_code("https://site.com/item/12345"))
        self.assertIsNone(extract_code("https://site.com/2020-01"))

    def test_bare_code_string(self):
        self.assertEqual(extract_code("ABP-123"), "ABP-123")

    def test_denylist_word_ignored(self):
        self.assertIsNone(extract_code("https://site.com/watch/page2"))
        self.assertIsNone(extract_code("https://site.com/video01.html"))

    def test_date_not_a_code(self):
        self.assertIsNone(extract_code("https://site.com/2020/01/01"))
        self.assertIsNone(extract_code("https://site.com/2020-01-01"))

    def test_no_code(self):
        self.assertIsNone(extract_code("https://example.com/about"))


if __name__ == "__main__":
    unittest.main()
