"""Narrow restore and backed-up startup repair of legacy version-label drafts."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from comic_manage.metadata import Metadata
from comic_manage.repository import Repository
from comic_manage.sources import Cancelled, inspect_source, source_id


class VariantRepairV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / '[pink-noise (みずいろめがね)] 合集' / '[2011.08] Cattleya & Lana [穿刺個人漢化]'
        self.repo = Repository(self.root / 'data' / 'catalog.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def legacy(self, leaf='无文字', **values):
        path = self.work / leaf
        path.mkdir(parents=True, exist_ok=True)
        (path / '1.png').write_bytes(b'original-page')
        snapshot = inspect_source(path)
        old = Metadata(title=values.pop('title', leaf), original_title=values.pop('original_title', leaf),
                       authors=values.pop('authors', ['pink-noise (みずいろめがね)']), **values)
        snapshot.metadata_context = {}
        snapshot.initial_metadata = lambda: (Metadata.from_dict(old.to_dict()), '保留的旧版本提示')
        identity = self.repo.upsert_scan(snapshot)
        return path, identity

    def states(self, snapshots):
        return {source_id(snapshot.path): self.repo.review_state(source_id(snapshot.path)) for snapshot in snapshots}

    def backup(self):
        target = self.root / 'before-repair.sqlite3'
        self.repo.backup(target)
        return target

    def values(self, identity):
        return self.repo.get(identity, include_removed=True).metadata.to_dict()

    def test_removed_generic_preview_can_opt_in_to_exact_restore_repair_without_importing_peer(self):
        path, identity = self.legacy()
        peer, peer_id = self.legacy('有文字')
        self.repo.remove([identity, peer_id])
        snapshot = inspect_source(path)
        previous = self.repo.get(identity, include_removed=True)
        default, _, changes = self.repo.preview_variant_repair(snapshot, previous)
        self.assertEqual(default.title, '无文字')
        self.assertEqual(changes, [])
        preview, _, changes = self.repo.preview_variant_repair(snapshot, previous, allow_removed=True)
        self.assertEqual(preview.title, 'Cattleya & Lana')
        self.assertIn('title', changes)
        self.assertTrue(self.repo.is_removed(identity), 'Preview does not restore or modify data')
        self.repo.upsert_scan(snapshot, restore=True)
        saved = self.repo.get(identity)
        self.assertEqual(saved.metadata.to_dict(), preview.to_dict())
        self.assertEqual(saved.metadata.authors, ['pink-noise (みずいろめがね)'])
        self.assertEqual(saved.metadata.text_version, '无文字')
        self.assertTrue(saved.metadata.work_group)
        self.assertTrue(self.repo.is_removed(peer_id), 'Unchecked peer must remain removed')
        self.assertEqual((path / '1.png').read_bytes(), b'original-page')

    def test_restore_still_preserves_own_xml_and_historical_review_protection(self):
        path, identity = self.legacy(genre='漫画', age_rating='Everyone', tags=['日常'])
        self.repo.approve(identity, inspect_source(path))
        current = self.repo.get(identity).metadata
        current.summary = '旧人工修改'
        self.repo.save(identity, current)
        self.repo.remove([identity])
        before = self.values(identity)
        self.repo.upsert_scan(inspect_source(path), restore=True)
        self.assertEqual(self.values(identity), before)
        self.assertEqual(self.repo.get(identity).status, 'draft')
        xml_path, xml_id = self.legacy('【有文字】')
        (xml_path / 'ComicInfo.xml').write_text('<ComicInfo><Title>【有文字】</Title></ComicInfo>', encoding='utf8')
        self.repo.remove([xml_id])
        before = self.values(xml_id)
        self.repo.upsert_scan(inspect_source(xml_path), restore=True)
        self.assertEqual(self.values(xml_id), before)

    def test_candidate_listing_is_database_only_and_omits_noneligible_records(self):
        _, eligible = self.legacy()
        meaningful, meaningful_id = self.legacy('有文字', title='用户确认标题')
        self.repo.save(meaningful_id, self.repo.get(meaningful_id).metadata, fields={'title'})
        _, removed_id = self.legacy('[有文字]')
        self.repo.remove([removed_id])
        _, chinese_id = self.legacy('【无文字】', chinese_title='人工中文名')
        _, series_id = self.legacy('［有文字］', collection_mode='series', series='真实系列')
        _, packaged_id = self.legacy('无字版')
        self.repo.mark_packaged(packaged_id, self.root / 'old.cbz', {})
        metadata = self.repo.get(packaged_id).metadata
        self.repo.save(packaged_id, metadata)
        with patch.object(Path, 'exists', side_effect=AssertionError('No source reads during candidate listing')):
            identities = [comic.id for comic in self.repo.variant_repair_sources()]
        self.assertEqual(identities, [eligible])
        self.assertNotIn(meaningful_id, identities)
        self.assertNotIn(chinese_id, identities)
        self.assertNotIn(series_id, identities)

    def test_startup_backfill_repairs_restored_visible_pair_with_backup_and_preserves_raw_unselected_data(self):
        books = [self.legacy('有文字', tags=['人工标签'], summary='人工说明'), self.legacy('无文字', tags=['人工标签'], summary='人工说明')]
        before = {}
        with self.repo.connect() as db:
            for _, identity in books:
                self.repo._event(db, identity, 'edit', '人工修改元数据')
                self.repo._event(db, identity, 'remove', '旧版本移出')
                self.repo._event(db, identity, 'restore', '旧版本恢复')
                row = db.execute('SELECT metadata FROM comics WHERE id=?', (identity,)).fetchone()
                raw = json.loads(row['metadata'])
                raw['unrecognizedLegacyField'] = {'preserve': [1, 2]}
                db.execute('UPDATE comics SET metadata=? WHERE id=?', (json.dumps(raw, ensure_ascii=False), identity))
                before[identity] = raw
        snapshots = [inspect_source(path) for path, _ in books]
        states = self.states(snapshots)
        backup = self.backup()
        repaired = self.repo.apply_variant_backfill(snapshots, expected=states, backup_path=backup)
        self.assertEqual(set(repaired), {identity for _, identity in books})
        groups = set()
        with self.repo.connect() as db:
            for path, identity in books:
                after = self.repo.review_state(identity)
                for field in ('id', 'source', 'kind', 'fingerprint', 'page_count', 'status', 'output'):
                    self.assertEqual(after[field], states[identity][field], field)
                self.assertEqual(after['metadata']['title'], 'Cattleya & Lana')
                self.assertEqual(after['metadata']['authors'], ['pink-noise (みずいろめがね)'])
                self.assertEqual(after['metadata']['tags'], ['人工标签'])
                self.assertEqual(after['metadata']['summary'], '人工说明')
                groups.add(after['metadata']['work_group'])
                raw = json.loads(db.execute('SELECT metadata FROM comics WHERE id=?', (identity,)).fetchone()['metadata'])
                self.assertEqual(raw['unrecognizedLegacyField'], before[identity]['unrecognizedLegacyField'])
                detail = json.loads(db.execute("SELECT detail FROM events WHERE comic_id=? AND action='version_folder_repair'", (identity,)).fetchone()['detail'])
                self.assertEqual(detail['backup'], str(backup.resolve()))
                self.assertEqual(detail['previous_metadata'], before[identity])
                self.assertEqual((path / '1.png').read_bytes(), b'original-page')
        self.assertEqual(len(groups), 1)
        with closing(sqlite3.connect(backup)) as db:
            for _, identity in books:
                self.assertEqual(json.loads(db.execute('SELECT metadata FROM comics WHERE id=?', (identity,)).fetchone()[0]), before[identity])
        self.assertEqual(self.repo.variant_repair_sources(), [])

    def test_startup_backfill_does_not_restore_hidden_record(self):
        path, identity = self.legacy()
        snapshot = inspect_source(path)
        expected = self.states([snapshot])
        backup = self.backup()
        self.repo.remove([identity])
        before = self.values(identity)
        with self.assertRaises(ValueError):
            self.repo.apply_variant_backfill([snapshot], expected=expected, backup_path=backup)
        self.assertTrue(self.repo.is_removed(identity))
        self.assertEqual(self.values(identity), before)

    def test_changed_revision_is_checked_for_whole_batch_before_any_write(self):
        books = [self.legacy(), self.legacy('有文字')]
        snapshots = [inspect_source(path) for path, _ in books]
        states = self.states(snapshots)
        backup = self.backup()
        second = self.repo.get(books[1][1]).metadata
        second.summary = '识别期间修改'
        self.repo.save(books[1][1], second)
        before = {identity: self.values(identity) for _, identity in books}
        with self.assertRaisesRegex(ValueError, '识别期间发生变化'):
            self.repo.apply_variant_backfill(snapshots, expected=states, backup_path=backup)
        self.assertEqual({identity: self.values(identity) for _, identity in books}, before)

    def test_changed_source_fingerprint_or_page_count_never_causes_implicit_scan(self):
        path, identity = self.legacy()
        old_state = self.repo.review_state(identity)
        backup = self.backup()
        (path / '2.png').write_bytes(b'new-page')
        snapshot = inspect_source(path)
        with self.assertRaisesRegex(ValueError, '来源或审核状态'):
            self.repo.apply_variant_backfill([snapshot], expected={identity: old_state}, backup_path=backup)
        self.assertEqual(self.repo.review_state(identity), old_state)

    def test_missing_backup_and_missing_expected_revision_refuse_to_write(self):
        path, identity = self.legacy()
        snapshots = [inspect_source(path)]
        before = self.values(identity)
        with self.assertRaisesRegex(ValueError, '备份不存在'):
            self.repo.apply_variant_backfill(snapshots, expected=self.states(snapshots), backup_path=self.root / 'absent.sqlite3')
        with self.assertRaisesRegex(ValueError, '识别期间发生变化'):
            self.repo.apply_variant_backfill(snapshots, expected={}, backup_path=self.backup())
        self.assertEqual(self.values(identity), before)

    def test_apply_never_resolves_the_source_again_on_the_database_thread(self):
        path, identity = self.legacy()
        snapshot = inspect_source(path)
        expected = self.states([snapshot])
        backup = self.backup()
        resolve = Path.resolve
        source_prefix = str(self.work)
        def local_only(item, *args, **kwargs):
            if str(item).startswith(source_prefix):
                raise AssertionError('Source path resolution belongs to the bounded read-only inspector')
            return resolve(item, *args, **kwargs)
        with patch.object(Path, 'resolve', local_only):
            self.assertEqual(self.repo.apply_variant_backfill([snapshot], expected=expected, backup_path=backup), [identity])

    def test_duplicate_candidates_and_cancellation_roll_back_every_write(self):
        books = [self.legacy(), self.legacy('有文字')]
        snapshots = [inspect_source(path) for path, _ in books]
        states = self.states(snapshots)
        backup = self.backup()
        with self.assertRaisesRegex(ValueError, '候选重复'):
            self.repo.apply_variant_backfill([snapshots[0], snapshots[0]], expected=states, backup_path=backup)
        cancel = Event()
        original = self.repo._event
        def interrupt(db, comic_id, action, detail):
            original(db, comic_id, action, detail)
            if action == 'version_folder_repair':
                cancel.set()
        with patch.object(self.repo, '_event', side_effect=interrupt), self.assertRaises(Cancelled):
            self.repo.apply_variant_backfill(snapshots, expected=states, backup_path=backup, cancel=cancel)
        for _, identity in books:
            self.assertEqual(self.repo.review_state(identity), states[identity])


if __name__ == '__main__':
    unittest.main()
