"""importer：txt / csv / html 文件解析 → 链接列表（去重 + 域名 + 番号标注）。"""

import unittest

from app import importer


class TestParseTxt(unittest.TestCase):
    def test_extract_urls(self):
        text = "https://a.com/ABP-123\nhttps://b.com/SSIS001\nno url here\n"
        links = importer.parse(text.encode("utf-8"), "x.txt")
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["url"], "https://a.com/ABP-123")
        self.assertEqual(links[0]["code"], "ABP-123")
        self.assertEqual(links[0]["domain"], "a.com")
        self.assertEqual(links[1]["code"], "SSIS-001")

    def test_dedup(self):
        text = "https://a.com/ABP-123\nhttps://a.com/ABP-123\n"
        self.assertEqual(len(importer.parse(text.encode(), "x.txt")), 1)

    def test_trailing_punct_stripped(self):
        text = "https://a.com/ABP-123,\n"
        links = importer.parse(text.encode(), "x.txt")
        self.assertEqual(links[0]["url"], "https://a.com/ABP-123")


class TestParseCsv(unittest.TestCase):
    def test_urls_in_cells(self):
        csv_text = 'title,url,note\n"x",https://a.com/ABP-123,ok\n"y",https://b.com/MIDE-999,\n'
        links = importer.parse(csv_text.encode("utf-8-sig"), "x.csv")
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["code"], "ABP-123")


class TestParseHtml(unittest.TestCase):
    def test_hrefs_extracted(self):
        html = ('<html><body>'
                '<a href="https://a.com/ABP-123">x</a>'
                '<a href="https://b.com/SSIS-001">y</a>'
                '<a href="/relative">z</a>'
                '</body></html>')
        links = importer.parse(html.encode(), "x.html")
        urls = [l["url"] for l in links]
        self.assertIn("https://a.com/ABP-123", urls)
        self.assertIn("https://b.com/SSIS-001", urls)
        self.assertNotIn("/relative", urls)

    def test_unrecognized_code(self):
        html = '<a href="https://example.com/about">x</a>'
        links = importer.parse(html.encode(), "x.html")
        self.assertEqual(len(links), 1)
        self.assertIsNone(links[0]["code"])


if __name__ == "__main__":
    unittest.main()
