import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import it_vacancy_scraper_v2 as scraper


def notice(**overrides):
    value = {
        "source": "Test PSC",
        "title": "Advertisement for IT Officer",
        "notice_type": "open_vacancy",
        "role": "computer officer",
        "level": "7th",
        "date_found": "2083-01-02",
        "calendar": "BS",
        "date_AD": "2026-04-15",
        "date_BS": "2083-01-02",
        "date_source": "PDF",
        "match_evidence": "PDF text passed open competition, role, level, and date rules",
        "document_link": "https://example.gov.np/notice.pdf?download=1",
        "match_score": 100,
        "it_status": "rule_match",
        "link": "https://example.gov.np/notice.pdf?download=1",
    }
    value.update(overrides)
    return value


class NoticeTriageTests(unittest.TestCase):
    def test_triage_distinguishes_vacancy_updates_results_and_other(self):
        self.assertEqual(scraper.triage_notice("Advertisement for vacant posts"), "candidate")
        self.assertEqual(
            scraper.triage_notice("Open competition advertisement for vacant posts"),
            "open_vacancy",
        )
        self.assertEqual(scraper.triage_notice("Promotion advertisement"), "promotion")
        self.assertEqual(scraper.triage_notice("Deadline extension notice"), "update")
        self.assertEqual(scraper.triage_notice("Final result of examination"), "result")
        self.assertEqual(scraper.triage_notice("Office contact details"), "other")

    def test_extract_collects_candidate_for_pdf_verification(self):
        html = """
        <table><tr>
          <td>2083-01-02</td>
          <td><a href="/files/ad.pdf">Advertisement for vacant posts</a></td>
        </tr></table>
        """
        soup = scraper.BeautifulSoup(html, "html.parser")
        results = scraper.extract(soup, "https://example.gov.np/notices", "Test PSC", set())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["notice_type"], "candidate")
        self.assertEqual(results[0]["link"], "https://example.gov.np/files/ad.pdf")

    def test_extract_excludes_result_notices(self):
        html = '<a href="/result.pdf">Result of vacancy examination</a>'
        soup = scraper.BeautifulSoup(html, "html.parser")
        self.assertEqual(
            scraper.extract(soup, "https://example.gov.np/", "Test PSC", set()),
            [],
        )

    def test_promotion_category_page_is_not_a_candidate(self):
        soup = scraper.BeautifulSoup(
            '<a href="/category/advertisement-promotion">Advertisement Promotion</a>',
            "html.parser",
        )
        self.assertEqual(
            scraper.extract(soup, "https://example.gov.np/", "Test PSC", set()),
            [],
        )

    def test_pdf_url_detection_in_path_and_query(self):
        self.assertTrue(scraper._is_pdf_url("https://example.gov.np/files/ad.pdf"))
        self.assertTrue(
            scraper._is_pdf_url("https://example.gov.np/download?file=ad.pdf")
        )
        self.assertFalse(scraper._is_pdf_url("https://example.gov.np/notices/123"))


class PdfRuleTests(unittest.TestCase):
    TODAY = scraper.date(2026, 11, 6)

    def setUp(self):
        self.candidate = {
            "source": "Test PSC",
            "title": "Advertisement number 123",
            "listing_context": "",
            "link": "https://example.gov.np/notice",
        }

    def test_accepts_recent_open_competition_seventh_level_it_role(self):
        text = """
        Open and inclusive competitive examination
        Computer Officer, 7th Level
        Publication date: 2026-10-15
        """
        result = scraper.verify_notice(self.candidate, text, self.TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "computer officer")
        self.assertEqual(result["level"], "7th")
        self.assertEqual(result["date_AD"], "2026-10-15")

    def test_accepts_role_when_level_is_not_stated(self):
        text = """
        Open competition
        Information Technology Officer
        Published on: 2026-10-15
        """
        result = scraper.verify_notice(self.candidate, text, self.TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["level"], "Not stated")

    def test_rejects_promotion_only_notices(self):
        text = """
        Internal promotion examination
        Computer Officer, 7th Level
        Publication date: 2026-10-15
        """
        self.assertIsNone(scraper.verify_notice(self.candidate, text, self.TODAY))

    def test_rejects_information_officer_without_it_role(self):
        text = """
        Open competition
        Information Officer, 7th Level
        Publication date: 2026-10-15
        """
        self.assertIsNone(scraper.verify_notice(self.candidate, text, self.TODAY))

    def test_rejects_lower_or_higher_levels(self):
        for level in ("5th Level", "6th Level", "8th Level", "9th Level"):
            with self.subTest(level=level):
                text = (
                    "Open competition\nComputer Officer, "
                    f"{level}\nPublication date: 2026-10-15"
                )
                self.assertIsNone(
                    scraper.verify_notice(self.candidate, text, self.TODAY)
                )

    def test_rejects_old_or_undated_notices(self):
        old = (
            "Open competition\nComputer Officer, 7th Level\n"
            "Publication date: 2026-08-01"
        )
        undated = "Open competition\nComputer Officer, 7th Level"
        self.assertIsNone(scraper.verify_notice(self.candidate, old, self.TODAY))
        self.assertIsNone(scraper.verify_notice(self.candidate, undated, self.TODAY))

    def test_rejects_mixed_notice_when_role_is_not_in_open_section(self):
        text = """
        Open competition for administrative posts
        Promotion competition for Computer Officer, 7th Level
        Publication date: 2026-10-15
        """
        self.assertIsNone(scraper.verify_notice(self.candidate, text, self.TODAY))

    def test_reads_selectable_pdf_text_with_size_limit_path(self):
        content = b"%PDF-1.4 test fixture"
        response = Mock(
            headers={"Content-Length": str(len(content))},
            iter_content=Mock(return_value=iter([content])),
        )
        reader = Mock()
        reader.pages = [Mock(extract_text=Mock(return_value="Computer Officer"))]
        with patch.object(scraper, "allowed_by_robots", return_value=True), \
             patch.object(scraper, "_request", return_value=response), \
             patch.object(scraper, "PdfReader", return_value=reader):
            text = scraper._download_pdf_text("https://example.gov.np/test.pdf")
        self.assertIn("Computer Officer", text)
        response.close.assert_called_once()

    def test_accepts_nepali_open_competition_and_seventh_level_terms(self):
        text = """
        खुला तथा समावेशी प्रतियोगितात्मक परीक्षा
        कम्प्युटर अधिकृत ७ औं तह
        प्रकाशन मिति: २०८३-०६-२९
        """
        result = scraper.verify_notice(self.candidate, text, self.TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["level"], "7th")
        self.assertEqual(result["date_source"], "PDF")


class PersistenceTests(unittest.TestCase):
    def test_new_unchanged_and_changed_statuses_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "notices.sqlite3")
            first = notice()
            scraper.save_notices([first], database, now="2026-11-06T00:00:00+00:00")
            self.assertEqual(first["status"], "NEW")

            unchanged = notice()
            scraper.save_notices(
                [unchanged], database, now="2026-11-07T00:00:00+00:00"
            )
            self.assertEqual(unchanged["status"], "")

            changed = notice(title="Amended advertisement for IT Officer")
            scraper.save_notices(
                [changed], database, now="2026-11-08T00:00:00+00:00"
            )
            self.assertEqual(changed["status"], "CHANGED")

            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT title, first_seen, last_seen FROM notices"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], "Amended advertisement for IT Officer")
            self.assertEqual(row[1], "2026-11-06T00:00:00+00:00")
            self.assertEqual(row[2], "2026-11-08T00:00:00+00:00")


class HtmlReportTests(unittest.TestCase):
    def test_report_shows_direct_link_and_rule_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = os.path.join(directory, "report.html")
            item = notice(title="IT Officer <script>alert(1)</script>")
            item["status"] = "NEW"
            scraper.write_html_report([item], report_path)

            with open(report_path, encoding="utf-8") as report:
                content = report.read()

            self.assertIn("href=\"https://example.gov.np/notice.pdf?download=1\"", content)
            self.assertIn("Open PDF", content)
            self.assertIn("Rule checks passed", content)
            self.assertIn("PDF text passed open competition", content)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
            self.assertNotIn("<script>alert(1)</script>", content)

    def test_empty_report_has_clear_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = os.path.join(directory, "report.html")
            scraper.write_html_report(
                [],
                report_path,
                [("Koshi PSC", "WARN", "No listing links found")],
            )
            with open(report_path, encoding="utf-8") as report:
                content = report.read()
        self.assertIn("No notices passed all configured filters in this scan.", content)
        self.assertIn("Koshi PSC", content)
        self.assertIn("Check warnings", content)


class RobotsPolicyTests(unittest.TestCase):
    def setUp(self):
        scraper._ROBOTS_CACHE.clear()
        scraper._LAST_REQUEST_AT.clear()

    def test_disallowed_url_is_rejected(self):
        response = Mock(status_code=200, url="https://example.gov.np/robots.txt")
        response.text = "User-agent: *\nDisallow: /private\n"
        with patch.object(scraper, "_request", return_value=response):
            self.assertFalse(
                scraper.allowed_by_robots("https://example.gov.np/private/file.pdf")
            )

    def test_unavailable_robots_file_fails_closed(self):
        with patch.object(
            scraper,
            "_request",
            side_effect=requests.ConnectionError("offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "could not check robots.txt"):
                scraper.allowed_by_robots("https://example.gov.np/notices")

    def test_missing_robots_file_allows_crawl(self):
        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError("404", response=response)
        with patch.object(scraper, "_request", side_effect=error):
            self.assertTrue(
                scraper.allowed_by_robots("https://example.gov.np/notices")
            )


class RequestTests(unittest.TestCase):
    def setUp(self):
        scraper._LAST_REQUEST_AT.clear()

    def test_client_errors_are_not_retried_or_allowed_to_disable_tls(self):
        response = Mock(status_code=404)
        response.raise_for_status.side_effect = requests.HTTPError("404")
        with patch.object(scraper.requests, "get", return_value=response) as get:
            with self.assertRaises(requests.HTTPError):
                scraper._request("https://example.gov.np/missing")
        get.assert_called_once()
        self.assertNotIn("verify", get.call_args.kwargs)

    def test_server_errors_are_retried(self):
        failure = Mock(status_code=503)
        success = Mock(status_code=200)
        with patch.object(
            scraper.requests, "get", side_effect=[failure, success]
        ) as get, patch.object(scraper.time, "sleep"):
            result = scraper._request("https://example.gov.np/notices")
        self.assertIs(result, success)
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
