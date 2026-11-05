"""Только синтетические тестовые компоненты. Production их не импортирует."""

from threading import Event

from app.pipeline import PipelineAdapters
from app.replay import semantic_hash
from app.schemas import (
    AssignmentResult, EvidenceRef, ExtractedNeed, ExtractionResult, Fragment,
    NormalizedDocument, PassportExample, ProcessingProfile, RecordingMeta, Topic, TopicAssignment, TopicPassport,
)


class TestComponents:
    __test__ = False

    def __init__(self, *, target=None, decision="new"):
        self.events = []
        self.target = target
        self.decision = decision
        self.entered = Event()
        self.release = Event()
        self.block = None
        self.fail = None
        self.profile = ProcessingProfile(model_id="synthetic-test-only", normalization_version="test-1",
            prompt_hashes={"extract_needs": "a" * 64, "assign_topic": "a" * 64}, recording_set_version="test-1")

    def adapters(self):
        return PipelineAdapters(self, self, self.profile)

    def event(self, stage, source):
        self.events.append((stage, source))
        if self.block == stage:
            self.entered.set()
            if not self.release.wait(10):
                raise RuntimeError("Test release timeout")
        if self.fail == stage:
            raise RuntimeError("SENSITIVE_TEST_SENTINEL")

    def normalize_ticket(self, ticket, *, content_hash, source_version):
        self.event("normalize", ticket.source_id)
        return NormalizedDocument(product_id=ticket.product_id, source_id=ticket.source_id, content_hash=content_hash,
            version=source_version, normalization_version=self.profile.normalization_version,
            fragments=[Fragment(fragment_id="test-input-fragment", kind="text", text=ticket.text)])

    def meta(self, value):
        return RecordingMeta(request_hash=semantic_hash(value), prompt_hash="a" * 64, normalization_hash="b" * 64,
            model_id=self.profile.model_id, origin="authored", recording_set_version=self.profile.recording_set_version)

    def extract_needs(self, document):
        self.event("extract", document.source_id)
        fragment = document.fragments[0]
        need = ExtractedNeed(need_id="test-input-need", source_id=document.source_id, fragment_id=fragment.fragment_id,
            evidence_quote=fragment.text, scenario="Синтетический сценарий", need_type="feature_request", statement="Синтетическая потребность", explicit_request=True)
        return ExtractionResult(needs=[need], metadata=[self.meta(document.model_dump(mode="json"))])

    def assign_topic(self, need, topic_passports):
        self.event("assign", need.source_id)
        ref = EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)
        decision = "match" if self.target else self.decision
        assignment = TopicAssignment(need_id=need.need_id, decision=decision, matched_topic_id=self.target,
            candidate_topic_ids=[self.target] if self.target else [], reason="Синтетическое тестовое назначение", evidence_refs=[ref],
            passport_versions={t.topic_id: t.passport.version for t in topic_passports.topics}, catalog_revision=topic_passports.catalog_revision,
            total_topic_count=len(topic_passports.topics), searched_topic_count=len(topic_passports.topics), candidate_set_complete=True,
            search_truncated=False, mode="replay")
        topic = None
        if decision == "new":
            topic = Topic(topic_id="server-will-assign-id", passport=TopicPassport(scenario=need.scenario, need_statement=need.statement,
                scope="Только синтетический тест", inclusion_examples=[PassportExample(text=need.evidence_quote, evidence_refs=[ref])], version=1),
                title="Синтетическая тестовая тема", summary="Не результат модели", need_ids=[need.need_id], evidence=[ref])
        return AssignmentResult(assignment=assignment, new_topic=topic, metadata=[self.meta(need.model_dump(mode="json"))])
