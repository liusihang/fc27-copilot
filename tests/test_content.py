import unittest

from fc27.content import ContentService, FutggContentClient
from fc27.errors import FC27Error


class ContentServiceTest(unittest.TestCase):
    def test_futgg_evolutions_use_content_hashed_manifest(self):
        base = "https://example.test/27"
        payloads = {
            f"{base}/manifest.json": {
                "_version": 1,
                "active-evolutions": "active-hash",
            },
            f"{base}/active-evolutions.v1.active-hash.json": [
                {
                    "id": 10,
                    "eaId": 20,
                    "name": "Starter Evolution",
                    "url": "/evolutions/10-starter/",
                    "coinsCost": 0,
                    "pointsCost": 0,
                    "isExpired": False,
                    "requirementsText": [{"label": "Overall", "value": "Max. 80"}],
                    "levels": [{"idx": 1, "challenges": ["Play 1"], "totalUpgradesText": []}],
                }
            ],
        }
        raw = FutggContentClient(base, payloads.__getitem__).evolutions("active")
        self.assertEqual(raw["manifest_hash"], "active-hash")
        self.assertEqual(raw["evolutions"][0]["name"], "Starter Evolution")
        self.assertEqual(raw["evolutions"][0]["url"], "https://www.fut.gg/evolutions/10-starter/")

    def test_normalizes_objective_groups_and_bounds_results(self):
        options = ContentService.validate_arguments(
            {
                "content_type": "objective",
                "source": "ea",
                "detail": "detailed",
                "limit": 1,
            }
        )
        result = ContentService.normalize_ea(
            "objective",
            {
                "status": 200,
                "categories": [
                    {
                        "id": 1,
                        "name": "Seasonal",
                        "groups": [
                            {
                                "id": 2,
                                "title": "First Team Dreams",
                                "completed": False,
                                "tasks": [{"id": 3, "title": "Complete one SBC"}],
                            },
                            {"id": 4, "title": "Hidden by limit", "completed": False, "tasks": []},
                        ],
                    }
                ],
            },
            options,
        )
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["items"][0]["category_name"], "Seasonal")
        self.assertEqual(result["items"][0]["tasks"][0]["id"], 3)

    def test_rejects_unknown_content_type(self):
        with self.assertRaises(FC27Error) as context:
            ContentService.validate_arguments({"content_type": "packs"})
        self.assertEqual(context.exception.code, "INVALID_CONTENT_TYPE")


if __name__ == "__main__":
    unittest.main()
