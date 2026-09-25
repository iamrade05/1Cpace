"""Content outside a block is silently thrown away in a template that extends
another one. That is how every "Call via PBX" button stopped working: the
click handler was written after the closing {% endblock %}, so it never
reached the browser and the button did nothing - with no error anywhere."""
import pathlib

from jinja2 import Environment, nodes

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "onecpase" / "templates"


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
