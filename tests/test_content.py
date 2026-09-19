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
        options = ContentService.validate_arguments(
            {"content_type": "evolution", "source": "futgg"}
        )
        normalized = ContentService.normalize_futgg_evolutions(raw, options)
        self.assertEqual(normalized["items"][0]["id"], "ea:20")
        self.assertEqual(normalized["items"][0]["costs"]["coins"], 0)
        self.assertEqual(
            normalized["items"][0]["source_evidence"]["match_method"],
            "unmatched_public",
        )

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
                "objective_sources": [
                    {"id": "ut", "name": "Ultimate Team Objectives"},
                    {"id": "fc", "name": "FC Objectives"},
                ],
                "campaign": {"id": 27, "title": "Season 1"},
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
        self.assertEqual(result["source_meta"]["objective_sources"][1]["id"], "fc")
        self.assertEqual(result["source_meta"]["campaign"]["id"], 27)

    def test_filters_objectives_by_fc_hub_section(self):
        options = ContentService.validate_arguments(
            {
                "content_type": "objective",
                "source": "ea",
                "section": "fc_pro",
                "state": "all",
            }
        )
        result = ContentService.normalize_ea(
            "objective",
            {
                "categories": [
                    {"id": 1, "name": "FC Pro", "groups": [{"id": 10, "title": "Pro", "completed": False}]},
                    {"id": 2, "name": "Mastery", "groups": [{"id": 20, "title": "Master", "completed": False}]},
                ],
                "sections": [
                    {"id": 1, "name": "FC Pro", "group_count": 1},
                    {"id": 2, "name": "Mastery", "group_count": 1},
                ],
            },
            options,
        )
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["items"][0]["title"], "Pro")
        self.assertEqual(result["items"][0]["section"], "fc_pro")
        self.assertEqual(len(result["source_meta"]["sections"]), 2)

    def test_normalizes_compact_season_levels(self):
        options = ContentService.validate_arguments(
            {"content_type": "season", "source": "ea", "state": "all", "detail": "detailed"}
        )
        result = ContentService.normalize_ea(
            "season",
            {
                "campaign": {"id": 88, "title": "Season 1", "current_xp": 1550},
                "season_levels": [
                    {
                        "level": 1,
                        "required_xp": 1000,
                        "remaining_xp": 0,
                        "standard": {"claimed": True, "claimable": False, "unlocked": True, "rewards": []},
                        "premium": {"claimed": True, "claimable": False, "unlocked": True, "rewards": []},
                    },
                    {
                        "level": 2,
                        "required_xp": 2000,
                        "remaining_xp": 450,
                        "standard": {"claimed": False, "claimable": False, "unlocked": False, "rewards": []},
                        "premium": {"claimed": False, "claimable": False, "unlocked": False, "rewards": []},
                    },
                ],
            },
            options,
        )
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["items"][1]["remaining_xp"], 450)
        self.assertEqual(result["source_meta"]["campaign"]["current_xp"], 1550)

    def test_merges_ea_and_futgg_evolutions(self):
        options = ContentService.validate_arguments(
            {
                "content_type": "evolution",
                "source": "auto",
                "state": "current",
                "detail": "detailed",
            }
        )
        ea_raw = {
            "status": 200,
            "categories": [{"id": 0, "name": "Evolutions"}],
            "lifecycle": {"active": [2710]},
            "evolutions": [
                {
                    "id": 2710,
                    "ea_id": 2710,
                    "name": "Fullback Fork",
                    "display_group": "my_evolutions",
                    "availability": "account_started",
                    "active": True,
                    "started": True,
                    "completed": False,
                    "expired": False,
                    "levels": [{"index": 1, "state": "IN_PROGRESS", "objectives": [{"id": 1}]}],
                },
                {
                    "id": 2696,
                    "ea_id": 2696,
                    "name": "Pinged Pass",
                    "display_group": "rewards",
                    "availability": "account_available",
                    "active": False,
                    "started": False,
                    "completed": False,
                    "expired": False,
                    "levels": [{"index": 1, "state": "NOT_STARTED"}],
                },
            ],
        }
        futgg_raw = {
            "manifest_version": 1,
            "manifest_key": "active-evolutions",
            "manifest_hash": "hash",
            "evolutions": [
                {
                    "id": "futgg:2502",
                    "futgg_id": 2502,
                    "ea_id": 2710,
                    "name": "Fullback Fork",
                    "expired": False,
                    "levels": [{"index": 1, "upgrades": ["Overall +5"]}],
                },
                {
                    "id": "futgg:2501",
                    "futgg_id": 2501,
                    "ea_id": 999999,
                    "name": "Pinged Pass [SP+ 1]",
                    "expired": False,
                    "levels": [{"index": 1, "upgrades": ["Pinged Pass"]}],
                },
                {
                    "id": "futgg:2498",
                    "futgg_id": 2498,
                    "ea_id": 999999,
                    "name": "Creative or Composed? [SP 27]",
                    "expired": False,
                    "display_group": "public",
                    "availability": "public",
                    "levels": [],
                },
            ],
        }
        result = ContentService.merge_evolutions(ea_raw, futgg_raw, options)
        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["source_meta"]["matches"]["ea_id"], 1)
        self.assertEqual(result["source_meta"]["matches"]["name"], 1)
        self.assertEqual(result["source_meta"]["matches"]["unmatched_public"], 1)
        fullback = next(item for item in result["items"] if item["ea_id"] == 2710)
        self.assertEqual(fullback["futgg_id"], 2502)
        self.assertEqual(fullback["levels"][0]["upgrades"], ["Overall +5"])
        self.assertEqual(fullback["levels"][0]["objectives"], [{"id": 1}])

        public_options = ContentService.validate_arguments(
            {
                "content_type": "evolution",
                "source": "auto",
                "section": "public",
                "state": "current",
            }
        )
        public = ContentService.merge_evolutions(ea_raw, futgg_raw, public_options)
        self.assertEqual(public["total_count"], 1)
        self.assertEqual(public["items"][0]["futgg_id"], 2498)

    def test_rejects_unknown_content_type(self):
        with self.assertRaises(FC27Error) as context:
            ContentService.validate_arguments({"content_type": "packs"})
        self.assertEqual(context.exception.code, "INVALID_CONTENT_TYPE")

    def test_rejects_section_and_state_for_wrong_content_type(self):
        with self.assertRaises(FC27Error) as context:
            ContentService.validate_arguments(
                {"content_type": "season", "section": "fc_pro"}
            )
        self.assertEqual(context.exception.code, "INVALID_CONTENT_SECTION")
        with self.assertRaises(FC27Error) as context:
            ContentService.validate_arguments(
                {"content_type": "objective", "state": "paused"}
            )
        self.assertEqual(context.exception.code, "INVALID_CONTENT_STATE")


if __name__ == "__main__":
    unittest.main()
