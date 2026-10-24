import sys, unittest
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from study_protocol import evidence_lifecycle, publisher_key, deduplicate_evidence, metric_interpretation
from cycle_engine import build_report

class StudyProtocolTests(unittest.TestCase):
    def test_quarterly_retention_requires_recent_review(self):
        x={'published_at':'2026-08-01','frequency':'quarterly','max_age_days':120}
        self.assertEqual(evidence_lifecycle(x,date(2026,10,24))['status'],'review_due')
        x['last_reviewed_at']='2026-10-23'
        self.assertEqual(evidence_lifecycle(x,date(2026,10,24))['status'],'current')
        self.assertEqual(x['published_at'],'2026-08-01')
        x['next_expected_release_at']='2026-10-20'
        self.assertEqual(evidence_lifecycle(x,date(2026,10,24))['status'],'expired')
    def test_unknown_frequency_and_supersession(self):
        x={'published_at':'2026-08-01','last_reviewed_at':'2026-10-24','max_age_days':120}
        self.assertEqual(evidence_lifecycle(x,date(2026,10,24))['status'],'expired')
        x.update(published_at='2026-10-20',superseded_by='new')
        self.assertEqual(evidence_lifecycle(x,date(2026,10,24))['status'],'superseded')
    def test_issuer_routes_not_independent(self):
        a={'source_url':'https://nvidianews.nvidia.com/a','publisher_id':'news'}
        b={'source_url':'https://investor.nvidia.com/b','publisher_id':'ir'}
        self.assertEqual(publisher_key(a),publisher_key(b))
    def test_same_release_metric_deduplicates(self):
        a={'id':'a','source_url':'https://investor.nvidia.com/a','segment':'ai_compute_design','pillar':'earnings','metric':'revenue_yoy','origin_event_id':'nvda-q3'}
        b={**a,'id':'b','source_url':'https://nvidianews.nvidia.com/b','last_reviewed_at':'2026-10-24'}
        self.assertEqual([x['id'] for x in deduplicate_evidence([a,b])],['b'])
    def test_semantics(self):
        for metric in ('inventory_yoy','capex_yoy','unmapped_comparison'):
            self.assertEqual(metric_interpretation(metric,30,{}, {})[1],'neutral')
        self.assertEqual(metric_interpretation('capex_yoy',30,{'economic_role':'customer_investment'}, {})[:2],('demand','positive'))
        self.assertEqual(metric_interpretation('revenue_yoy',-22,{}, {})[:2],('earnings','negative'))
    def test_deterministic_check_is_not_independent_audit(self):
        facts=[]
        for i,(host,pillar) in enumerate((('nvidia.com','earnings'),('amd.com','demand'))):
            facts.append({'id':str(i),'segment':'ai_compute_design','pillar':pillar,'direction':'positive','fact':'Official fact','source_url':'https://'+host+'/a','published_at':'2026-10-23','source_family':host,'confidence':'direct','review_status':'verified','audit_passed':True,'audit_kind':'deterministic'})
        report=build_report(facts,datetime.fromisoformat('2026-10-24T12:00:00+09:00'))
        segment=next(x for x in report['segments'] if x['id']=='ai_compute_design')
        self.assertEqual(segment['status'],'neutral')
        self.assertEqual(segment['assessment_status'],'awaiting_independent_review')
        self.assertEqual(segment['coverage']['publisher_count'],2)
        self.assertTrue(segment['key_hypotheses'])

    def test_neutral_context_is_projected_without_changing_status(self):
        fact={'id':'neutral-capex','segment':'ai_compute_design','pillar':'supply','direction':'neutral','fact':'Official capacity plan, interpretation pending','source_url':'https://investor.nvidia.com/a','published_at':'2026-10-23','source_family':'event_nvda_ir','confidence':'direct','review_status':'verified','audit_passed':True,'audit_kind':'independent'}
        report=build_report([fact],datetime.fromisoformat('2026-10-24T12:00:00+09:00'),companies=[{'ticker':'NVDA','exposures':[{'segment':'ai_compute_design','weight':1.0}]}])
        segment=next(x for x in report['segments'] if x['id']=='ai_compute_design')
        self.assertEqual(segment['status'],'neutral')
        self.assertEqual(segment['supporting_evidence_ids'],[])
        self.assertEqual(segment['contrary_evidence_ids'],[])
        self.assertEqual(segment['context_evidence_ids'],['neutral-capex'])
        self.assertEqual(report['company_cycle'][0]['context_evidence_ids'],['neutral-capex'])
        self.assertEqual(report['company_cycle'][0]['status'],'neutral')
