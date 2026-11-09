from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Count = Annotated[int, Field(ge=0, strict=True)]


class MaterialCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: Literal["materials"] = "materials"
    total: Count
    successful: Count
    failed: Count
    empty: Count
    usable: Count
    ratio: Annotated[float, Field(ge=0, le=1)] | None

    @model_validator(mode="after")
    def counts(self):
        if (self.total != self.successful + self.failed or self.empty + self.usable != self.successful
                or self.ratio != (self.successful / self.total if self.total else None)):
            raise ValueError("Покрытие рассчитывается по исходам материалов")
        return self


class SourceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: Literal["sources"] = "sources"
    total: Count
    complete: Count
    partial: Count
    failed: Count
    empty: Count

    @model_validator(mode="after")
    def counts(self):
        if self.total != self.complete + self.partial + self.failed + self.empty:
            raise ValueError("Источники считаются отдельно от материалов")
        return self
