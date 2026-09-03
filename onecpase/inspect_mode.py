"""Dev-only 'Inspect' overlay.

When INSPECT_MODE=1, every Jinja {% block %} and {% include %} in the
templates gets invisible HTML-comment markers spliced in next to it, naming
the template file, block/include name, and source line. The Inspect toggle
in the page (see static/js/inspect.js) uses those comments to tell you which
template — and which Python view function — rendered whatever you click on.

Completely inert unless INSPECT_MODE is set: enable_inspect_mode() is the
only entry point, and __init__.py only calls it behind that config flag.
"""
import inspect
import os
import re

from flask import Flask, current_app, request
from flask.templating import Environment as FlaskJinjaEnvironment

_TOKEN_RE = re.compile(
    r"(?P<block_open>\{%-?\s*block\s+(?P<block_name>\w+)\s*-?%\})"
    r"|(?P<block_close>\{%-?\s*endblock\s*\w*\s*-?%\})"
    r"|(?P<include>\{%-?\s*include\s+(?P<q>['\"])(?P<target>(?:(?!(?P=q)).)+)(?P=q)[^%]*-?%\})"
)

# "title" renders inside <title>...</title>, a raw-text element — an HTML
# comment spliced in there would show up as literal text in the tab title.
_SKIP_BLOCKS = {"title"}


def _line_of(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _tag_source(source: str, template_name: str) -> str:
    """Splice `<!--@inspect:...-->` markers around each block/include.

    These are plain literal template text (not Jinja expressions), so they
    need no escaping and can never change what a template renders — only
    what invisible comments end up sitting next to the output. A single
    left-to-right scan keeps a stack of open blocks so a skipped block's
    {% endblock %} is recognized as its own close, even when blocks nest.
    """
    out = []
    pos = 0
    skip_stack = []
    for m in _TOKEN_RE.finditer(source):
        out.append(source[pos:m.start()])
        pos = m.end()
        if m.group("block_open"):
            name = m.group("block_name")
            skip = name in _SKIP_BLOCKS
            skip_stack.append(skip)
            out.append(m.group(0))
            if not skip:
                label = f"{template_name} #{name} : L{_line_of(source, m.start())}"
                out.append(f"<!--@inspect:BEGIN {label}-->")
        elif m.group("block_close"):
            skip = skip_stack.pop() if skip_stack else False
            if not skip:
                out.append("<!--@inspect:END-->")
            out.append(m.group(0))
        elif m.group("include"):
            target = m.group("target")
            label = f"{target} (included from {template_name}:{_line_of(source, m.start())})"
            out.append(f"<!--@inspect:BEGIN {label}-->")
            out.append(m.group(0))
            out.append("<!--@inspect:END-->")
    out.append(source[pos:])
    return "".join(out)


class _InspectEnvironment(FlaskJinjaEnvironment):
    def preprocess(self, source, name=None, filename=None):
        source = super().preprocess(source, name, filename)
        if name and name.endswith((".html", ".htm")):
            source = _tag_source(source, name)
        return source


def _inject_inspect_meta():
    if not current_app.config.get("INSPECT_MODE"):
        return {}

    endpoint = request.endpoint
    meta = {"endpoint": endpoint, "blueprint": request.blueprint}
    view = current_app.view_functions.get(endpoint) if endpoint else None
    if view is not None:
        target = inspect.unwrap(view)
        try:
            source_file = inspect.getsourcefile(target)
            _, line = inspect.getsourcelines(target)
            if source_file:
                meta["file"] = os.path.relpath(source_file, current_app.root_path)
                meta["line"] = line
                meta["name"] = getattr(target, "__name__", "")
        except (TypeError, OSError):
            pass
    return {"inspect_meta": meta}


def enable_inspect_mode(app: Flask) -> None:
    """Turn on template source-tagging and the view-function context
    processor. Only called when app.config['INSPECT_MODE'] is true."""
    app.jinja_env.__class__ = _InspectEnvironment
    app.context_processor(_inject_inspect_meta)
    app.logger.warning(
        "INSPECT_MODE is on — templates carry HTML-comment source markers "
        "and pages expose view-function locations. Do not run this against "
        "real member data long-term; it's a local dev aid."
    )
