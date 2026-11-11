"""Tests offline cho cải tiến Bài 4 và bonus: kiểm tra nguồn, retry 429, rút gọn context."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from agent import run_agent, MAX_LIST_ITEMS, MAX_RATE_LIMIT_TRIES
from bonus.tools import BonusTools
from scenarios import ScenarioTools
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final


class SourceValidationTests(unittest.TestCase):
    def test_canonical_incident_url_is_accepted(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('Nguồn: https://www.githubstatus.com/incidents/xr3myzj7ms51')])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(any(t['event'] == 'validation_missing_source' for t in result['trace']))

    def test_every_provider_incident_must_be_cited(self):
        a = tool_call('a', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}')
        b = tool_call('b', 'get_incident_details', '{"provider":"cloudflare","incident_id":"yaa0witbl2me"}')
        a['calls'] += b['calls']
        a['message']['tool_calls'] += b['message']['tool_calls']
        cf = StatusTools().get_incident_details('cloudflare', 'yaa0witbl2me')['url']
        fake = FakeProvider([a, final('Chỉ có https://stspg.io/pjgptg3cfc6n'), final('https://stspg.io/pjgptg3cfc6n và ' + cf)])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual(result['status'], 'completed')
        gate = [t for t in result['trace'] if t['event'] == 'validation_missing_source']
        self.assertEqual(gate[0]['missing'], [cf])
        # Yêu cầu sửa được gửi lại cho model như một message user, sau assistant message bị từ chối.
        self.assertIn(cf, fake.histories[2][-1]['content'])

    def test_similar_id_prefix_is_not_a_citation(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('https://www.githubstatus.com/incidents/xr3myzj7ms51x')])
        self.assertEqual(run_agent('x', fake, StatusTools(), max_steps=2)['status'], 'max_steps')


class RobustnessTests(unittest.TestCase):
    def test_persistent_rate_limit_stops_calling_api(self):
        tools = BonusTools(scenario='rate_limit_always')
        responses = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(5)] + [final('Vercel chưa kiểm tra được')]
        result = run_agent('x', FakeProvider(responses), tools, max_steps=8)
        self.assertEqual(result['status'], 'completed')
        real_calls = [t for t in result['trace'] if t['event'] == 'tool']
        blocked = [t for t in result['trace'] if t['event'] == 'tool_blocked']
        self.assertEqual(len(real_calls), MAX_RATE_LIMIT_TRIES)
        self.assertEqual(len(blocked), 5 - MAX_RATE_LIMIT_TRIES)
        self.assertEqual(blocked[0]['result']['error'], 'RETRY_BUDGET_EXHAUSTED')

    def test_long_lists_are_compacted_before_reaching_model(self):
        fake = FakeProvider([tool_call('1', 'get_vercel_status', '{}'), final()])
        result = run_agent('x', fake, BonusTools(scenario='noisy'))
        observation = json.loads(fake.histories[1][-1]['content'])
        self.assertEqual(len(observation['distractors']), MAX_LIST_ITEMS + 1)
        self.assertEqual(observation['provider'], 'vercel')
        self.assertTrue(any(t['event'] == 'context_compacted' for t in result['trace']))

    def test_unknown_tool_and_timeout_are_observations(self):
        fake = FakeProvider([tool_call('1', 'delete_everything', '{}'), tool_call('2', 'get_status', '{"provider":"cloudflare"}'), final('Không đủ dữ liệu')])
        result = run_agent('x', fake, ScenarioTools('cloudflare_timeout'))
        self.assertEqual(result['status'], 'completed')
        errors = [t['result']['error'] for t in result['trace'] if t['event'] == 'tool']
        self.assertEqual(errors, ['UNKNOWN_TOOL', 'TimeoutError'])

    def test_provider_exception_returns_error(self):
        class Broken:
            def complete(self, messages, schemas):
                raise RuntimeError('Provider trả HTTP 500')
        result = run_agent('x', Broken(), StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertTrue(any(t['event'] == 'provider_error' for t in result['trace']))


if __name__ == '__main__':
    unittest.main()
