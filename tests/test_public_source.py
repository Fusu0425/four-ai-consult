from tools.public_source import inspect_files, scan_bytes, selected_files


def test_scanner_reports_location_not_secret_value():
    secret = 'sk-' + 'aB92' * 12
    results = scan_bytes(('safe\n' + secret).encode())
    assert (2, 'provider-key') in results
    assert secret not in str(results)


def test_public_selection_excludes_runtime_and_private_marketing(tmp_path):
    for name in ('main.py', '.env', 'marketing/private.md', 'docs/LATE_ANSWER_075.md',
                 'four_ai_consult/app.py', 'four_ai_consult/__pycache__/app.pyc'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('test', encoding='utf-8')
    assert {p.relative_to(tmp_path).as_posix() for p in selected_files(tmp_path)} == {
        'main.py', 'four_ai_consult/app.py',
    }


def test_private_database_inside_source_is_rejected(tmp_path):
    path = tmp_path / 'private.db'
    path.write_bytes(b'not a real database')
    assert inspect_files(tmp_path, [path])[0]['rule'] == 'private-or-generated-file'
