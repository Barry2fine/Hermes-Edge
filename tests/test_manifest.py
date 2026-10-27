import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sovereign_bots.core.registry import DOMAINS, coverage, domain_bots, load_manifest  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_all_required_bots_present(self):
        cov = coverage(load_manifest(str(ROOT / "manifest.json")))
        for domain in DOMAINS:
            self.assertIn(domain, cov)
            self.assertEqual(cov[domain]["missing"], [], f"{domain} missing {cov[domain]['missing']}")

    def test_no_duplicate_ids_within_domain(self):
        for domain in DOMAINS:
            ids = [b.id for b in domain_bots(domain)]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate id in {domain}: {ids}")

    def test_every_bot_has_mandate_and_domain(self):
        for domain in DOMAINS:
            for b in domain_bots(domain):
                self.assertTrue(b.mandate and len(b.mandate) > 8, f"{b.id} missing mandate")
                self.assertIn(domain, b.domains)


if __name__ == "__main__":
    unittest.main(verbosity=2)
