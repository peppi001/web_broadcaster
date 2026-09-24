"""Regression coverage for Scheduler URL-specific fixed metadata, including queued playback."""
from __future__ import annotations

import ast
import contextlib
from datetime import datetime, timedelta
import threading
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'app.py').read_text(encoding='utf-8')
FUNCTIONS = {node.name: node for node in ast.parse(SOURCE).body if isinstance(node, ast.FunctionDef)}


def isolate(name, namespace):
    module = ast.fix_missing_locations(ast.Module(body=[FUNCTIONS[name]], type_ignores=[]))
    exec(compile(module, str(ROOT / 'app.py'), 'exec'), namespace)
    return namespace[name]


class SchedulerUrlMetadataTests(unittest.TestCase):
    def test_url_modal_collects_metadata_and_saves_it_with_rule(self):
        ui = (ROOT / 'html/static/scheduler.js').read_text(encoding='utf-8')
        html = (ROOT / 'html/broadcaster.html').read_text(encoding='utf-8')
        self.assertIn('allowCustomMetadata: true,', ui)
        self.assertIn('defaultCustomMetadata: previous ? schedulerUrlCustomMetadata', ui)
        self.assertIn('schedulerUrlCustomMetadata = values.custom_metadata;', ui)
        self.assertIn('schedulerUrlCustomMetadata = insertKind === \'stream\' ? String(ruleData.custom_metadata || \'\')', ui)
        self.assertEqual(ui.count('custom_metadata: schedulerUrlCustomMetadata'), 2)
        self.assertIn('schedulerUrlCustomMetadata = \'\';', ui)
        self.assertIn('data-rule-custom-metadata', ui)
        self.assertIn('id="scheduler-url-custom-metadata"', html)
        self.assertIn('placeholder="e.g. Radio - Live Show"', html)

    def test_rule_title_validation_and_non_url_fallback(self):
        parse = isolate('_scheduler_rule_custom_title', {})
        self.assertEqual(parse({'custom_metadata': ' Radio - Live Show '}, 'stream'), 'Radio - Live Show')
        self.assertEqual(parse({}, 'stream'), '')
        self.assertEqual(parse({'custom_metadata': 'Fixed'}, 'file'), '')
        self.assertEqual(parse({'custom_metadata': 'Fixed'}, 'dir'), '')
        for value in ('a\nb', '\x7f', 'é' * 121):
            with self.subTest(value=value[:10]), self.assertRaises(ValueError):
                parse({'custom_metadata': value}, 'stream')

    def test_rule_title_persists_per_station_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            files = {key: Path(directory) / f'{key}.db' for key in ('A', 'B')}
            for path in files.values():
                with sqlite3.connect(path) as db:
                    db.executescript('''
                        CREATE TABLE scheduler_rules (id INTEGER PRIMARY KEY);
                        CREATE TABLE scheduler_rule_custom_metadata (
                            rule_id INTEGER PRIMARY KEY, custom_metadata TEXT NOT NULL DEFAULT '');
                        CREATE TRIGGER scheduler_rule_custom_metadata_cleanup
                        AFTER DELETE ON scheduler_rules BEGIN
                            DELETE FROM scheduler_rule_custom_metadata WHERE rule_id=OLD.id;
                        END;
                        INSERT INTO scheduler_rules(id) VALUES (7), (8);
                    ''')
            save = isolate('_write_scheduler_rule_custom_title', {})
            for station, text in [('A', 'A - Live'), ('B', 'B - Live')]:
                with sqlite3.connect(files[station]) as db:
                    save(db, 7, text)
                    db.commit()
            with sqlite3.connect(files['A']) as a, sqlite3.connect(files['B']) as b:
                self.assertEqual(a.execute('SELECT custom_metadata FROM scheduler_rule_custom_metadata WHERE rule_id=7').fetchone()[0], 'A - Live')
                self.assertEqual(b.execute('SELECT custom_metadata FROM scheduler_rule_custom_metadata WHERE rule_id=7').fetchone()[0], 'B - Live')
                save(a, 7, '')
                self.assertIsNone(a.execute('SELECT * FROM scheduler_rule_custom_metadata WHERE rule_id=7').fetchone())
                self.assertEqual(b.execute('SELECT custom_metadata FROM scheduler_rule_custom_metadata WHERE rule_id=7').fetchone()[0], 'B - Live')
                save(a, 8, 'Another title')
                a.execute('DELETE FROM scheduler_rules WHERE id=8')
                self.assertIsNone(a.execute('SELECT * FROM scheduler_rule_custom_metadata WHERE rule_id=8').fetchone())

    def test_create_list_edit_and_delete_rule_keeps_optional_title(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'station.db'
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE scheduler_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, is_enabled INTEGER NOT NULL,
                        auto_start INTEGER NOT NULL, name TEXT NOT NULL, run_when TEXT NOT NULL,
                        insert_kind TEXT NOT NULL, insert_value TEXT NOT NULL, priority TEXT NOT NULL,
                        last_run_at TEXT, next_run_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE scheduler_rule_custom_metadata (
                        rule_id INTEGER PRIMARY KEY, custom_metadata TEXT NOT NULL DEFAULT '');
                    CREATE TRIGGER scheduler_rule_custom_metadata_cleanup
                    AFTER DELETE ON scheduler_rules BEGIN
                        DELETE FROM scheduler_rule_custom_metadata WHERE rule_id=OLD.id;
                    END;
                """)
            class FakeRequest:
                payload = {}
                def get_json(self, *, force):
                    return self.payload
            request = FakeRequest()
            class FakeApp:
                def route(self, *_args, **_kwargs):
                    return lambda function: function
            ns = {
                'app': FakeApp(), 'login_required': lambda fn: fn,
                'init_db': lambda: None, 'request': request,
                'get_active_station_db_path': lambda: str(path),
                'get_db': lambda: sqlite3.connect(path),
                'jsonify': lambda result: result, 'url_for': lambda _: '/broadcaster',
                'sqlite3': sqlite3, 'datetime': datetime,
                'compute_next_run_at': lambda *_args: '2026-09-21T20:00:15',
                '_utc_now_naive': datetime.now,
            }
            ns['_scheduler_rule_custom_title'] = isolate('_scheduler_rule_custom_title', ns)
            ns['_write_scheduler_rule_custom_title'] = isolate('_write_scheduler_rule_custom_title', ns)
            create = isolate('api_scheduler_create_rule', ns)
            update = isolate('api_scheduler_update_rule', ns)
            listing = isolate('api_scheduler_rules_list', ns)
            payload = {
                'name': 'Scheduled stream', 'run_when': 'Everyday 20:00:15',
                'insert_kind': 'stream', 'insert_value': '60:https://radio.example/live',
                'priority': 'immediate', 'custom_metadata': 'Radio - Live Show',
            }
            request.payload = payload
            self.assertTrue(create()['ok'])
            rules = listing()['rules']
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]['custom_metadata'], 'Radio - Live Show')
            rule_id = rules[0]['id']
            request.payload = dict(payload, custom_metadata='New show title')
            self.assertTrue(update(rule_id)['ok'])
            self.assertEqual(listing()['rules'][0]['custom_metadata'], 'New show title')
            request.payload = dict(payload, custom_metadata='')
            self.assertTrue(update(rule_id)['ok'])
            self.assertEqual(listing()['rules'][0]['custom_metadata'], '')
            request.payload = dict(payload, insert_kind='file', insert_value='/station.mp3', custom_metadata='Ignored')
            self.assertTrue(update(rule_id)['ok'])
            self.assertEqual(listing()['rules'][0]['custom_metadata'], '')
            request.payload = dict(payload, custom_metadata='a\nb')
            self.assertEqual(create()[1], 400)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM scheduler_rules').fetchone()[0], 1)

    def test_end_priority_saves_fixed_title_before_replan(self):
        events=[]
        namespace={
            '_enqueue_track_ids_for_station': lambda *a, **kw: events.append((a,kw)) or True,
        }
        run = isolate('_apply_scheduler_rule_queue_action_for_station', namespace)
        self.assertTrue(run('A', [101], 'end', custom_metadata='Radio - Live Show'))
        self.assertEqual(events, [(('A', [101], 'end'), {'custom_metadata':'Radio - Live Show'})])
        events.clear()
        self.assertTrue(run('A', [101], 'end'))
        self.assertEqual(events, [(('A', [101], 'end'), {})])

    def test_immediate_and_next_priorities_assign_title_to_new_queue_id_before_activation(self):
        for priority in ('next', 'immediate'):
            with self.subTest(priority=priority):
                events=[]
                class Repo:
                    def remove_queue_items(self, ids, *, station_key):
                        events.append(('remove', station_key, ids))
                ns={
                    '_enqueue_track_ids_return_queue_ids_for_station': lambda *a: events.append(('enqueue',)) or [401],
                    '_mark_queue_items_origin_for_station': lambda *a: events.append(('origin',)) or True,
                    '_save_queue_url_custom_metadata': lambda station, ids, title: events.append(('metadata', station, ids, title)),
                    '_move_station_queue_ids_to_front': lambda *a: events.append(('move',)) or True,
                    '_get_playback_repository': lambda: Repo(),
                    'wake_autodj_worker': lambda: events.append(('wake',)),
                    'station_runtime_context': lambda _: contextlib.nullcontext(),
                    '_ab_schedule_async_replan': lambda *_: events.append(('replan',)),
                    '_perform_station_next_action': lambda *_: events.append(('next',)) or True,
                    'time': type('Time', (), {'sleep': staticmethod(lambda *_: None)})(),
                }
                run=isolate('_apply_scheduler_rule_queue_action_for_station',ns)
                self.assertTrue(run('A',[31],priority,custom_metadata='Fixed Title'))
                order=[event[0] for event in events]
                self.assertEqual(order[:4], ['enqueue','origin','metadata','move'])
                self.assertLess(order.index('metadata'),order.index('replan'))
                if priority == 'immediate':
                    self.assertLess(order.index('metadata'),order.index('next'))
                self.assertEqual(events[2], ('metadata','A',[401],'Fixed Title'))

    def test_title_write_failure_prevents_scheduler_next_and_rolls_back_queue_item(self):
        events=[]
        class Repo:
            def remove_queue_items(self, ids, *, station_key):
                events.append(('remove',station_key,ids))
        def fail_save(*_args):
            raise sqlite3.OperationalError('simulated write failure')
        ns={
            '_enqueue_track_ids_return_queue_ids_for_station': lambda *_: [55],
            '_mark_queue_items_origin_for_station': lambda *_: True,
            '_save_queue_url_custom_metadata': fail_save,
            '_get_playback_repository': lambda: Repo(),
            '_perform_station_next_action': lambda *_: events.append(('next',)) or True,
        }
        run=isolate('_apply_scheduler_rule_queue_action_for_station',ns)
        self.assertFalse(run('A',[11],'immediate',custom_metadata='Fixed'))
        self.assertEqual(events,[('remove','A',[55])])

    def test_due_scheduler_dispatches_only_own_station_fixed_title(self):
        with tempfile.TemporaryDirectory() as directory:
            stations = {key: Path(directory) / f'{key}.db' for key in ('A', 'B')}
            due = (datetime.now() - timedelta(seconds=1)).replace(microsecond=0).isoformat(timespec='seconds')
            for station, path in stations.items():
                with sqlite3.connect(path) as db:
                    db.executescript("""
                        CREATE TABLE scheduler_rules (
                            id INTEGER PRIMARY KEY, is_enabled INTEGER NOT NULL, next_run_at TEXT,
                            insert_kind TEXT, insert_value TEXT, priority TEXT, run_when TEXT);
                        CREATE TABLE scheduler_rule_custom_metadata (
                            rule_id INTEGER PRIMARY KEY, custom_metadata TEXT NOT NULL DEFAULT '');
                    """)
                    db.execute('INSERT INTO scheduler_rules VALUES (1, 1, ?, ?, ?, ?, ?)',
                               (due, 'stream', '60:https://radio.example/live', 'immediate', 'Everyday 20:00:15'))
                    if station == 'A':
                        db.execute('INSERT INTO scheduler_rule_custom_metadata VALUES (1, ?)', ('A - Show',))
            events = []
            ns = {
                '_SCHEDULER_LOCK': threading.Lock(), '_SCHEDULER_LAST_ON_AIR_BY_STATION': {},
                'datetime': datetime, 'sqlite3': sqlite3,
                'get_registered_station_keys': lambda: ['A', 'B'],
                'is_station_on_air': lambda station: True,
                'get_db_for_station': lambda station: sqlite3.connect(stations[station]),
                '_scheduler_normalize_overdue_rules_for_station': lambda *_: 0,
                '_resolve_insert_to_track_ids_for_station': lambda station, *_: [7],
                '_apply_scheduler_rule_queue_action_for_station': lambda station, ids, priority, **kw: (
                    events.append((station, ids, priority, kw)) or True
                ),
                '_scheduler_rule_is_recurring': lambda *_: True,
                '_scheduler_update_after_run_for_station': lambda *_: None,
                'NoActiveStationError': type('NoActiveStationError', (Exception,), {}),
            }
            tick = isolate('scheduler_process_due_once', ns)
            self.assertTrue(tick())
            self.assertEqual(events, [
                ('A', [7], 'immediate', {'custom_metadata': 'A - Show'}),
                ('B', [7], 'immediate', {}),
            ])

    def test_due_rule_and_listing_read_metadata_from_rule_companion_table(self):
        self.assertIn("LEFT JOIN scheduler_rule_custom_metadata AS metadata ON metadata.rule_id = rules.id", SOURCE)
        self.assertIn('custom_metadata = str(r.get("custom_metadata") or "").strip() if insert_kind == "stream" else ""', SOURCE)
        self.assertIn('station_key, track_ids, priority, custom_metadata=custom_metadata', SOURCE)
        self.assertIn('AFTER DELETE ON scheduler_rules', SOURCE)
        self.assertIn('_write_scheduler_rule_custom_title(conn, int(c.lastrowid), custom_metadata)', SOURCE)
        self.assertIn('_write_scheduler_rule_custom_title(conn, rule_id, custom_metadata)', SOURCE)


if __name__ == '__main__':
    unittest.main()
