import sys, unittest
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from publication_health import validate_artifacts, identity_errors
from build_macro_policy_signal import _finish,unavailable_context,policy_changed,official_policy_context
NOW=datetime.fromisoformat('2026-10-24T12:00:00+09:00')
class PublicationHealthTests(unittest.TestCase):
    def test_new_build_cannot_hide_old_market_source(self):
        q={'generated_at':NOW.isoformat(),'expires_at':'2026-10-30T12:00:00+09:00','source_collection_finished_at':'2026-10-01T12:00:00+09:00','source_collection_status':'partial'}
        self.assertEqual(len(validate_artifacts({'quant-latest.json':q},NOW,selected_names=['quant-latest.json'])),2)
    def test_empty_expired_cycle_fails(self):
        q={'generated_at':NOW.isoformat(),'expires_at':'2026-10-20T12:00:00+09:00','quality_gate':{'status':'passed'},'evidence':[]}
        self.assertEqual(len(validate_artifacts({'cycle-latest.json':q},NOW,selected_names=['cycle-latest.json'])),2)
    def test_public_identity(self):
        for name in ('macro-policy-latest.json','cycle-latest.json'):
            self.assertTrue(identity_errors({}, {},name))
        p={'content_sha256':'hash','generated_at':'time','expires_at':'expiry'}
        self.assertEqual(identity_errors(p,p,'macro-policy-latest.json'),[])
        self.assertTrue(identity_errors(p,{**p,'content_sha256':'other'},'macro-policy-latest.json'))
    def test_unavailable_policy_does_not_renew(self):
        a=_finish(unavailable_context(NOW));b=_finish(unavailable_context(datetime.fromisoformat('2026-10-25T12:00:00+09:00')))
        self.assertFalse(policy_changed(a,b))
        self.assertIsNone(b['sector']['probability_pct'])
        self.assertEqual(validate_artifacts({'macro-policy-latest.json':a},NOW,selected_names=['macro-policy-latest.json']),[])
    def test_official_decision_is_fact(self):
        r={'event_id':'macro-fomc-2026-10-20','status':'complete','review_status':'verified','source_published_at':'2026-10-20T14:00:00-04:00','source_urls':['https://www.federalreserve.gov/newsevents/a.htm'],'facts':[{'metric':'federal_funds_target_range_lower','value':3.5},{'metric':'federal_funds_target_range_upper','value':3.75},{'metric':'federal_funds_target_range_change','value':-0.25}]}
        p=official_policy_context({'results':[r]}, {},NOW)
        self.assertEqual(p['sector']['direction'],'cut');self.assertIsNone(p['sector']['probability_pct'])
        self.assertIsNone(official_policy_context({'results':[r]}, {},datetime.fromisoformat('2027-01-01T12:00:00+09:00')))
