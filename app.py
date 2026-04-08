# Capella Fabric Generator — Flask application
# Routes: / → /inspect → /generate → /download/<session_id>

from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for, send_file, flash
)
import capella_service as svc

app = Flask(__name__)
app.secret_key = 'capella-fabric-generator-dev-secret'


# ---------------------------------------------------------------------------
# Page 1 — Upload form
# ---------------------------------------------------------------------------

@app.route('/', methods=['GET'])
def index():
    return render_template('upload.html')


# ---------------------------------------------------------------------------
# Page 2 — Inspect objects
# ---------------------------------------------------------------------------

@app.route('/inspect', methods=['POST'])
def inspect():
    file = request.files.get('archive')
    if not file or file.filename == '':
        flash('Please select a Capella archive (.zip) to upload.')
        return redirect(url_for('index'))

    if not file.filename.lower().endswith('.zip'):
        flash('Only .zip archives are accepted.')
        return redirect(url_for('index'))

    uuid_text = request.form.get('uuids', '').strip()
    if not uuid_text:
        flash('Please enter at least one UUID.')
        return redirect(url_for('index'))

    include_realized = 'include_realized' in request.form
    include_realizing = 'include_realizing' in request.form

    session_id = svc.create_session()
    try:
        svc.save_upload(file, session_id)
        svc.unpack_archive(session_id)

        aird_path = svc.find_aird_file(session_id)
        if aird_path is None:
            flash('No .aird file found inside the archive.')
            svc.cleanup_session(session_id)
            return redirect(url_for('index'))

        model = svc.open_model(aird_path)
        uuid_list = svc.parse_uuid_text(uuid_text)
        resolved, not_found = svc.resolve_uuids(model, uuid_list)

        session_data = {
            'session_id': session_id,
            'archive_name': file.filename,
            'aird_path': str(aird_path),
            'uuid_list': uuid_list,
            'resolved_uuids': [obj['uuid'] for obj in resolved],
            'not_found': not_found,
            'include_realized': include_realized,
            'include_realizing': include_realizing,
            'yaml_path': None,
        }
        svc.save_session(session_id, session_data)

        return render_template(
            'inspect.html',
            session_id=session_id,
            archive_name=file.filename,
            resolved=resolved,
            not_found=not_found,
        )

    except Exception as exc:
        svc.cleanup_session(session_id)
        flash(f'Error processing archive: {exc}')
        return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Page 3 — Generate fabric and show download
# ---------------------------------------------------------------------------

@app.route('/generate', methods=['POST'])
def generate():
    session_id = request.form.get('session_id', '').strip()
    if not session_id:
        flash('Missing session — please start over.')
        return redirect(url_for('index'))

    try:
        session = svc.load_session(session_id)
        yaml_path, object_count = svc.generate_fabric(session)

        session['yaml_path'] = str(yaml_path)
        session['object_count'] = object_count
        svc.save_session(session_id, session)

        # First ~40 lines for the preview pane
        with open(yaml_path, encoding='utf-8') as f:
            preview_lines = [next(f, '') for _ in range(40)]
        preview = ''.join(preview_lines)

        return render_template(
            'ready.html',
            session_id=session_id,
            archive_name=session['archive_name'],
            yaml_name=Path(yaml_path).name,
            root_count=len(session['resolved_uuids']),
            object_count=object_count,
            preview=preview,
        )

    except Exception as exc:
        flash(f'Error generating fabric: {exc}')
        return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Download generated YAML
# ---------------------------------------------------------------------------

@app.route('/download/<session_id>', methods=['GET'])
def download(session_id):
    try:
        session = svc.load_session(session_id)
        yaml_path = session.get('yaml_path')
        if not yaml_path or not Path(yaml_path).exists():
            flash('File not available — please generate again.')
            return redirect(url_for('index'))
        return send_file(yaml_path, as_attachment=True)
    except Exception as exc:
        flash(f'Download error: {exc}')
        return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Start over (clean up temp files)
# ---------------------------------------------------------------------------

@app.route('/start-over', methods=['GET'])
def start_over():
    session_id = request.args.get('session_id', '').strip()
    if session_id:
        svc.cleanup_session(session_id)
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True)
