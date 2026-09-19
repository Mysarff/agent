from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    capability: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=2000)
    depends_on: list[str] = Field(default_factory=list, max_length=5)


class Plan(StrictModel):
    action: Literal["execute", "clarify", "unsupported"]
    message: str = Field(default="", max_length=1000)
    steps: list[Step] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def check_shape(self):
        if self.action == "execute" and not self.steps:
            raise ValueError("执行计划必须有任务")
        if self.action != "execute" and (self.steps or not self.message.strip()):
            raise ValueError("追问或不支持时只返回说明，不执行任务")
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("任务 ID 重复")
        return self


class Capability(StrictModel):
    id: str
    description: str
    examples: list[str]
    handler: Literal["a2a", "knowledge", "generation"]
    endpoint: str | None = None
    requires_confirmation: bool = False
    data_notice: str = ""
    structured_request: bool = False


class Evidence(StrictModel):
    source_id: str
    quote: str = Field(min_length=6, max_length=600)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class GroundedAnswer(StrictModel):
    supported: bool
    claims: list[Claim] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def check_support(self):
        if self.supported != bool(self.claims):
            raise ValueError("有据回答需包含带引用的结论，无据时不得生成结论")
        return self


class StepResult(StrictModel):
    step_id: str
    capability: str
    status: Literal["success", "failed", "input_required", "blocked", "insufficient_evidence",
                    "awaiting_confirmation", "cancelled"]
    text: str
    sources: list[dict] = Field(default_factory=list)
    elapsed_ms: float = 0
    error_code: str | None = None
    trace: list[dict] = Field(default_factory=list)


class RunResult(StrictModel):
    run_id: str
    plan: Plan | None = None
    results: list[StepResult] = Field(default_factory=list)
    message: str = ""
    pending_token: str | None = None
    routing_trace: dict = Field(default_factory=dict)

    def render(self) -> str:
        parts = [self.message] if self.message else []
        for result in self.results:
            parts.append(f"**{result.capability} · {result.status}**\n\n{result.text}")
        return "\n\n".join(parts) or "没有可展示的结果。"
