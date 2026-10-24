import sys,unittest
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from discover_macro_calendar import parse_official_calendar
class MacroCalendarTests(unittest.TestCase):
    def monitor(self,name='macro-us-cpi'):
        return {'id':name,'name':'Official release','calendar_url':'https://www.bls.gov/schedule/'}
    def test_reference_and_dst(self):
        for today,release,expected in [('2026-10-01','October 13, 2026','21:30:00'),('2026-11-01','November 10, 2026','22:30:00')]:
            html='<p>All times Eastern Time</p><table><tr><td>'+release+'</td><td>08:30 AM</td><td>September 2026</td></tr></table>'
            e=parse_official_calendar(html,self.monitor(),datetime.fromisoformat(today+'T12:00:00+09:00'))
            self.assertIn(expected,e[0]['scheduled_at']);self.assertEqual(e[0]['reference_period'],'2026-09')
    def test_missing_reference_and_zone(self):
        now=datetime.fromisoformat('2026-11-01T12:00:00+09:00')
        self.assertEqual(parse_official_calendar('<tr>November 10, 2026 08:30 AM</tr>',self.monitor(),now),[])
        e=parse_official_calendar('<tr>November 10, 2026 08:30 AM October 2026</tr>',self.monitor(),now)
        self.assertIsNone(e[0]['scheduled_at'])
    def test_fomc_date_only_and_unsupported(self):
        now=datetime.fromisoformat('2026-10-24T12:00:00+09:00')
        e=parse_official_calendar('<p>2026 FOMC Meetings October 27–28 November 20 2027 FOMC Meetings January 26–27</p>',self.monitor('macro-fomc'),now)
        self.assertEqual(e[0]['scheduled_date'],'2026-10-28');self.assertIsNone(e[0]['scheduled_at'])
        self.assertEqual(parse_official_calendar('<p>November 10, 2026</p>',self.monitor('macro-kr-cpi'),now),[])

    def test_fomc_next_year_in_forty_five_day_window(self):
        now=datetime.fromisoformat('2026-12-20T12:00:00+09:00')
        e=parse_official_calendar('<p>2026 FOMC Meetings December 8–9 2027 FOMC Meetings January 26–27</p>',self.monitor('macro-fomc'),now)
        self.assertEqual(e[0]['scheduled_date'],'2027-01-27')
