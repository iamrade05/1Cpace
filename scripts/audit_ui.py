"""Measure template styling debt and optionally lower its regression baseline."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'onecpase/templates'
BASELINE = ROOT / 'tests/template_ui_baseline.json'
PATTERNS = {
    'inline_styles': r'\bstyle\s*=',
    'style_blocks': r'<style\b',
    'hex_colours': r'#[0-9a-fA-F]{3,8}\b',
    'emoji': '[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]',
}

def audit():
    return {
        p.relative_to(TEMPLATES).as_posix(): {
            key: len(re.findall(pattern, p.read_text(encoding='utf-8')))
            for key, pattern in PATTERNS.items()
        }
        for p in sorted(TEMPLATES.rglob('*.html'))
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ratchet-baseline', action='store_true', help='Lower existing limits; never permit an increase.')
    args = parser.parse_args()
    current = audit()
    if args.ratchet_baseline:
        old = json.loads(BASELINE.read_text(encoding='utf-8'))
        for name, metrics in current.items():
            for key, value in metrics.items():
                if value > old.get(name, {}).get(key, 0):
                    raise SystemExit(f'Refusing to increase {name}: {key}')
        BASELINE.write_text(json.dumps(current, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'templates': len(current), 'totals': {key: sum(row[key] for row in current.values()) for key in PATTERNS}}, indent=2))

if __name__ == '__main__':
    main()
