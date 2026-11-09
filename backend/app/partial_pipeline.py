"""Первый проход partial профиля; назначение тем остаётся общим Pipeline У2/У3."""

from .errors import RepositoryError
from .gateways import GatewayUnavailable
from .processing_schemas import NormalizationResult, analysis_input
from .replay import semantic_hash, stable_id
from .schemas import ExtractionResult, MaterialError


def process_source(pipeline, source, run_id, check_metadata):
    repo, adapters = pipeline.repo, pipeline.adapters
    raw = adapters.normalizer.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=source.source_version)
    result = NormalizationResult.model_validate(raw.model_dump())
    expected = adapters.profile.parameters["partial_media"]["media_profile_hash"]
    if result.profile_hash != expected or result.document.normalization_version != adapters.profile.normalization_version:
        raise RepositoryError("NORMALIZATION_PROFILE_CONFLICT", "Результат не соответствует настроенному partial профилю.", 422)
    previous = repo.processing_result(source.ticket.source_id, source.ticket.product_id, source.source_version)
    with repo.transaction() as tx:
        pipeline._ensure_running(tx, run_id)
        tx.publish_normalization(result)
    steps, reused = (0, 1) if previous == result else (1, 0)
    view = analysis_input(result)
    errors = []
    extraction = None

    def failure(stage, exc):
        from .pipeline import RunStopped
        if isinstance(exc, RunStopped):
            raise exc
        errors.append(MaterialError(source_id=source.ticket.source_id, stage=stage,
            code=exc.code if isinstance(exc, (GatewayUnavailable, RepositoryError)) else "PIPELINE_STAGE_FAILED",
            message="Этап не завершён. Успешные части сохранены; проверьте код ошибки и повторите."))

    # Empty является успешной нормализацией, failed не считается пустым извлечением.
    if view is None:
        if result.state == "empty":
            with repo.transaction() as tx:
                pipeline._ensure_running(tx, run_id)
                tx.publish_processing_needs(result, [])
        return result, None, steps, reused, errors
    key = semantic_hash({"policy": "partial-extraction-v1", "digest": view.analysis_digest,
                         "profile": adapters.profile.model_dump(mode="json")})
    try:
        saved = repo.checkpoint(key)
        if saved:
            extraction = ExtractionResult.model_validate(saved)
            reused += 1
        else:
            extraction = ExtractionResult.model_validate(adapters.analysis.extract_needs(view.document).model_dump())
            needs = {}
            for need in extraction.needs:
                identity = need.model_dump(mode="json", exclude={"need_id"})
                nid = stable_id("partial-need-v1", source.ticket.product_id, source.source_version,
                                source.content_hash, identity)
                needs[nid] = need.model_copy(update={"need_id": nid})
            extraction = extraction.model_copy(update={"needs": [needs[n] for n in sorted(needs)]})
            steps += 1
        check_metadata(extraction.metadata, "extract_needs")
        with repo.transaction() as tx:
            pipeline._ensure_running(tx, run_id)
            tx.publish_processing_needs(result, extraction.needs)
            tx.save_checkpoint(key, "partial-extraction", run_id, extraction.model_dump(mode="json"))
    except Exception as exc:
        failure("extraction", exc)
        return result, None, steps, reused, errors
    for need in extraction.needs:
        try:
            assessment = repo.assessment(need.need_id, view.analysis_digest)
            if assessment is None:
                if adapters.assessor is None:
                    raise GatewayUnavailable("ASSESSMENT_NOT_CONFIGURED", "Оценки категории/приоритета не подключены.")
                assessment = adapters.assessor.assess(need, view)
                steps += 1
            else:
                reused += 1
            check_metadata([assessment.metadata], "classify_need")
            with repo.transaction() as tx:
                pipeline._ensure_running(tx, run_id)
                tx.save_assessment(assessment, result)
        except Exception as exc:
            failure("classification", exc)
    return result, extraction, steps, reused, errors
