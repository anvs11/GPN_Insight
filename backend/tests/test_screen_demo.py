import json
import time
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import Repository
from scripts.prepare_screen_demo import prepare_screen_demo


def test_screen_demo_40_sources_8_topics_strict_replay_retry_repeat_and_reopen(tmp_path):
    demo, info = prepare_screen_demo(tmp_path)
    settings = demo.settings.model_copy(update={'app_weekly_archive_enabled': False})
    with TestClient(create_app(settings), base_url='http://127.0.0.1') as client:
        assert client.get('/api/v1/monitoring?view=sources').json()['total'] == 40
        assert client.get('/api/v1/monitoring?view=topics').json()['total'] == 8
        assert client.get('/api/v1/monitoring?view=sources&category=unclassified').json()['total'] == 2
        assert client.get('/api/v1/monitoring?view=sources&priority=potentially_critical').json()['total'] == 5
        assert client.get('/api/v1/monitoring?view=sources&offset=24').json()['total'] == 40
        raw = (tmp_path / 'text-manifest.json').read_text(encoding='utf-8')
        repeated = client.post('/api/v1/imports', files={'manifest': (None, raw)})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()['counts']['unchanged'] == 39
        assert client.get('/api/v1/monitoring?view=sources').json()['total'] == 40
        for _ in range(2):
            started = client.post('/api/v1/runs', json={})
            assert started.status_code == 202, started.text
            run_id = started.json()['run_id']
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                run = client.get('/api/v1/runs/' + run_id).json()
                if run['state'] not in {'queued', 'running'}:
                    break
                time.sleep(.02)
            assert run['state'] == 'completed', run
            assert run['material_coverage']['successful'] == 44
            assert client.get('/api/v1/monitoring?view=topics').json()['total'] == 8
        card = client.get('/api/v1/monitoring?view=topics&priority=potentially_critical').json()['items'][0]
        before = client.get('/api/v1/topics/' + card['id']).json()
        body = dict(version=before['topic']['version'], status='needs_research', owner='Синтетический аналитик', reason='Проверить возможный доступ к чужому договору.')
        saved = client.patch('/api/v1/topics/' + card['id'] + '/decision', json=body)
        assert saved.status_code == 200, saved.text
        assert client.patch('/api/v1/topics/' + card['id'] + '/decision', json=body).status_code == 409
        snapshot = saved.json()
    reopened = Repository(demo.repo.path).get_topic(card['id'], 'replay')
    assert reopened.topic.version == snapshot['topic']['version']
    assert reopened.topic.owner == body['owner']
    assert len(reopened.history) == len(snapshot['history'])
    (tmp_path / 'http-check.json').write_text(json.dumps(dict(synthetic=True, origin='authored', live_model_calls=False,
        sources=40, topics=8, unclassified=2, potentially_critical=5, full_coverage='44/44',
        repeat_without_duplicates=True, stale_decision_409=True, decision_preserved_after_reopen=True)), encoding='utf-8')
