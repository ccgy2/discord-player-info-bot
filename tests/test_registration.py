"""Offline tests of registration logic, using stdlib HTTP/Discord test doubles.

No Discord login, Firebase connection, or OCR occurs. Test runtime functions are
AST-extracted from the delivered bot.py to avoid importing its existing Firebase
startup side effects. This is NOT a discord.py SDK integration test.
"""
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'bot.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
NAMES = {
    'AUTOMOD_ROOT_NAMES', 'AUTOMOD_SLASH_PATCH_VERSION',
    'AutoModRegistrationError', '_slash_error', '_command_key',
    '_command_schema', '_matches_command', '_command_differences',
    '_normalise_command_payload', 'AutoModSlashRegistrar',
    '_automod_register_after_ready', '_automod_repair_permission',
    'automod_register_command', 'automod_registration_status_command',
    '_automod_register_joined_guild', 'load_cogs', 'setup_hook', 'on_ready',
}


class FakeHTTPError(Exception):
    def __init__(self, status=403, code=50001):
        super().__init__('credential-should-not-be-printed')
        self.status, self.code = status, code


class FakeRoute:
    def __init__(self, method, path, **params):
        self.method, self.path, self.params = method, path, params


class FakeHTTP:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.next_id = 10000
        self.error = None
        self.drop_writes = False
        self.fail_guilds = set()
        self.inject_unknown = False

    async def request(self, route, **kwargs):
        await asyncio.sleep(0)
        scope = route.params.get('guild_id')
        self.calls.append((route.method, scope, copy.deepcopy(kwargs.get('json'))))
        if self.error is not None:
            raise self.error
        if scope in self.fail_guilds:
            raise FakeHTTPError()
        rows = self.rows.setdefault(scope, {})
        if route.method == 'GET':
            return copy.deepcopy(list(rows.values()))
        if route.method != 'POST':
            raise AssertionError('Destructive/unsupported method: ' + route.method)
        payload = copy.deepcopy(kwargs['json'])
        key = payload.get('type', 1), payload['name']
        existing = rows.get(key)
        self.next_id += 1
        payload['id'] = existing['id'] if existing else str(self.next_id)
        payload['application_id'] = str(route.params['application_id'])
        payload['version'] = '20000'
        if scope is not None:
            payload['guild_id'] = str(scope)
        if not self.drop_writes:
            rows[key] = payload
        if self.inject_unknown:
            rows[(1, '외부에서추가')] = {'name': '외부에서추가', 'type': 1, 'id': '99999', 'description': 'keep'}
        return copy.deepcopy(payload)


class FakeCommand:
    def __init__(self, name, children=()):
        self.name = name
        self.payload = {
            'name': name, 'type': 1, 'description': name + ' 설명',
            'default_member_permissions': None, 'nsfw': False,
            'dm_permission': False,
            'options': [{'name': child, 'type': 1, 'description': child + ' 설명', 'options': []}
                        for child in children],
        }

    def to_dict(self, tree):
        return copy.deepcopy(self.payload)

    async def get_translated_payload(self, tree, translator):
        result = self.to_dict(tree)
        result['name_localizations'] = {'en-US': 'translated-' + str(len(self.name))}
        return result


class FakeTree:
    translator = None
    fallback_to_global = True
    def __init__(self, commands):
        self.commands = {command.name: command for command in commands}
        self.scoped = {}

    def get_command(self, name, *, guild=None):
        return self.commands.get(name) if guild is None else self.scoped.get(guild.id, {}).get(name)

    def get_commands(self):
        return list(self.commands.values())

    def add_command(self, command):
        if command.name in self.commands:
            raise AssertionError('overwrite')
        self.commands[command.name] = command


class FakeBot:
    def __init__(self, roots):
        self.owned = [FakeCommand(name, ('상태', '저장상태', '연결점검') if name == '자동관리' else ()) for name in roots]
        self.cog = types.SimpleNamespace(get_app_commands=lambda: self.owned)
        self.tree = FakeTree([*self.owned, FakeCommand('기존선수명령')])
        self.application_id = 123456789012345678
        self.http = FakeHTTP()
        self.guilds = [types.SimpleNamespace(id=101), types.SimpleNamespace(id=202)]
        self.extensions = {}
        self.user = 'test-bot'
        self.ready = False
        self.load_calls = []

    def get_cog(self, name):
        return self.cog if name == 'AutoMod' else None

    async def wait_until_ready(self):
        self.ready = True

    async def load_extension(self, name):
        if name == 'cogs.broken':
            raise FakeHTTPError(500)
        self.load_calls.append(name)
        self.extensions[name] = True


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        nodes = []
        for node in TREE.body:
            name = getattr(node, 'name', None)
            if isinstance(node, ast.Assign):
                name = getattr(node.targets[0], 'id', None)
            if name in NAMES:
                node = copy.deepcopy(node)
                if hasattr(node, 'decorator_list'):
                    node.decorator_list = []
                nodes.append(node)
        discord = types.ModuleType('discord')
        discord.Object = lambda *, id: types.SimpleNamespace(id=id)
        discord.HTTPException = FakeHTTPError
        discord.AllowedMentions = types.SimpleNamespace(none=lambda: None)
        http = types.ModuleType('discord.http')
        http.Route = FakeRoute
        self.mods = patch.dict(sys.modules, {'discord': discord, 'discord.http': http})
        self.mods.start()
        self.addCleanup(self.mods.stop)
        self.ns = {'asyncio': asyncio, 'json': json, 'discord': discord, 'print': lambda *a, **kw: None,
                   '__file__': str(SOURCE), 'BOT_PREFIX': '!'}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.bot = FakeBot(self.ns['AUTOMOD_ROOT_NAMES'])
        self.reg = self.ns['AutoModSlashRegistrar'](self.bot)
        self.ns.update(bot=self.bot, _automod_registrar=self.reg, _automod_registration_task=None)
        self.Error = self.ns['AutoModRegistrationError']

    def posts(self):
        return [row for row in self.bot.http.calls if row[0] == 'POST']

    async def test_guild_registers_all_seven_and_verifies(self):
        result = await self.reg.publish(guild_id=101)
        self.assertEqual(len(result['verified']), 7)
        self.assertEqual(result['writes'], 7)
        self.assertEqual([call[0] for call in self.bot.http.calls], ['GET'] + ['POST'] * 7 + ['GET'])

    async def test_repeated_publish_writes_nothing(self):
        await self.reg.publish(guild_id=101)
        result = await self.reg.publish(guild_id=101)
        self.assertEqual(result['writes'], 0)
        self.assertEqual(len(self.posts()), 7)

    async def test_concurrent_publish_is_serialized(self):
        results = await asyncio.gather(*(self.reg.publish(guild_id=101) for _ in range(5)))
        self.assertEqual(sum(r['writes'] for r in results), 7)
        self.assertEqual(len(self.posts()), 7)

    async def test_unknown_remote_guild_command_preserved(self):
        unrelated = {'id': '555', 'name': '기존관리', 'type': 1, 'description': 'do not delete'}
        self.bot.http.rows[101] = {(1, '기존관리'): copy.deepcopy(unrelated)}
        await self.reg.publish(guild_id=101)
        self.assertEqual(self.bot.http.rows[101][(1, '기존관리')], unrelated)

    async def test_remote_commands_added_mid_operation_preserved(self):
        self.bot.http.inject_unknown = True
        await self.reg.publish(guild_id=101)
        self.assertIn((1, '외부에서추가'), self.bot.http.rows[101])

    async def test_global_updates_loaded_and_preserves_unloaded(self):
        self.bot.http.rows[None] = {(1, '미로딩Cog명령'): {'id': '12', 'name': '미로딩Cog명령', 'type': 1}}
        result = await self.reg.publish()
        self.assertEqual(result['checked'], 8)
        self.assertIn((1, '미로딩Cog명령'), self.bot.http.rows[None])
        self.assertIn((1, '기존선수명령'), self.bot.http.rows[None])

    async def test_guild_only_updates_automod(self):
        await self.reg.publish(guild_id=101)
        self.assertNotIn((1, '기존선수명령'), self.bot.http.rows[101])

    async def test_each_guild_is_separate(self):
        await self.reg.publish(guild_id=101)
        await self.reg.publish(guild_id=202)
        self.assertNotEqual(self.bot.http.rows[101][(1, '자동관리')]['id'], self.bot.http.rows[202][(1, '자동관리')]['id'])

    async def test_existing_stale_group_updated_in_place(self):
        await self.reg.publish(guild_id=101)
        old_id = self.bot.http.rows[101][(1, '자동관리')]['id']
        self.bot.http.rows[101][(1, '자동관리')]['options'] = []
        result = await self.reg.publish(guild_id=101)
        self.assertEqual(result['writes'], 1)
        self.assertEqual(result['verified']['자동관리'], old_id)
        names = {v['name'] for v in self.bot.http.rows[101][(1, '자동관리')]['options']}
        self.assertIn('연결점검', names)

    async def test_global_server_contexts_explicit(self):
        await self.reg.publish()
        auto = self.bot.http.rows[None][(1, '자동관리')]
        self.assertEqual(auto['contexts'], [0])
        self.assertEqual(auto['integration_types'], [0])
        self.assertNotIn('dm_permission', auto)

    async def test_guild_payload_omits_global_context_fields(self):
        for command in self.bot.owned:
            command.payload.update(contexts=[0], integration_types=[0])
        await self.reg.publish(guild_id=101)
        for _, _, payload in self.posts():
            self.assertFalse({'contexts', 'integration_types', 'dm_permission'} & payload.keys())

    async def test_missing_cog_refuses_without_writes(self):
        self.bot.cog = None
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertEqual(self.posts(), [])

    async def test_missing_local_tree_repaired_from_cog(self):
        self.bot.tree.commands.pop('자동관리')
        await self.reg.publish(guild_id=101)
        self.assertIs(self.bot.tree.get_command('자동관리'), self.bot.owned[0])

    async def test_other_cog_name_conflict_not_overwritten(self):
        other = FakeCommand('자동관리')
        self.bot.tree.commands['자동관리'] = other
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertIs(self.bot.tree.commands['자동관리'], other)
        self.assertEqual(self.posts(), [])

    async def test_other_scoped_cog_conflict_not_overwritten(self):
        other = FakeCommand('자동관리')
        self.bot.tree.scoped[101] = {'자동관리': other}
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertEqual(self.posts(), [])

    async def test_required_diagnostic_subcommand_missing_refuses(self):
        self.bot.owned[0].payload['options'] = []
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertEqual(self.posts(), [])

    async def test_missing_required_root_refuses(self):
        self.bot.owned.pop()
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertEqual(self.posts(), [])

    async def test_disabled_global_fallback_refuses(self):
        self.bot.tree.fallback_to_global = False
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertEqual(self.posts(), [])

    async def test_network_error_does_not_claim_success(self):
        self.bot.http.error = FakeHTTPError(403)
        with self.assertRaises(FakeHTTPError):
            await self.reg.publish(guild_id=101)
        self.assertNotIn(101, self.reg.last_results)

    async def test_lost_or_unapplied_write_detected_by_requery(self):
        self.bot.http.drop_writes = True
        with self.assertRaisesRegex(self.Error, '재조회'):
            await self.reg.publish(guild_id=101)
        self.assertNotIn(101, self.reg.last_results)

    async def test_missing_application_id_refuses(self):
        self.bot.application_id = None
        with self.assertRaises(self.Error):
            await self.reg.publish(guild_id=101)
        self.assertEqual(self.posts(), [])

    async def test_inspection_read_only_even_when_cog_missing(self):
        self.bot.cog = None
        result = await self.reg.inspect(101)
        self.assertFalse(result['cog_loaded'])
        self.assertEqual([v[0] for v in self.bot.http.calls], ['GET', 'GET'])

    async def test_one_guild_failure_does_not_block_next_guild(self):
        self.bot.http.fail_guilds.add(101)
        await self.ns['_automod_register_after_ready']()
        self.assertTrue(self.bot.ready)
        self.assertIn(202, self.reg.last_results)
        self.assertNotIn(101, self.reg.last_results)

    async def test_translated_schema_is_used(self):
        self.bot.tree.translator = object()
        await self.reg.publish(guild_id=101)
        self.assertIn('en-US', self.bot.http.rows[101][(1, '자동관리')]['name_localizations'])
        self.assertEqual((await self.reg.publish(guild_id=101))['writes'], 0)

    async def test_owner_bootstrap_permission_no_firestore(self):
        ctx = types.SimpleNamespace(guild=types.SimpleNamespace(owner_id=77), author=types.SimpleNamespace(id=77))
        self.assertTrue(await self.ns['_automod_repair_permission'](ctx))

    async def test_admin_bootstrap_permission_checked_fresh(self):
        async def member(_):
            return types.SimpleNamespace(roles=[types.SimpleNamespace(id=5)])
        async def roles():
            return [types.SimpleNamespace(id=5, permissions=types.SimpleNamespace(administrator=True))]
        guild = types.SimpleNamespace(owner_id=1, fetch_member=member, fetch_roles=roles)
        ctx = types.SimpleNamespace(guild=guild, author=types.SimpleNamespace(id=77))
        self.assertTrue(await self.ns['_automod_repair_permission'](ctx))

    async def test_non_admin_and_dm_rejected(self):
        async def member(_):
            return types.SimpleNamespace(roles=[])
        async def roles():
            return []
        guild = types.SimpleNamespace(owner_id=1, fetch_member=member, fetch_roles=roles)
        ctx = types.SimpleNamespace(guild=guild, author=types.SimpleNamespace(id=77))
        self.assertFalse(await self.ns['_automod_repair_permission'](ctx))
        self.assertFalse(await self.ns['_automod_repair_permission'](types.SimpleNamespace(guild=None)))

    async def test_role_fetch_error_rejected(self):
        async def member(_):
            raise FakeHTTPError()
        guild = types.SimpleNamespace(owner_id=1, fetch_member=member)
        ctx = types.SimpleNamespace(guild=guild, author=types.SimpleNamespace(id=77))
        self.assertFalse(await self.ns['_automod_repair_permission'](ctx))

    def test_errors_never_echo_credential_like_exception_body(self):
        text = self.ns['_slash_error'](FakeHTTPError())
        self.assertIn('403', text)
        self.assertNotIn('credential-should-not-be-printed', text)

    def test_normalization_handles_discord_optional_defaults(self):
        left = FakeCommand('테스트').payload
        right = {**left, 'id': '99', 'version': '1', 'application_id': '123',
                 'name_localizations': None, 'description_localizations': {}}
        self.assertTrue(self.ns['_matches_command'](left, right, guild=True))

    def test_permission_zero_is_not_treated_as_unrestricted(self):
        left = FakeCommand('테스트').payload
        right = {**left, 'default_member_permissions': '0'}
        self.assertFalse(self.ns['_matches_command'](left, right, guild=True))

    def test_single_setup_hook_and_no_ready_resync(self):
        hooks = [node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'setup_hook']
        self.assertEqual(len(hooks), 1)
        ready = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'on_ready')
        self.assertNotIn('publish', ast.unparse(ready))
        self.assertFalse(any(isinstance(n, ast.Attribute) and n.attr == 'sync' for n in ast.walk(ready)))
        self.assertNotIn('load_cogs', ast.unparse(ready))

    def test_setup_schedules_ready_task_without_deadlocking(self):
        hook = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'setup_hook')
        text = ast.unparse(hook)
        self.assertIn('asyncio.create_task', text)
        self.assertNotIn('await bot.wait_until_ready', text)
        self.assertNotIn('await _automod_register_after_ready', text)

    def test_prefix_bootstrap_commands_are_guarded(self):
        for name in ('automod_register_command', 'automod_registration_status_command', 'automod_storage_diagnostic_command'):
            func = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
            decorators = '\n'.join(ast.unparse(node) for node in func.decorator_list)
            self.assertIn('commands.guild_only()', decorators)
            self.assertIn('commands.check(_automod_repair_permission)', decorators)
            self.assertIn('commands.cooldown', decorators)

    def test_no_on_message_override_or_command_redispatch(self):
        self.assertFalse(any(getattr(node, 'name', '') == 'on_message' for node in TREE.body))
        self.assertNotIn('process_commands(', SOURCE.read_text(encoding='utf-8'))


    def channel_payload(self):
        return {'name': '예외', 'type': 1, 'description': '검사 제외 채널과 역할',
                'options': [{'name': '채널', 'type': 1, 'description': '예외 채널',
                    'options': [{'name': '작업', 'type': 3, 'description': '작업', 'required': True,
                                 'choices': [{'name': '추가', 'value': '추가'}, {'name': '삭제', 'value': '삭제'}]},
                                {'name': '채널', 'type': 7, 'description': '대상', 'required': True,
                                 'channel_types': [0, 5, 10, 12, 11, 15, 16]}]}]}

    def test_channel_set_order_is_semantically_equal(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'][1]['channel_types'] = [0, 5, 10, 11, 12, 15, 16]
        self.assertTrue(self.ns['_matches_command'](expected, actual, guild=True))
        self.assertTrue(self.ns['_matches_command'](expected, actual, guild=False))

    def test_channel_duplicates_and_enum_numbers_normalized(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'][1]['channel_types'] += [11, 11]
        self.assertTrue(self.ns['_matches_command'](expected, actual, guild=True))

    def test_added_channel_permission_is_still_a_mismatch(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'][1]['channel_types'].append(2)
        self.assertFalse(self.ns['_matches_command'](expected, actual, guild=True))
        diff = self.ns['_command_differences'](expected, actual, guild=True)
        self.assertTrue(any('channel_types' in line for line in diff))

    def test_parameter_order_not_silently_normalized(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'].reverse()
        self.assertFalse(self.ns['_matches_command'](expected, actual, guild=True))

    def test_choices_order_is_preserved(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'][0]['choices'].reverse()
        self.assertFalse(self.ns['_matches_command'](expected, actual, guild=True))

    def test_none_optional_constraints_equal_omission(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'][1].update(min_value=None, max_value=None)
        self.assertTrue(self.ns['_matches_command'](expected, actual, guild=True))

    def test_required_permission_difference_remains_visible(self):
        expected = self.channel_payload()
        actual = copy.deepcopy(expected)
        actual['options'][0]['options'][1]['required'] = False
        self.assertFalse(self.ns['_matches_command'](expected, actual, guild=True))
        self.assertIn('required', ' '.join(self.ns['_command_differences'](expected, actual, guild=True)))

    def test_payload_normalisation_has_no_side_effects(self):
        payload = self.channel_payload()
        before = copy.deepcopy(payload)
        normalized = self.ns['_normalise_command_payload'](payload)
        self.assertEqual(before, payload)
        self.assertEqual(normalized['options'][0]['options'][1]['channel_types'], [0, 5, 10, 11, 12, 15, 16])
        self.assertEqual(normalized['options'][0]['options'][0], payload['options'][0]['options'][0])

    async def test_existing_discord_channel_sort_requires_no_repeat_upsert(self):
        owned = next(c for c in self.bot.owned if c.name == '예외')
        owned.payload = self.channel_payload()
        result = await self.reg.publish(guild_id=101)
        self.assertEqual(result['writes'], 7)
        actual = self.bot.http.rows[101][(1, '예외')]
        actual['options'][0]['options'][1]['channel_types'].reverse()
        self.assertEqual((await self.reg.publish(guild_id=101))['writes'], 0)

    def test_schema_diff_is_bounded_and_missing_detected(self):
        left = self.channel_payload()
        self.assertIn('응답에 없음', self.ns['_command_differences'](left, None, guild=True)[0])
        right = copy.deepcopy(left)
        right['options'] *= 10
        self.assertLessEqual(len(self.ns['_command_differences'](left, right, guild=True)), 8)


if __name__ == '__main__':
    unittest.main(verbosity=2)
