"""Content outside a block is silently thrown away in a template that extends
another one. That is how every "Call via PBX" button stopped working: the
click handler was written after the closing {% endblock %}, so it never
reached the browser and the button did nothing - with no error anywhere."""
import pathlib
import json
import re

from jinja2 import Environment, nodes

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "onecpase" / "templates"


def test_ui_debt_cannot_increase():
    baseline = json.loads((pathlib.Path(__file__).parent / "template_ui_baseline.json").read_text(encoding="utf-8"))
    patterns = {
        "inline_styles": r"\bstyle\s*=",
        "style_blocks": r"<style\b",
        "hex_colours": r"#[0-9a-fA-F]{3,8}\b",
        "emoji": "[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]",
    }
    problems = []
    for path in TEMPLATES.rglob("*.html"):
        name = path.relative_to(TEMPLATES).as_posix()
        source = path.read_text(encoding="utf-8")
        for metric, pattern in patterns.items():
            actual = len(re.findall(pattern, source))
            allowed = baseline.get(name, {}).get(metric, 0)
            if actual > allowed:
                problems.append(f"{name}: {metric} {actual} > {allowed}")
    assert not problems, "UI debt increased: " + "; ".join(problems)


def _content_outside_blocks(path):
    tree = Environment().parse(path.read_text(encoding="utf-8"))
    if not any(isinstance(node, nodes.Extends) for node in tree.body):
        return []
    stray = []
    for node in tree.body:
        if isinstance(node, nodes.Output):
            text = "".join(part.data for part in node.nodes if isinstance(part, nodes.TemplateData)).strip()
            has_expression = any(not isinstance(part, nodes.TemplateData) for part in node.nodes)
            if text or has_expression:
                stray.append((node.lineno, (text or "{{ expression }}")[:60].replace("\n", " ")))
    return stray


def test_no_template_has_content_outside_its_blocks():
    problems = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        stray = _content_outside_blocks(path)
        if stray:
            problems[str(path.relative_to(TEMPLATES))] = stray
    assert not problems, (
        "These templates extend another template but have content after their last block, "
        "which is never rendered - move it inside a {% block %}: " + repr(problems)
    )
