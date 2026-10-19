import json
import ssl
import tempfile
import unittest
from pathlib import Path

from factory.core.diagnostics import classify_error, observation, retry_decision, IncidentJournal, safe_text, safe_log_value, ActivityTracker


class ErrorDiagnosticsTest(unittest.TestCase):
    def test_shared_native_and_http_classification(self):
        cases = json.loads(Path('tests/fixtures/diagnostics/classification.json').read_text())
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(classify_error(**case['input']), case['code'])

    def test_tls_type_beats_untrusted_provider_hint(self):
        self.assertEqual(classify_error(ssl.SSLCertVerificationError(1, 'fixture')), 'TLS.CERTIFICATE_REJECTED')
        event = observation('TLS.CERTIFICATE_REJECTED', component='model', operation='request', endpoint_role='runtime_to_provider')
        self.assertFalse(retry_decision(event, attempt=1, elapsed_s=0, replay_safe=True)['retry'])

    def test_accepted_write_and_unknown_delivery_require_reconciliation(self):
        for delivery in ['accepted', 'unknown']:
            event = observation('NET.CONNECTION_RESET', component='bridge', operation='create', endpoint_role='mobile_to_bridge', delivery=delivery)
            decision = retry_decision(event, attempt=1, elapsed_s=0, replay_safe=False)
            self.assertFalse(decision['retry'])
            self.assertEqual(decision['action'], 'reconcile')

    def test_retry_budget_and_retry_after(self):
        event = observation('HTTP.RATE_LIMITED', component='model', operation='read', endpoint_role='runtime_to_provider', retry_after_s=6)
        self.assertEqual(retry_decision(event, attempt=1, elapsed_s=0, replay_safe=True, random_value=0)['delay_ms'], 6000)
        self.assertFalse(retry_decision(event, attempt=3, elapsed_s=0, replay_safe=True)['retry'])
        self.assertFalse(retry_decision(event, attempt=1, elapsed_s=29, replay_safe=True)['retry'])

    def test_incident_recovery_is_owned_stable_and_read_is_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = IncidentJournal(Path(temporary)/'incidents.json')
            a = observation('NET.CONNECTION_RESET', component='mobile', operation='read', endpoint_role='mobile_to_bridge', owner='connection-a')
            first = journal.observe(a, now=100)
            journal.acknowledge(first['incident_id'])
            self.assertEqual(journal.observe(a, now=101)['observations'], 1)
            self.assertEqual(journal.observe({**a,'event_id':'second-fixture'}, now=101)['observations'], 2)
            self.assertFalse(journal.recover(owner='connection-b', stable_s=8, now=102))
            self.assertFalse(journal.recover(owner='connection-a', stable_s=1, now=102))
            self.assertTrue(journal.recover(owner='connection-a', stable_s=5, now=106))
            self.assertEqual(journal.snapshot()['incidents'][0]['state'], 'recovered')
            self.assertEqual(journal.snapshot()['incidents'][0]['recovery_proof'], 'stable_ready')
            next_episode = journal.observe(a, now=107)
            self.assertNotEqual(first['incident_id'], next_episode['incident_id'])
            self.assertFalse(next_episode['read'])
            self.assertEqual(len(IncidentJournal(Path(temporary)/'incidents.json').snapshot()['incidents']), 2)

    def test_snapshot_is_bounded_and_does_not_export_raw_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = IncidentJournal(Path(temporary)/'incidents.json', limit=2)
            for i in range(5):
                event = observation('INTERNAL.UNEXPECTED', component='fixture', operation='read', endpoint_role='runtime_to_tool', owner=str(i))
                event['raw_body'] = 'secret fixture content'
                journal.observe(event)
            snapshot = journal.snapshot()
            self.assertEqual(len(snapshot['incidents']), 2)
            self.assertGreater(snapshot['health']['dropped'], 0)
            self.assertNotIn('secret fixture content', json.dumps(snapshot))

    def test_error_text_is_redacted_without_mutating_tool_contracts(self):
        text = safe_text('Bearer fixture-token api_key="fixture-secret" https://u:p@example.test/a?token=value#private')
        for secret in ['fixture-token', 'fixture-secret', 'u:p', 'token=value', '#private']:
            self.assertNotIn(secret, text)

    def test_generated_client_catalogues_are_current(self):
        from tools.generate_error_contract import outputs, ROOT
        for name, expected in outputs().items():
            self.assertEqual((ROOT/name).read_text(), expected, name)

    def test_diagnostic_sanitization_preserves_semantic_booleans_and_counters(self):
        original={'is_secret':False,'token_count':17,'authorization':'Bearer fixture-private','input':'token=fixture-private'}
        safe=safe_log_value(original)
        self.assertIs(safe['is_secret'],False)
        self.assertEqual(safe['token_count'],17)
        self.assertNotIn('fixture-private',json.dumps(safe))
        self.assertEqual(original['authorization'],'Bearer fixture-private')

    def test_classifier_failure_does_not_replace_original_error(self):
        class BadError(Exception):
            @property
            def code(self):
                raise RuntimeError('classifier fixture')
            def __str__(self):
                raise RuntimeError('formatter fixture')
        error=BadError()
        self.assertEqual(classify_error(error),'INTERNAL.UNEXPECTED')
        self.assertEqual(safe_text(error),'[unavailable]')

    def test_failed_storage_is_visible_without_losing_original_incident(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'occupied';target.mkdir()
            journal=IncidentJournal(target)
            event=observation('INTERNAL.UNEXPECTED',component='fixture',operation='read',endpoint_role='runtime_to_tool')
            journal.observe(event)
            snapshot=journal.snapshot()
            self.assertEqual(len(snapshot['incidents']),1)
            self.assertEqual(snapshot['health']['state'],'write_failed')

    def test_progress_uncertainty_does_not_fail_or_stop_a_task(self):
        tracker=ActivityTracker()
        tracker.observe({'kind':'task.start','task_id':'fixture','payload':{}},now=0)
        self.assertEqual(tracker.snapshot(now=130)[0]['progress_state'],'unconfirmed')
        self.assertEqual(tracker.snapshot(now=130)[0]['status'],'active')
        tracker.observe({'kind':'model.text_delta','task_id':'fixture','payload':{'text':'not retained'}},now=190)
        self.assertEqual(tracker.snapshot(now=200)[0]['progress_state'],'long_running')
        self.assertNotIn('not retained',json.dumps(tracker.snapshot(now=200)))

    def test_snapshot_bounds_excessive_metadata_and_corrupt_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'journal.json'
            target.write_text(json.dumps({'incidents':[{'incident_id':'fixture','observations':'bad','error':{'code':[]}}]}))
            journal=IncidentJournal(target)
            journal.observe(observation('INTERNAL.UNEXPECTED',component='fixture',operation='read',endpoint_role='runtime_to_tool'))
            self.assertLess(len(json.dumps(journal.snapshot()).encode()),524288)

    def test_terminal_duration_stops_and_explicit_resume_starts_a_new_observed_run(self):
        tracker = ActivityTracker()
        tracker.observe({'kind': 'task.start', 'task_id': 'fixture'}, now=10)
        tracker.observe({'kind': 'task.finish', 'task_id': 'fixture', 'payload': {'status': 'done'}}, now=20)
        self.assertEqual(tracker.snapshot(now=100)[0]['observed_duration_ms'], 10000)
        tracker.observe({'kind': 'task.start', 'task_id': 'fixture'}, now=110)
        resumed = tracker.snapshot(now=115)[0]
        self.assertEqual(resumed['status'], 'active')
        self.assertEqual(resumed['observed_duration_ms'], 5000)


if __name__ == '__main__':
    unittest.main()
