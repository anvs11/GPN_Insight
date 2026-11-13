"""40 synthetic sources / 8 topics. Explicit authored recordings, never live."""

from dataclasses import replace
import json
from uuid import uuid4

from app.config import REPO_ROOT
from app.bootstrap import configured_text_pipeline
from app.gateways import GatewayResult, text_gateway
from app.ingestion.importer import import_tickets
from app.partial_integration import build_partial_pipeline, build_partial_profile
from app.replay import make_record, publish_records
from app.schemas import TicketInput, ExtractedNeed, ExtractedNeeds, BatchAssignment, ProposedTopic, PassportExample
from app.assessment_schemas import NeedAssessment
from scripts.partial_pipeline_fixture import AuthoredPartialGateway, damaged_png, run_sync
from scripts.partial_media_fixture import prepare_media_demo
from scripts.prepare_acceptance_demo import save_contact_decision
from scripts.media_import_fixture import synthetic_files


GROUPS = [
    (6, 'Сохранение счёта', 'Сохранять введённые данные счёта после ошибки', 'suspected_defect', 'elevated',
     'При сохранении счёта возникает ошибка и пропадают введённые данные.', 'Описана потеря ввода и необходимость повторной работы; масштаб не установлен.'),
    (6, 'Экспорт заявок', 'Выгружать список заявок в CSV', 'improvement_request', 'normal',
     'Прошу добавить выгрузку списка заявок в CSV для сверки.', 'Описано улучшение регулярной сверки без признаков серьёзного влияния.'),
    (5, 'Восстановление доступа', 'Получать код восстановления доступа', 'suspected_defect', 'elevated',
     'Код восстановления пароля не приходит, войти в личный кабинет не получается.', 'Описано затруднение доступа; длительность и число затронутых пользователей неизвестны.'),
    (5, 'Электронная очередь', 'Выбирать доступное время электронной очереди', 'suspected_defect', 'elevated',
     'При выборе электронной очереди сайт сообщает, что выбранного времени не существует.', 'Ошибка мешает бронированию; массовость и влияние на отгрузки не подтверждены.'),
    (5, 'Оформление договора', 'Продолжать оформление договора после заполнения реквизитов', 'suspected_defect', 'elevated',
     'Реквизиты заполнены, но кнопка продолжения оформления договора не реагирует.', 'Описана остановка оформления; неизвестны обходной путь и масштаб.'),
    (5, 'Доступ к чужим документам', 'Исключить доступ к документам другой организации', 'suspected_defect', 'potentially_critical',
     'В личном кабинете после открытия документа вижу договор другой организации вместо своего.', 'Описан возможный доступ к чужим документам. Это потенциальный риск разграничения доступа, а не подтверждённая утечка.'),
    (5, 'Поиск фильтра заявок', 'Понимать расположение фильтра статуса заявок', 'usability_confusion', 'normal',
     'Не понимаю, где в списке заявок включить фильтр по статусу. Нужна понятная подсказка.', 'Описана трудность поиска элемента интерфейса без признаков повышенной срочности.'),
]


def cases():
    result = {}
    contexts = ['При повторном входе ситуация сохраняется.', 'Замечено при просмотре заявки.',
        'Проверено после обновления страницы.', 'Ситуация мешает привычной работе.',
        'Наблюдение передано в поддержку.', 'Повторилось в следующей рабочей сессии.']
    for count, scenario, statement, category, priority, text, reason in GROUPS:
        for i in range(count):
            sid = f'SCREEN-T{len(result) + 1:03}'
            result[sid] = (text + ' ' + contexts[i], scenario, statement, category, priority, reason)
    for text in ['После изменения стало иначе, но не могу назвать экран и действие.',
                 'Прошу вернуть прежний вариант. Не помню, в какой форме это было.']:
        result[f'SCREEN-T{len(result) + 1:03}'] = (text, 'Неуточнённый сценарий', 'Уточнить ожидаемое действие неизвестного экрана',
            'unclassified', 'unknown', 'Не указаны экран и ожидаемое действие.')
    assert len(result) == 39
    return result


class ScreenAuthoredGateway(AuthoredPartialGateway):
    """Offline authoring fixture only. Production uses strict ReplayGateway."""
    def generate(self, request, response_schema):
        sid = request.data.get('document', {}).get('source_id') if request.operation == 'extract_needs' else request.data['need']['source_id']
        known = cases()
        if sid not in known:
            return super().generate(request, response_schema)
        text, scenario, statement, category, priority, reason = known[sid]
        if request.operation == 'extract_needs':
            fragment = request.data['document']['fragments'][0]
            if fragment['text'] != text:
                raise ValueError('Unknown synthetic input')
            value = ExtractedNeeds(needs=[ExtractedNeed(need_id='pending', source_id=sid, fragment_id=fragment['fragment_id'],
                evidence_quote=text, scenario=scenario, statement=statement, need_type='problem' if category == 'suspected_defect' else 'question' if category == 'usability_confusion' else 'feature_request',
                explicit_request=text.startswith('Прошу'))])
        else:
            need = request.data['need']
            ref = dict(source_id=sid, fragment_id=need['fragment_id'], quote=need['evidence_quote'])
            if request.operation == 'classify_need':
                value = NeedAssessment(category=category, priority=priority, reason=reason,
                    uncertainties=['Масштаб, причина и фактический ущерб не установлены.'] if priority != 'normal' else [], evidence_refs=[ref])
            elif request.operation == 'assign_topic':
                ids = [t['topic_id'] for t in request.data['topics'] if t['passport']['need_statement'] == statement]
                decision = 'review' if category == 'unclassified' else 'match' if ids else 'provisional_no_match'
                value = BatchAssignment(decision=decision, matched_topic_id=ids[0] if decision == 'match' else None,
                    candidate_topic_ids=ids, reason='Синтетическое авторское сопоставление для демонстрации программы.', evidence_refs=[ref])
            elif request.operation == 'propose_topic' and category != 'unclassified':
                value = ProposedTopic(title=scenario, summary=statement, scenario=scenario, need_statement=statement,
                    scope='Только указанное действие и проблема в синтетическом наборе.',
                    inclusion_examples=[PassportExample(text=need['evidence_quote'], evidence_refs=[ref])])
            else:
                raise ValueError('Unsupported synthetic operation')
        record = make_record(request, response_schema, value.model_dump(mode='json'), model_id=self.profile.model_id,
            normalization_hash=self.profile.parameters['normalization_hash'], set_version=self.profile.recording_set_version, origin='authored')
        self.records.append(record)
        return GatewayResult(value=value, metadata=record.metadata)


def register(demo):
    tickets = [TicketInput(source_id=sid, created_at=f'2026-11-0{3 + i % 5}T{9 + i % 9:02}:00:00+03:00',
        requester_id=f'SYNTHETIC-REQUESTER-{i % 16 + 1:02}', text=case[0]) for i, (sid, case) in enumerate(cases().items())]
    manifest = dict(dataset_id='synthetic-screen-40-v1', synthetic=True, tickets=[t.model_dump(mode='json') for t in tickets])
    assert import_tickets(json.dumps(manifest).encode(), [], repository=demo.repo).counts.inserted == 39
    return manifest


def prepare_screen_demo(folder):
    demo = prepare_media_demo(folder)
    manifest = register(demo)
    training = prepare_media_demo(folder / 'recording-build')
    register(training)
    profile = build_partial_profile(demo.settings, media_profile=demo.profile, model_id='synthetic-screen-40-no-live-model', recording_set_version='synthetic-screen-40-v1')
    gateway = ScreenAuthoredGateway(profile)
    adapters = build_partial_pipeline(gateway, profile, training.settings.model_copy(update={
        'app_media_recordings_path': training.recordings, 'app_media_cache_root': training.folder / 'cache'}), training.repo)
    path = damaged_png(training); raw = path.read_bytes()
    try:
        path.write_bytes(b'SYNTHETIC-CORRUPT')
        first = run_sync(training.repo, adapters, 'authored-screen-partial')
        assert first.state == 'completed_with_errors', first.errors
        save_contact_decision(training)
    finally:
        path.write_bytes(raw)
    assert run_sync(training.repo, adapters, 'authored-screen-full').state == 'completed'
    root = folder / 'text-recordings'; publish_records(root, gateway.records)
    profile_path = folder / 'processing-profile.json'; profile_path.write_text(profile.model_dump_json(indent=2), encoding='utf-8')
    settings = demo.settings.model_copy(update={'app_processing_profile_path': profile_path, 'app_replay_model_id': profile.model_id,
        'app_replay_normalization_hash': profile.parameters['normalization_hash'], 'app_recordings_root': root,
        'app_media_recordings_path': demo.recordings, 'app_media_cache_root': folder / 'cache'})
    demo = replace(demo, settings=settings)
    replay, error = configured_text_pipeline(settings, text_gateway(settings)); assert replay and not error
    path = damaged_png(demo); raw = path.read_bytes()
    try:
        path.write_bytes(b'SYNTHETIC-CORRUPT')
        first = run_sync(demo.repo, replay, 'screen-partial')
        assert first.state == 'completed_with_errors' and first.material_coverage.successful == 43, first.errors
        save_contact_decision(demo)
    finally:
        path.write_bytes(raw)
    assert len(demo.repo.list_topics('replay', support_level='all')) == 8
    selected = folder / 'selected-files'; selected.mkdir()
    for name, content in synthetic_files().items(): (selected / name).write_bytes(content)
    (selected / 'manifest.json').write_text(json.dumps(dict(dataset_id='synthetic-partial-media-v1', synthetic=True,
        tickets=[demo.ticket.model_dump(mode='json')]), ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'text-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    env = dict(APP_DB_PATH=str(demo.repo.path), APP_UPLOAD_ROOT=str(settings.app_upload_root), APP_TMP_ROOT=str(settings.app_tmp_root),
        APP_MODEL_MODE='replay', APP_PROCESSING_PROFILE_PATH=str(profile_path), APP_RECORDINGS_ROOT=str(root),
        APP_MEDIA_RECORDINGS_PATH=str(demo.recordings), APP_MEDIA_CACHE_ROOT=str(settings.app_media_cache_root),
        APP_REPLAY_MODEL_ID=profile.model_id, APP_REPLAY_NORMALIZATION_HASH=profile.parameters['normalization_hash'],
        TEXT_MODEL_BASE_URL='', TEXT_MODEL_ID='', ASR_BASE_URL='', ASR_MODEL_ID='', VL_BASE_URL='', VL_MODEL_ID='')
    result = dict(synthetic=True, origin='authored', live_model_calls=False, sources=40, topics=8, unclassified_sources=2,
        potentially_critical_sources=5, folder=str(folder), selected_files=str(selected), text_manifest=str(folder / 'text-manifest.json'),
        environment=env, coverage='43/44 before retry; 44/44 after retry')
    (folder / 'demo-settings.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return demo, result


def main():
    _, result = prepare_screen_demo(REPO_ROOT / 'var' / ('screen-demo-' + uuid4().hex))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
