import copy
import json
import unittest

try:
    from ai_test_agent.m2_observation import assess_observation
except ImportError:
    assess_observation = None


PACKAGE = 'com.example.game'
SINCE = '10-16 10:00:00.000'


def line(message, *, pid='123', timestamp='10-16 10:00:01.000', tag='Unity', level='I'):
    return f'{timestamp} {pid:>5} {pid:>5} {level} {tag}: {message}'


def snapshot(**changes):
    value = {'pid': '123', 'foreground': PACKAGE, 'logs': '',
             'ui_nodes': [], 'screenshot_valid': True}
    value.update(changes)
    return value


def readiness(**changes):
    value = {'approved': True, 'source_ref': 'approved fixture: startup requirement',
             'log_markers': ['GAME READY'], 'ui_texts': [], 'ui_classes': []}
    value.update(changes)
    return value


class ObservationTests(unittest.TestCase):
    def assess(self, value=None, requirement=None, *, observed_pids=None, since=SINCE):
        self.assertIsNotNone(assess_observation, 'M2 observation assessment is not implemented')
        return assess_observation(value or snapshot(), requirement or readiness(), package=PACKAGE,
                                  observed_pids={'123'} if observed_pids is None else observed_pids,
                                  since=since)

    def test_waits_for_late_readiness_signal(self):
        self.assertEqual(self.assess()['status'], 'WAIT')
        result = self.assess(snapshot(logs=line('GAME READY')))
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['runtime'], {'server': None, 'resource_versions': None})
        self.assertTrue(result['reasons'])

    def test_empty_log_and_unity_root_are_not_business_readiness(self):
        result = self.assess(snapshot(ui_nodes=[{'class': 'com.unity3d.player.UnityPlayer'}]))
        self.assertEqual(result['status'], 'WAIT')

    def test_unapproved_readiness_blocks_even_with_matching_signal(self):
        self.assertEqual(self.assess(snapshot(logs=line('GAME READY')),
                                     readiness(approved=False))['status'], 'BLOCKED')

    def test_pid_screenshot_and_server_are_not_a_readiness_contract(self):
        result = self.assess(snapshot(logs=line('Hot InitRequestUrl Server Type SandboxServer')),
                             readiness(log_markers=[]))
        self.assertEqual(result['status'], 'BLOCKED')

    def test_approved_readiness_requires_source_reference(self):
        result = self.assess(snapshot(logs=line('GAME READY')), readiness(source_ref=''))
        self.assertEqual(result['status'], 'BLOCKED')

    def test_all_configured_markers_are_required_and_literal(self):
        requirement = readiness(log_markers=['[READY].*', 'MENU READY'])
        self.assertEqual(self.assess(snapshot(logs=line('[READY].*')), requirement)['status'], 'WAIT')
        self.assertEqual(self.assess(snapshot(logs=line('READY anything MENU READY')), requirement)['status'], 'WAIT')
        result = self.assess(snapshot(logs=line('[READY].*') + '\n' + line('MENU READY')), requirement)
        self.assertEqual(result['status'], 'READY')

    def test_log_markers_match_literal_content_in_the_actual_threadtime_line(self):
        result = self.assess(snapshot(logs=line('GAME READY')), readiness(log_markers=['Unity: GAME READY']))
        self.assertEqual(result['status'], 'READY')

    def test_markers_from_previous_or_other_pid_are_not_reused(self):
        for pid in ['122', '987']:
            with self.subTest(pid=pid):
                result = self.assess(snapshot(logs=line('GAME READY', pid=pid)), observed_pids={'122', '123'})
                self.assertEqual(result['status'], 'WAIT')

    def test_required_markers_cannot_be_combined_across_pids(self):
        result = self.assess(snapshot(logs=line('FIRST', pid='122') + '\n' + line('SECOND')),
                             readiness(log_markers=['FIRST', 'SECOND']), observed_pids={'122', '123'})
        self.assertEqual(result['status'], 'WAIT')

    def test_old_and_untimed_markers_are_not_current_evidence(self):
        for logs in [line('GAME READY', timestamp='10-16 09:59:59.999'),
                     'Unity: GAME READY', line('GAME READY', timestamp='09-16 10:00:01.000')]:
            with self.subTest(logs=logs):
                self.assertEqual(self.assess(snapshot(logs=logs))['status'], 'WAIT')

    def test_timestamp_window_accepts_short_new_year_rollover(self):
        result = self.assess(snapshot(logs=line('GAME READY', timestamp='01-01 00:00:00.050')),
                             since='12-31 23:59:59.900')
        self.assertEqual(result['status'], 'READY')

    def test_previous_year_log_is_not_future_evidence(self):
        result = self.assess(snapshot(logs=line('GAME READY', timestamp='12-31 23:59:59.999')),
                             since='01-01 00:00:00.000')
        self.assertEqual(result['status'], 'WAIT')

    def test_ui_text_content_description_and_class_are_explicit(self):
        requirement = readiness(log_markers=[], ui_texts=['Start', 'Main menu'], ui_classes=['android.widget.Button'])
        nodes = [{'text': 'Start', 'class': 'android.widget.Button', 'package': PACKAGE},
                 {'content-desc': 'Main menu', 'package': PACKAGE}]
        self.assertEqual(self.assess(snapshot(ui_nodes=nodes), requirement)['status'], 'READY')
        nodes[0]['text'] = 'Start downloading'
        self.assertEqual(self.assess(snapshot(ui_nodes=nodes), requirement)['status'], 'WAIT')

    def test_other_package_ui_node_does_not_match(self):
        requirement = readiness(log_markers=[], ui_texts=['Start'])
        result = self.assess(snapshot(ui_nodes=[{'text': 'Start', 'package': 'com.example.other'}]), requirement)
        self.assertEqual(result['status'], 'WAIT')

    def test_log_and_ui_requirements_are_both_required(self):
        requirement = readiness(ui_texts=['Start'])
        self.assertEqual(self.assess(snapshot(logs=line('GAME READY')), requirement)['status'], 'WAIT')
        result = self.assess(snapshot(logs=line('GAME READY'), ui_nodes=[{'text': 'Start'}]), requirement)
        self.assertEqual(result['status'], 'READY')

    def test_ready_needs_current_pid_foreground_and_valid_screenshot(self):
        for changes, expected in [({'pid': ''}, 'WAIT'),
                                  ({'foreground': 'com.example.other'}, 'BLOCKED'),
                                  ({'foreground': PACKAGE + '.other'}, 'BLOCKED'),
                                  ({'screenshot_valid': False}, 'BLOCKED')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.assess(snapshot(logs=line('GAME READY'), **changes))['status'], expected)

    def test_runtime_values_come_only_from_current_pid_and_window(self):
        logs = '\n'.join([line('Hot InitRequestUrl Server Type Production', pid='987'),
                          line('Hot InitRequestUrl Server Type OldServer', timestamp='10-16 09:59:59.999'),
                          line('Hot InitRequestUrl Server Type SandboxServer'),
                          line('localVersion:995 remoteVersion:996'), line('GAME READY')])
        result = self.assess(snapshot(logs=logs))
        self.assertEqual(result['runtime'], {'server': 'SandboxServer', 'resource_versions': ['995', '996']})
        self.assertEqual(result['status'], 'READY')

    def test_java_fatal_with_adjacent_target_process_is_failure(self):
        logs = '\n'.join([line('FATAL EXCEPTION: main', tag='AndroidRuntime', level='E'),
                          line(f'Process: {PACKAGE}, PID: 123', tag='AndroidRuntime', level='E'),
                          line('GAME READY')])
        result = self.assess(snapshot(logs=logs))
        self.assertEqual((result['status'], result['category']), ('FAIL', 'JAVA_CRASH'))
        self.assertIn(1, result['diagnostics'][0]['line_numbers'])

    def test_java_fatal_can_be_attributed_by_explicit_package_before_pid_sample(self):
        logs = '\n'.join([line('FATAL EXCEPTION: main', pid='122', tag='AndroidRuntime'),
                          line(f'Process: {PACKAGE}:worker, PID: 122', pid='122', tag='AndroidRuntime')])
        self.assertEqual(self.assess(snapshot(logs=logs), observed_pids={'123'})['status'], 'FAIL')

    def test_java_fatal_does_not_pair_with_different_emitter_or_distant_process_line(self):
        for logs in [line('FATAL EXCEPTION: main', tag='AndroidRuntime') + '\n' +
                     line(f'Process: {PACKAGE}, PID: 123', pid='987', tag='AndroidRuntime'),
                     line('FATAL EXCEPTION: main', tag='AndroidRuntime') + '\n' +
                     line(f'Process: {PACKAGE}, PID: 123', timestamp='10-16 10:00:40.000', tag='AndroidRuntime')]:
            with self.subTest(logs=logs):
                self.assertEqual(self.assess(snapshot(logs=logs))['status'], 'BLOCKED')

    def test_native_fatal_matches_every_observed_pid_in_current_round(self):
        logs = line('Fatal signal 11 (SIGSEGV), code 1 in tid 122 (UnityMain), pid 122', pid='122', tag='libc')
        result = self.assess(snapshot(logs=logs), observed_pids={'122', '123'})
        self.assertEqual((result['status'], result['category']), ('FAIL', 'NATIVE_CRASH'))

    def test_native_fatal_can_match_explicit_package_when_pid_not_sampled(self):
        logs = line(f'Fatal signal 6 (SIGABRT) in tid 122 (UnityMain), pid 122 ({PACKAGE})',
                    pid='122', tag='libc')
        self.assertEqual(self.assess(snapshot(logs=logs))['status'], 'FAIL')

    def test_native_fatal_can_match_explicit_tombstone_package(self):
        logs = line(f'Fatal signal 6 (SIGABRT), pid 122 >>> {PACKAGE}:worker <<<', pid='900', tag='DEBUG')
        self.assertEqual(self.assess(snapshot(logs=logs))['status'], 'FAIL')

    def test_current_target_pid_is_crash_evidence_even_when_observed_set_is_empty(self):
        logs = line('Fatal signal 11 (SIGSEGV), code 1', tag='libc')
        self.assertEqual(self.assess(snapshot(logs=logs), observed_pids=set())['status'], 'FAIL')

    def test_other_package_crash_is_ignored_without_exposing_payload(self):
        logs = '\n'.join([line('FATAL EXCEPTION: PRIVATE_OTHER_PAYLOAD', pid='987', tag='AndroidRuntime'),
                          line('Process: com.example.other, PID: 987 PRIVATE_OTHER_PAYLOAD', pid='987', tag='AndroidRuntime'),
                          line('GAME READY')])
        result = self.assess(snapshot(logs=logs))
        self.assertEqual(result['status'], 'READY')
        self.assertNotIn('PRIVATE_OTHER_PAYLOAD', json.dumps(result))

    def test_explicit_other_package_overrides_native_pid_match(self):
        logs = line('Fatal signal 11 (SIGSEGV) in tid 123 (main), pid 123 (com.example.other) PRIVATE_OTHER_PAYLOAD', tag='libc')
        result = self.assess(snapshot(logs=logs + '\n' + line('GAME READY')))
        self.assertEqual(result['status'], 'READY')
        self.assertNotIn('PRIVATE_OTHER_PAYLOAD', json.dumps(result))

    def test_old_target_crash_is_ignored(self):
        logs = '\n'.join([line('FATAL EXCEPTION: main', timestamp='10-16 09:59:59.500', tag='AndroidRuntime'),
                          line(f'Process: {PACKAGE}, PID: 123', timestamp='10-16 09:59:59.600', tag='AndroidRuntime'),
                          line('Fatal signal 11', timestamp='10-16 09:59:59.700', tag='libc'), line('GAME READY')])
        self.assertEqual(self.assess(snapshot(logs=logs))['status'], 'READY')

    def test_target_anr_fails_even_with_live_pid_and_readiness_marker(self):
        logs = line(f'ANR in {PACKAGE}', pid='900', tag='ActivityManager', level='E')
        result = self.assess(snapshot(logs=logs + '\n' + line('GAME READY')))
        self.assertEqual((result['status'], result['category']), ('FAIL', 'ANR'))

    def test_system_am_anr_event_is_package_bound(self):
        logs = line(f'[0,123,{PACKAGE},950157893,Input dispatching timed out]', pid='900', tag='am_anr', level='I')
        self.assertEqual(self.assess(snapshot(logs=logs))['category'], 'ANR')

    def test_other_package_and_prefix_anrs_do_not_fail_target(self):
        for other in ['com.example.other', PACKAGE + '.other']:
            with self.subTest(other=other):
                logs = line(f'ANR in {other} PRIVATE_OTHER_PAYLOAD', pid='900', tag='ActivityManager')
                result = self.assess(snapshot(logs=logs + '\n' + line('GAME READY')))
                self.assertEqual(result['status'], 'READY')
                self.assertNotIn('PRIVATE_OTHER_PAYLOAD', json.dumps(result))

    def test_benign_mentions_of_exception_names_are_not_crash_events(self):
        logs = '\n'.join([line('ANR watchdog initialized'),
                          line('Fatal signal handler installed'),
                          line('No FATAL EXCEPTION found'), line('GAME READY')])
        self.assertEqual(self.assess(snapshot(logs=logs))['status'], 'READY')

    def test_unknown_fatal_is_blocked_and_diagnostic_is_payload_free(self):
        logs = line('Fatal signal 11 PRIVATE_UNKNOWN_PAYLOAD', pid='987', tag='libc')
        result = self.assess(snapshot(logs=logs + '\n' + line('GAME READY')))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertNotEqual(result['category'], 'GAME_BUG')
        self.assertTrue(result['diagnostics'])
        self.assertIn(1, result['diagnostics'][0]['line_numbers'])
        self.assertNotIn('PRIVATE_UNKNOWN_PAYLOAD', json.dumps(result))

    def test_untimed_fatal_does_not_become_current_target_failure(self):
        logs = '123 libc: Fatal signal 11 PRIVATE_UNKNOWN_PAYLOAD\n' + line('GAME READY')
        result = self.assess(snapshot(logs=logs))
        self.assertNotEqual(result['status'], 'FAIL')
        self.assertNotIn('PRIVATE_UNKNOWN_PAYLOAD', json.dumps(result))

    def test_invalid_since_blocks_and_empty_marker_is_not_vacuous_evidence(self):
        self.assertEqual(self.assess(snapshot(logs=line('GAME READY')), since='invalid')['status'], 'BLOCKED')
        self.assertEqual(self.assess(snapshot(logs=line('GAME READY')), readiness(log_markers=['']))['status'], 'BLOCKED')

    def test_assessment_does_not_mutate_inputs_or_reuse_previous_observation(self):
        value = snapshot(logs=line('GAME READY'))
        requirement = readiness()
        original = copy.deepcopy((value, requirement))
        self.assertEqual(self.assess(value, requirement)['status'], 'READY')
        self.assertEqual((value, requirement), original)
        self.assertEqual(self.assess()['status'], 'WAIT')


if __name__ == '__main__':
    unittest.main()
