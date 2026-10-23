"""Staff training resources; never served from the public static directory."""
import json
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, abort, render_template, request, send_file

from .auth import login_required

training_bp = Blueprint('training', __name__, url_prefix='/training')
RESOURCE_DIR = Path(__file__).parent / 'resources' / 'training'


@lru_cache(maxsize=1)
def chapters():
    return json.loads((RESOURCE_DIR / 'chapters.json').read_text(encoding='utf-8'))


@training_bp.get('')
@login_required
def index():
    return render_template('training/index.html', chapters=chapters())


@training_bp.get('/topics/<slug>')
@login_required
def topic(slug):
    chapter = next((c for c in chapters() if c['slug'] == slug), None)
    if chapter is None:
        abort(404)
    return render_template('training/topic.html', chapter=chapter, chapters=chapters())


@training_bp.get('/manual/<format>')
@login_required
def manual(format):
    if format not in {'pdf', 'docx'}:
        abort(404)
    response = send_file(
        RESOURCE_DIR / f'manual.{format}',
        as_attachment=format == 'docx' or request.args.get('download') == '1',
        download_name=f'1Cpace_Staff_Training_Manual.{format}',
    )
    response.headers['Cache-Control'] = 'private, no-store'
    return response
