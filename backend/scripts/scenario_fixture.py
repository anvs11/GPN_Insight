"""Явно authored fixture только для CLI record --authored-scenario, без моделей."""

from app.gateways import GatewayResult
from app.replay import make_record
from app.schemas import BatchAssignment, EvidenceRef, ExtractedNeed, ExtractedNeeds, PassportExample, ProposedTopic


class ScenarioAuthoredGateway:
    def __init__(self, spec, profile):
        self.spec, self.profile = spec, profile
        self.records = []

    def generate(self, request, response_schema):
        if request.operation == "extract_needs":
            document = request.data["document"]
            fragment = document["fragments"][0]
            definitions = self.spec.authored_needs[document["source_id"]]
            # Не создавать ответ на неизвестный изменившийся вход fixture.
            ticket = next(t for t in [*self.spec.base, self.spec.delta] if t.source_id == document["source_id"])
            if fragment["text"] != ticket.text:
                raise ValueError("Unknown authored source text")
            value = ExtractedNeeds(needs=[ExtractedNeed(need_id="pending", source_id=document["source_id"],
                fragment_id=fragment["fragment_id"], evidence_quote=n.quote, scenario=n.scenario,
                need_type="feature_request", statement=n.statement, explicit_request=True) for n in definitions])
        else:
            need = request.data["need"]
            definition = next(n for values in self.spec.authored_needs.values() for n in values if n.statement == need["statement"])
            ref = EvidenceRef(source_id=need["source_id"], fragment_id=need["fragment_id"], quote=need["evidence_quote"])
            if request.operation == "assign_topic":
                candidates = [t["topic_id"] for t in request.data["topics"] if t["passport"]["need_statement"] == definition.statement]
                decision = "review" if definition.label == "review" else ("match" if candidates else "provisional_no_match")
                value = BatchAssignment(decision=decision, matched_topic_id=candidates[0] if decision == "match" else None,
                    candidate_topic_ids=candidates, reason="Явно подготовленный synthetic/authored ответ, модель не вызывалась",
                    evidence_refs=[ref])
            else:
                if request.operation != "propose_topic" or definition.label == "review":
                    raise ValueError("Unsupported authored operation")
                value = ProposedTopic(title="Синтетическая тема " + definition.label, summary=need["statement"],
                    scenario=need["scenario"], need_statement=need["statement"], scope="Синтетические границы " + definition.label,
                    inclusion_examples=[PassportExample(text=need["evidence_quote"], evidence_refs=[ref])])
        record = make_record(request, response_schema, value.model_dump(mode="json"), model_id=self.profile.model_id,
            normalization_hash=self.profile.parameters["normalization_hash"], set_version=self.profile.recording_set_version,
            origin="authored")
        self.records.append(record)
        return GatewayResult(value=value, metadata=record.metadata)
