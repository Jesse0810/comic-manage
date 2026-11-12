"""Komga manifest export uses the metadata of verified, current CBZ outputs."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from comic_manage.service import ComicService


class KomgaManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = ComicService(self.root / "data")
        self.destination = self.root / "komga_notable_entities_manifest.json"

    def generated(self, name, **fields):
        source = self.root / name
        source.mkdir()
        (source / "1.jpg").write_bytes(b"synthetic page")
        preview = self.service.call("prepare_import", {"paths": [str(source)]})
        identity = preview["items"][0]["id"]
        self.service.call("commit_import", {"token": preview["token"], "ids": [identity]})
        metadata = {"title": name, "authors": ["作者"], "age_rating": "Everyone", **fields}
        self.service.call("approve", {"id": identity, "metadata": metadata})
        plan = self.service.call("plan", {"ids": [identity], "outputRoot": str(self.root / "output")})
        result = self.service.call("export", {"token": plan["token"]})
        self.assertEqual(result["errors"], [])
        return identity

    def export(self, identities):
        return self.service.call("export_komga", {"ids": identities, "path": str(self.destination)})

    def test_manifest_contains_portable_paths_and_api_only_supplements(self):
        one = self.generated("独立作品", characters=["角色甲"], original_works=["东方Project"])
        first = self.generated("第一卷", collection_mode="series", series="连续系列", number="1",
                               localized_series="系列译名", localized_series_label="zh-Hans")
        second = self.generated("第二卷", collection_mode="series", series="连续系列", number="2",
                                localized_series="系列译名", localized_series_label="zh-Hans")
        result = self.export([one, first, second])
        document = json.loads(self.destination.read_text(encoding="utf-8"))
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["seriesCount"], 1)
        self.assertTrue(document["books"][0]["relativePath"].startswith("_oneshots/"))
        self.assertEqual(document["books"][1]["relativePath"].split("/")[0],
                         document["books"][2]["relativePath"].split("/")[0])
        self.assertIn({"kind": "原作", "name": "东方Project"}, document["books"][0]["notableEntities"])
        self.assertEqual(document["series"][0]["alternateTitles"], [{"label": "zh-Hans", "title": "系列译名"}])

    def test_changed_draft_cannot_replace_existing_manifest(self):
        identity = self.generated("作品")
        self.destination.write_text("previous", encoding="utf-8")
        self.service.call("save", {"id": identity, "metadata": {"summary": "已修改"}})
        with self.assertRaisesRegex(ValueError, "生成"):
            self.export([identity])
        self.assertEqual(self.destination.read_text(encoding="utf-8"), "previous")

    def test_conflicting_series_aliases_do_not_publish_partial_manifest(self):
        first = self.generated("第一卷", collection_mode="series", series="系列", number="1", localized_series="译名甲")
        second = self.generated("第二卷", collection_mode="series", series="系列", number="2", localized_series="译名乙")
        with self.assertRaisesRegex(ValueError, "别名"):
            self.export([first, second])
        self.assertFalse(self.destination.exists())

    def test_tampered_export_cannot_supply_stale_api_metadata(self):
        identity = self.generated("作品")
        output = Path(self.service.repo.get(identity).output)
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("ComicInfo.xml", "<ComicInfo/>")
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.export([identity])
        self.assertFalse(self.destination.exists())
