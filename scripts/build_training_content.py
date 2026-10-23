"""Compile the reviewed training text into escaped, structured reader content.

Run after revising the manual: python -m scripts.build_training_content
The PDF and Word resources must be updated from the same reviewed edition.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPICS = [
    ('sales', 'Sales', 34, 'Capture prospects, record activities and decisions, check evidence and hand over applications.'),
    ('collections', 'Collections', 38, 'Verify balances, work collection calls, record payments and escalate unresolved cases.'),
    ('ptp', 'Promise to Pay (PTP)', 41, 'Prepare arrangements, understand approvals and instalments, and follow up missed promises.'),
    ('debicheck', 'DebiCheck and NuPay', 44, 'Capture mandates, interpret provider status and handle failed debits or banking changes.'),
    ('queries', 'Queries', 46, 'Assign cases, investigate issues, request approvals, escalate and close with evidence.'),
    ('notifications', 'Notifications and handover', 49, 'Work alerts with your own supervisor, acknowledge reminders and hand over unfinished work.'),
    ('practice', 'Practice and daily checklist', 51, 'Use five practice cases and a daily checklist to assess readiness.'),
]


def build():
    chapters = []
    for line in (ROOT / 'artifacts/training-manual/expanded-content.md').read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('# '):
            slug, title, page, summary = TOPICS[len(chapters)]
            chapters.append(dict(slug=slug, title=title, page=page, summary=summary, blocks=[]))
            continue
        blocks = chapters[-1]['blocks']
        if line.startswith('## '):
            blocks.append(dict(type='heading', text=line[3:]))
        elif line.startswith('|'):
            if not blocks or blocks[-1]['type'] != 'table':
                blocks.append(dict(type='table', rows=[]))
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                blocks[-1]['rows'].append(cells)
        elif re.match(r'^\d+\. ', line) or line.startswith(('- ', '• ')):
            kind = 'ordered' if re.match(r'^\d+\. ', line) else 'unordered'
            if not blocks or blocks[-1]['type'] != kind:
                blocks.append(dict(type=kind, items=[]))
            blocks[-1]['items'].append(re.sub(r'^(?:\d+\. |[-•] )', '', line))
        else:
            blocks.append(dict(type='paragraph', text=line))
    assert len(chapters) == len(TOPICS), 'Manual chapter structure changed; update TOPICS.'
    for chapter in chapters:
        chapter['search'] = json.dumps(chapter, ensure_ascii=False).lower()
    destination = ROOT / 'onecpase/resources/training/chapters.json'
    destination.write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    build()
