from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comic_manage.komga_client import KomgaError
from comic_manage.metadata import Metadata
from comic_manage.service import ComicService, ServiceError
from comic_manage.sources import inspect_source


class UploadReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / 'db'
        self.output = self.root / 'out'
        self.connection = {'url': 'http://example.test', 'apiKey': 'fixture', 'libraryId': 'books'}
        self.service = ComicService(self.data)
        self.first = self._book('春日旅途')
        self.second = self._book('夏日旅途')

    def _book(self, title):
        path = self.root / title
        path.mkdir()
        (path / '1.jpg').write_bytes(b'fixture page')
        identity = self.service.repo.upsert_scan(inspect_source(path))
        metadata = Metadata(title=title, authors=['测试作者'], genre='漫画', age_rating='Everyone', tags=['日常'])
        self.service.call('approve', {'id': identity, 'metadata': metadata.to_dict()})
        return identity

    def _remote(self, plans, *args, first_state='new', **kwargs):
        return {'libraryId': 'books', 'libraryName': '测试书库', 'items': [
            {'id': plan.comic.id, 'status': first_state if plan.comic.id == self.first else 'new',
             'matches': [], 'matchCount': 0} for plan in plans
        ]}

    def _plan(self):
        return self.service.call('plan', {'ids': [self.first, self.second], 'outputRoot': str(self.output),
                                          'comparison': self.connection})

    def _export(self, plan, **params):
        return self.service.call('export', {'token': plan['token'], 'comparison': self.connection, **params})

    def test_comparison_and_subset_confirmation(self):
        def compare(*args, **kwargs):
            return self._remote(*args, first_state='existing', **kwargs)
        with patch('comic_manage.service.compare_uploads', side_effect=compare):
            plan = self._plan()
            self.assertEqual(plan['komga']['libraryName'], '测试书库')
            self.assertEqual([item['komga']['status'] for item in plan['items']], ['existing', 'new'])
            with self.assertRaisesRegex(ServiceError, '可能重复'):
                self._export(plan, ids=[self.first], confirmedIds=[])
            self.assertFalse(self.output.exists())
            plan = self._plan()
            result = self._export(plan, ids=[self.second], confirmedIds=[])
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(result['completed']), 1)
            self.assertEqual(self.service.repo.get(self.first).status, 'approved')

    def test_existing_path_continues_as_distinct_package_only_after_confirmation(self):
        with patch('comic_manage.service.compare_uploads', side_effect=lambda *args, **kw: self._remote(*args, first_state='possible_version', **kw)):
            existing = self._plan()
            original = Path(existing['items'][0]['destination'])
            original.parent.mkdir(parents=True)
            original.write_bytes(b'existing cbz')
            plan = self._plan()
            self.assertNotEqual(plan['items'][0]['destination'], str(original))
            result = self._export(plan, ids=[self.first], confirmedIds=[self.first])
            self.assertEqual(result['completed'], [plan['items'][0]['destination']])
            self.assertEqual(original.read_bytes(), b'existing cbz')
            self.assertTrue(Path(result['completed'][0]).is_file())

    def test_offline_or_changed_library_prevents_commit(self):
        with patch('comic_manage.service.compare_uploads', side_effect=KomgaError('模拟服务离线')):
            failed = self._plan()
            self.assertEqual(failed['komga']['state'], 'error')
            with self.assertRaises(ServiceError):
                self._export(failed)
        with patch('comic_manage.service.compare_uploads', side_effect=self._remote):
            originally_new = self._plan()
        with patch('comic_manage.service.compare_uploads', side_effect=lambda *a, **kw: self._remote(*a, first_state='existing', **kw)):
            with self.assertRaisesRegex(ServiceError, '有变化'):
                self._export(originally_new, ids=[self.first])
        self.assertFalse(self.output.exists())

    def test_scan_uses_active_records_without_saving_old_roots(self):
        self.service.repo.set_setting('roots', str(self.root / 'older-share'))
        self.service.repo.set_setting('exact_import_roots', str(self.root / 'older-folder'))
        self.service.call('remove', {'ids': [self.second]})
        reopened = ComicService(self.data)
        self.assertEqual(reopened.repo.setting('roots'), '')
        self.assertEqual(reopened.repo.setting('exact_import_roots'), '')
        self.assertEqual(reopened.call('scan')['count'], 1)
        self.assertFalse(reopened.call('scan')['errors'])

    def test_current_remote_book_retires_only_corresponding_completed_local_task(self):
        service = self.service
        repo = service.repo
        pending_path = Path(repo.get(self.second).source)
        source_path = Path(repo.get(self.first).source)
        plan = service.call('plan', {'ids': [self.first], 'outputRoot': str(self.output)})
        exported = service.call('export', {'token': plan['token']})
        output = Path(exported['completed'][0])
        book = {'scope': 'test-scope', 'server_url': self.connection['url'], 'library_id': 'books',
                'library_name': '测试书库', 'book_id': 'same-book', 'series_id': 'same-series',
                'remote_path': '/library/' + output.relative_to(self.output).as_posix(), 'source': 'komga://test-scope/same-book'}
        repo.upsert_komga('remote-id', book, Metadata(title='春日旅途'), {}, 'v1', 1, '')
        repo.save_komga_mapping(book['server_url'], book['library_id'], self.first,
                                book['book_id'], book['series_id'], book['remote_path'])
        self.assertEqual(repo.retire_indexed_uploads('other-scope'), [])
        with patch('comic_manage.service.pull_library', return_value={'cancelled': False, 'errors': [], 'scope': 'test-scope'}):
            self.assertEqual(service.call('komga_pull', self.connection)['retired'], 1)
        self.assertEqual({book.id for book in repo.all()}, {self.second, 'remote-id'})
        self.assertTrue(source_path.is_dir() and pending_path.is_dir() and output.is_file())
        with repo.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM events WHERE comic_id=?', (self.first,)).fetchone())
