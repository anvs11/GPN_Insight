"""Единая политика HTTP-выдачи originals; raw repository остаётся внутренним."""

from .schemas import SourceDetail, TopicDetail


def public_source(source: SourceDetail) -> SourceDetail:
    source = SourceDetail.model_validate(source.model_dump())
    if source.synthetic:
        return source
    # Нельзя дать browser URL/исходное имя из закрытого originals-хранилища.
    assets = [a.model_copy(update={"path": "restricted" + {
        "audio/wav": ".wav", "audio/mpeg": ".mp3", "image/png": ".png", "image/jpeg": ".jpg"
    }[a.mime_type], "source_url": None}) for a in source.ticket.assets]
    ticket = source.ticket.model_copy(update={"text": "", "requester_id": None, "assets": assets})
    return SourceDetail.model_validate({**source.model_dump(), "ticket": ticket.model_dump(), "original_access": "restricted"})


def public_topic(card: TopicDetail) -> TopicDetail:
    return TopicDetail.model_validate({**card.model_dump(), "sources": [public_source(s).model_dump() for s in card.sources]})
