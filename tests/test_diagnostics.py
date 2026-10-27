"""Offline error-classification/read-only diagnostics tests; no Firebase SDK or RPC."""
import ast
import asyncio
import copy
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'cogs' / 'automod.py'
TREE = ast.parse(SOURCE.read_text())

class GoogleAPICallError(Exception):
    pass
class GoogleAuthError(Exception):
    pass
class FailedPrecondition(GoogleAPICallError):
    pass
class PermissionDenied(GoogleAPICallError):
    pass
class ServiceUnavailable(GoogleAPICallError):
    pass
class RefreshError(GoogleAuthError):
    pass

class FakeClient:
    def __init__(self, failures=None, read_error=None):
        self.failures = failures or {}
        self.read_error = read_error
        self.calls = []
    def collection(self, path):
        return FakeReference(self, path)

class FakeReference:
    def __init__(self, client, path, filters=(), ordering=None, maximum=None):
        self.client = client
        self.path = path
        self.filters = filters
        self.ordering = ordering
        self.maximum = maximum
    def collection(self, name):
        return FakeReference(self.client, self.path + '/' + name)
    def document(self, name):
        return FakeReference(self.client, self.path + '/' + name)
    def where(self, *, filter):
        return FakeReference(self.client, self.path, self.filters + (filter,), self.ordering, self.maximum)
    def order_by(self, name, direction=None):
        return FakeReference(self.client, self.path, self.filters, (name, direction), self.maximum)
    def limit(self, maximum):
        return FakeReference(self.client, self.path, self.filters, self.ordering, maximum)
    async def get(self, **kwargs):
        self.client.calls.append(('GET', self.path))
        if self.client.read_error:
            raise self.client.read_error
        return types.SimpleNamespace(exists=False)
    async def stream(self, **kwargs):
        self.client.calls.append(('QUERY', self.path, self.filters, self.ordering, self.maximum))
        fields = {item[0] for item in self.filters}
        if 'log_status' in fields:
            label = 'pending_logs'
        elif 'retention_state' in fields:
            label = 'retention'
        elif 'generation' in fields:
            label = 'violations_current'
        else:
            label = 'violations_all'
        if label in self.client.failures:
            raise self.client.failures[label]
        yield {'should_not_be_in_diagnostic': 'PRIVATE_MEMBER_DATA'}


def namespace():
    wanted = {'_firestore_index_link', 'firestore_diagnostic', 'utc_now', '_id', 'Store', 'StorageUnavailable'}
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes += [copy.deepcopy(node) for node in TREE.body if getattr(node, 'name', None) in wanted]
    ns = {'re': re, 'UTC': timezone.utc, 'datetime': datetime, 'logging': logging,
          'ROOT_COLLECTION': 'discord_moderation_guilds', 'AsyncClient': FakeClient,
          'FieldFilter': lambda name, op, value: (name, op, value),
          'GoogleAuthError': GoogleAuthError,
          'google_errors': types.SimpleNamespace(GoogleAPICallError=GoogleAPICallError),
          'asyncio': asyncio, '__name__': 'offline_diagnostics_test'}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), ns)
    return ns


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = namespace()
        self.logger = logging.getLogger('offline_diagnostics_test')
        self.logger.disabled = True
        self.addCleanup(setattr, self.logger, 'disabled', False)

    def test_missing_index_classification_and_safe_url(self):
        url = 'https://console.firebase.google.com/v1/r/project/demo-safe-project/firestore/indexes?create_composite=YWJjZA%3D%3D'
        result = self.ns['firestore_diagnostic'](FailedPrecondition('The query requires an index. ' + url), 'query')
        self.assertEqual(result['category'], 'missing_index')
        self.assertEqual(result['index_url'], url)

    def test_precondition_without_index_not_mislabelled(self):
        result = self.ns['firestore_diagnostic'](FailedPrecondition('database is in wrong mode'), 'query')
        self.assertEqual(result['category'], 'failed_precondition')
        self.assertIsNone(result['index_url'])

    def test_network_error_does_not_become_missing_index(self):
        result = self.ns['firestore_diagnostic'](ServiceUnavailable('network offline'), 'probe')
        self.assertEqual(result['category'], 'network_or_timeout')

    def test_permission_error_does_not_print_raw_secrets(self):
        result = self.ns['firestore_diagnostic'](PermissionDenied('SECRET_TOKEN_AND_PRIVATE_KEY'), 'probe')
        self.assertEqual(result['category'], 'permission_denied')
        self.assertNotIn('SECRET_TOKEN_AND_PRIVATE_KEY', str(result))

    def test_refresh_error_is_authentication(self):
        result = self.ns['firestore_diagnostic'](RefreshError('PRIVATE_KEY'), 'probe')
        self.assertEqual(result['category'], 'authentication')
        self.assertNotIn('PRIVATE_KEY', str(result))

    def test_wrapper_and_grpc_cause_preserve_meaningful_status(self):
        sdk = FailedPrecondition('The query requires an index')
        sdk.__cause__ = RuntimeError('raw grpc details')
        wrapper = RuntimeError('Firestore failed')
        wrapper.__cause__ = sdk
        result = self.ns['firestore_diagnostic'](wrapper, 'query')
        self.assertEqual(result['exception'], 'FailedPrecondition')
        self.assertEqual(result['category'], 'missing_index')

    def test_diagnostic_url_rejects_foreign_host_and_extra_secrets(self):
        parser = self.ns['_firestore_index_link']
        self.assertIsNone(parser('https://evil.example/indexes?token=secret'))
        self.assertIsNone(parser('https://console.firebase.google.com/project/demo-project/firestore/indexes?create_composite=YWJj&token=secret'))
        self.assertIsNone(parser('https://console.firebase.google.com.evil.example/project/demo-project/firestore/indexes?create_composite=YWJj'))

    async def test_all_query_checks_are_reads_and_do_not_return_member_data(self):
        client = FakeClient()
        store = self.ns['Store'](client)
        result = await store.diagnose(101, 202)
        self.assertTrue(result['ready'])
        self.assertTrue(store.healthy)
        self.assertEqual(len(client.calls), 5)
        self.assertTrue(all(call[0] in {'GET', 'QUERY'} for call in client.calls))
        self.assertTrue(all(call[4] == 1 for call in client.calls if call[0] == 'QUERY'))
        self.assertNotIn('PRIVATE_MEMBER_DATA', str(result))
        self.assertNotIn('should_not_be_in_diagnostic', str(result))

    async def test_probe_success_does_not_hide_composite_query_failure(self):
        client = FakeClient(failures={'pending_logs': FailedPrecondition('query requires an index')})
        store = self.ns['Store'](client)
        result = await store.diagnose(101, 202)
        self.assertTrue(result['document_read'])
        self.assertFalse(result['ready'])
        self.assertFalse(store.healthy)
        self.assertEqual(result['queries'][0]['error']['category'], 'missing_index')
        self.assertEqual(len(result['queries']), 4)

    async def test_all_missing_indexes_reported_not_just_first(self):
        labels = ('pending_logs', 'retention', 'violations_all', 'violations_current')
        client = FakeClient(failures={label: FailedPrecondition('query requires an index') for label in labels})
        store = self.ns['Store'](client)
        result = await store.diagnose(101, 202)
        self.assertEqual(sum(not row['ok'] for row in result['queries']), 4)

    async def test_auth_failure_stops_queries_and_keeps_sanctions_disabled(self):
        client = FakeClient(read_error=RefreshError('bad credentials'))
        store = self.ns['Store'](client)
        result = await store.diagnose(101, 202)
        self.assertFalse(result['document_read'])
        self.assertEqual(result['queries'], [])
        self.assertFalse(store.healthy)
        self.assertEqual(result['error']['category'], 'authentication')

    async def test_after_repair_reads_clear_only_diagnostic_state(self):
        client = FakeClient(failures={'retention': FailedPrecondition('requires index')})
        store = self.ns['Store'](client)
        self.assertFalse((await store.diagnose(101, 202))['ready'])
        client.failures.clear()
        self.assertTrue((await store.diagnose(101, 202))['ready'])
        self.assertIsNone(store.last_error)
        self.assertTrue(all(call[0] in {'GET', 'QUERY'} for call in client.calls))

    async def test_query_failure_records_operation_before_wrapping(self):
        client = FakeClient(failures={'pending_logs': FailedPrecondition('requires index')})
        store = self.ns['Store'](client)
        await store.probe()
        query = client.collection('incidents').where(filter=('log_status', '==', 'pending'))
        with self.assertRaises(self.ns['StorageUnavailable']):
            await store._query(query)
        self.assertEqual(store.last_error['operation'], 'query')
        self.assertEqual(store.last_error['category'], 'missing_index')
        self.assertFalse(store.healthy)

    def test_model_missing_detection_precedes_engine_import(self):
        worker = next(n for n in TREE.body if getattr(n, 'name', '') == '_ocr_worker_main')
        source = ast.unparse(worker)
        self.assertLess(source.index('OCR 모델 누락'), source.index('import torch'))
        self.assertIn('download_enabled=False', source)

    def test_original_punishment_defaults_preserved(self):
        config_node = next(n for n in TREE.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'DEFAULT_CONFIG' for t in n.targets))
        config = ast.literal_eval(config_node.value)
        self.assertEqual(config['warning_limit'], 1)
        self.assertEqual(config['timeout_seconds'], 3600)
        self.assertFalse(config['retain_originals'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
