"""Read current Itensity payment and last-visit reports, without changing records."""
import json
import sys
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.itensity_portal import BASE, credentials, login

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    login(page, *credentials())
    page.goto(BASE + '/Access_Report.php', wait_until='networkidle', timeout=60000)
    visits = page.evaluate("""async () => {
      const response = await fetch('XML/access_report.php', {method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body:new URLSearchParams({type:'1',date:'null',to:'null',from:'0',name:'',days:''})});
      return {status:response.status, html:await response.text()};
    }""")
    assert visits['status'] == 200
    soup = BeautifulSoup(visits['html'], 'html.parser')
    visit_rows = [[c.get_text(' ', strip=True) for c in tr.find_all(['th','td'])]
                  for tr in soup.select('tr')]
    assert len(visit_rows) > 1000, 'Incomplete last-visit report'
    print('Last-visit report rows:', len(visit_rows), flush=True)
    page.goto(BASE + '/transaction_report.php', wait_until='networkidle', timeout=60000)
    transactions = []
    windows = [('2026-01-01','2026-02-28'), ('2026-03-01','2026-04-30'),
               ('2026-05-01','2026-06-30'), ('2026-07-01','2026-08-31'),
               ('2026-09-01','2026-10-14')]
    for start,end in windows:
        rows = []
        expected = None
        while expected is None or len(rows) < expected:
            result = page.evaluate("""async args => {
              const params = new URLSearchParams({start:args.start,end:args.end,
                skip:String(args.skip),take:'500',filter:''});
              const response = await fetch('XML/transactions_report.php?'+params);
              if (!response.ok) throw new Error('Transaction report unavailable');
              return JSON.parse(await response.text());
            }""", {'start':start,'end':end,'skip':len(rows)})
            assert isinstance(result.get('items'), list)
            expected = int(result['totalCount'])
            batch = result['items']
            assert batch or len(rows) == expected, 'Transaction report truncated'
            rows.extend(batch)
        assert len(rows) == expected
        transactions.extend(rows)
        print(start, end, 'transactions:', len(rows), flush=True)
    browser.close()

output = {'retrieved_at':datetime.now().isoformat(), 'through_date':'2026-10-14',
          'visits':visit_rows, 'transactions':transactions, 'windows':windows}
(ROOT/'artifacts/current-cancellation-evidence-20261014.json').write_text(
    json.dumps(output, indent=2), encoding='utf-8')
print('Saved complete live report evidence.')
