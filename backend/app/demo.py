"""Только загрузка подготовленного синтетического состояния, не импорт тикетов."""

import hashlib
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from .schemas import Contract, ExtractedNeed, NormalizedDocument, Text, TicketInput, Topic


class DemoFixture(Contract):
    fixture_id: Text
    synthetic: Literal[True]
    origin: Literal["authored"]
    sources: list[TicketInput]
    documents: list[NormalizedDocument]
    needs: list[ExtractedNeed]
    topic: Topic

    @model_validator(mode="after")
    def references(self) -> Self:
        sources = {s.source_id: s for s in self.sources}
        documents = {d.source_id: d for d in self.documents}
        needs = {n.need_id: n for n in self.needs}
        if len(sources) != len(self.sources) or len(documents) != len(self.documents) or len(needs) != len(self.needs):
            raise ValueError("Идентификаторы начального состояния должны быть уникальны")
        if sources.keys() != documents.keys() or not sources:
            raise ValueError("Для каждого источника нужен нормализованный документ")
        for source in self.sources:
            if source.assets or not source.text:
                raise ValueError("Этап 1 поддерживает только текстовую синтетическую фикстуру")
        fragments = {}
        for document in self.documents:
            for fragment in document.fragments:
                if fragment.kind != "text" or not fragment.text or fragment.text not in sources[document.source_id].text:
                    raise ValueError("Текстовый фрагмент фикстуры должен дословно входить в исходное обращение; медиа не поддерживаются")
                if fragment.fragment_id in fragments:
                    raise ValueError("fragment_id должны быть уникальны во всей фикстуре")
                fragments[fragment.fragment_id] = (document.source_id, fragment.text)
        for need in self.needs:
            source_id, text = fragments.get(need.fragment_id, (None, ""))
            if need.source_id != source_id or need.evidence_quote not in text:
                raise ValueError("Потребность должна ссылаться на свой фрагмент с точной цитатой")
        if set(self.topic.need_ids) != needs.keys():
            raise ValueError("Потребности фикстуры должны совпадать с потребностями тестовой темы")
        refs = list(self.topic.evidence)
        for example in self.topic.passport.inclusion_examples + self.topic.passport.exclusion_examples:
            refs.extend(example.evidence_refs)
        for ref in refs:
            source_id, text = fragments.get(ref.fragment_id, (None, ""))
            if ref.source_id != source_id or ref.quote not in text:
                raise ValueError("Доказательство должно содержать точную цитату своего фрагмента")
        if {e.source_id for e in self.topic.evidence} != {n.source_id for n in self.needs}:
            raise ValueError("Тема должна иметь доказательства для всех источников")
        if self.topic.status != "new" or self.topic.version != 1 or self.topic.owner is not None:
            raise ValueError("Начальное состояние темы должно быть new, version=1, без владельца")
        return self

    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


def load_fixture(path: Path) -> DemoFixture:
    return DemoFixture.model_validate_json(path.read_text(encoding="utf-8"))
