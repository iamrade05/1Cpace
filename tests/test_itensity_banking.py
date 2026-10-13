from types import SimpleNamespace

from scripts.itensity_banking import enrich_unverified_banking, parse_banking_page


def banking_html(number="00123456789", kind="Savings"):
    headers = ['Gym Name', 'Acc Name', 'ID / Passport No.', 'Cell', 'Acc Number',
               'Acc Branch', 'Bank Name', 'Acc TYPE', 'Debit Date', 'Type']
    values = ['Example Gym', 'Other Payer', '9001015009087', '0720000000', number,
              '051001', 'Standard Bank', kind, '1', 'Debit Order']
    return '<div id="bankDetails"><table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr><tr>'+''.join('<td>'+v+'</td>' for v in values)+'</tr></table></div>'


def test_profile_banking_preserves_leading_zeroes_and_holder():
    result = parse_banking_page(banking_html(), '123')
    assert result['id'] == '123'
    assert result['accno'] == '00123456789'
    assert result['branch'] == '051001'
    assert result['accname'] == 'Other Payer'
    assert result['account_type'] == 'Savings'


def test_masked_account_and_unknown_type_are_not_usable():
    result = parse_banking_page(banking_html('001****789', 'Unknown'), '123')
    assert result['accno'] == ''
    assert result['account_type'] == ''


def test_enrichment_targets_unverified_members_and_preserves_on_failure():
    calls = []
    def get(url, timeout):
        calls.append(url)
        if 'id=2' in url:
            raise TimeoutError()
        return SimpleNamespace(ok=True, text=lambda: banking_html())
    page = SimpleNamespace(request=SimpleNamespace(get=get))
    rows = [{'userid': '1', 'status': 'Unverified', 'role': 'Member'},
            {'userid': '2', 'status': 'Unverified', 'role': 'Member'},
            {'userid': '3', 'status': 'Active', 'role': 'Member'},
            {'userid': '4', 'status': 'Unverified', 'role': 'Sales'}]
    banking = {'2': {'accno': '987654321'}}
    stats = enrich_unverified_banking(page, 'https://example.com', rows, banking)
    assert len(calls) == 2
    assert banking['1']['accno'] == '00123456789'
    assert banking['2']['accno'] == '987654321'
    assert stats == {'attempted': 2, 'retrieved': 1, 'missing': 0, 'failed': 1,
                     'needs_review': 0, 'review_refs': []}
    assert rows[0]['status'] == 'Unverified'


def test_numeric_source_account_types_map_to_debicheck():
    for raw, expected in [('1', 'Cheque/Current'), ('2', 'Savings'), ('3', 'Transmission')]:
        assert parse_banking_page(banking_html(kind=raw), '123')['account_type'] == expected


def test_multiple_source_accounts_are_reported_without_overwriting():
    html = banking_html()
    soup_row = html[html.index('<tr><td>'):html.index('</tr></table>')+5]
    html = html.replace('</table>', soup_row+'</table>')
    page = SimpleNamespace(request=SimpleNamespace(get=lambda *a, **k:
                           SimpleNamespace(ok=True, text=lambda: html)))
    banking = {'123': {'accno': '987654321'}}
    stats = enrich_unverified_banking(page, 'https://example.com',
             [{'userid': '123', 'status': 'Unverified', 'role': 'Member'}], banking)
    assert stats['needs_review'] == 1
    assert stats['review_refs'] == ['123']
    assert banking['123']['accno'] == '987654321'
