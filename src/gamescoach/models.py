"""Validated, serializable athlete profiles and coaching sessions."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import math
import re
from typing import Any

KINDS = ("workout", "training", "nutrition", "competition", "chat")


@dataclass
class AthleteProfile:
    name: str = "Athlete"
    experience: str = "unspecified"
    goals: str = "General fitness"
    training_days_per_week: int | None = None
    session_minutes: int | None = None
    equipment: str = ""
    limitations: str = ""
    competition_date: str = ""
    age: int | None = None
    bodyweight_kg: float | None = None
    dietary_preferences: str = ""

    def __post_init__(self) -> None:
        for name in ("name", "experience", "goals", "equipment", "limitations", "competition_date", "dietary_preferences"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > 8000:
                raise ValueError(f"{name} must be text of at most 8000 characters.")
        for name, low, high in (("training_days_per_week", 1, 7), ("session_minutes", 5, 300), ("age", 1, 120)):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not low <= value <= high):
                raise ValueError(f"{name} must be an integer from {low} to {high}, or omitted.")
        if self.bodyweight_kg is not None:
            if type(self.bodyweight_kg) not in (int, float) or not math.isfinite(self.bodyweight_kg) or not 1 <= self.bodyweight_kg <= 600:
                raise ValueError("bodyweight_kg must be a finite number from 1 to 600, or omitted.")
        if self.competition_date:
            try:
                parsed = date.fromisoformat(self.competition_date)
            except ValueError:
                raise ValueError("competition_date must use YYYY-MM-DD.") from None
            if parsed.isoformat() != self.competition_date:
                raise ValueError("competition_date must use YYYY-MM-DD.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AthleteProfile":
        if not isinstance(data, dict):
            raise ValueError("An athlete profile must be a JSON object.")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown profile fields: {', '.join(sorted(unknown))}.")
        return cls(**data)


@dataclass
class CoachTurn:
    provider: str
    stage: str
    content: str

    def __post_init__(self) -> None:
        if not all(isinstance(item, str) and item.strip() for item in (self.provider, self.stage, self.content)):
            raise ValueError("Every coaching turn needs a provider, stage, and nonempty content.")


@dataclass
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError("Chat messages must come from the athlete or coach.")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Chat messages need nonempty text.")


@dataclass
class CoachingSession:
    id: str
    created_at: str
    kind: str
    request: str
    profile: AthleteProfile
    turns: list[CoachTurn]
    final_plan: str
    demo: bool = False
    models: dict[str, str] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-f0-9]{12,32}", self.id):
            raise ValueError("Invalid session ID.")
        if self.kind not in KINDS:
            raise ValueError("Unknown plan kind.")
        if not isinstance(self.created_at, str):
            raise ValueError("Session time must be an ISO timestamp.")
        try:
            timestamp = datetime.fromisoformat(self.created_at)
        except ValueError:
            raise ValueError("Session time must be an ISO timestamp.") from None
        if timestamp.tzinfo is None:
            raise ValueError("Session time must include a timezone.")
        if not isinstance(self.request, str) or not self.request.strip():
            raise ValueError("A coaching request is required.")
        if not isinstance(self.profile, AthleteProfile) or not isinstance(self.turns, list) or not self.turns or not all(isinstance(t, CoachTurn) for t in self.turns):
            raise ValueError("Session profile or conversation is invalid.")
        if not isinstance(self.final_plan, str) or not self.final_plan.strip():
            raise ValueError("A final plan is required.")
        if type(self.demo) is not bool or not isinstance(self.models, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.models.items()):
            raise ValueError("Session metadata is invalid.")
        if not isinstance(self.messages, list) or not all(isinstance(message, ChatMessage) for message in self.messages):
            raise ValueError("Session chat messages are invalid.")
        if not isinstance(self.context_ids, list) or not all(isinstance(reference, str) and re.fullmatch(r"[a-f0-9]{12,32}", reference) for reference in self.context_ids):
            raise ValueError("Saved reference IDs are invalid.")
        if len(set(self.context_ids)) != len(self.context_ids) or self.id in self.context_ids:
            raise ValueError("Saved reference IDs must be distinct from the conversation ID.")
        if self.kind == "chat":
            if not self.messages or len(self.messages) % 2:
                raise ValueError("Saved chats need complete athlete/coach exchanges.")
            for index, message in enumerate(self.messages):
                if message.role != ("user" if index % 2 == 0 else "assistant"):
                    raise ValueError("Chat messages must alternate between athlete and coach.")
            if self.messages[0].content != self.request or self.messages[-1].content != self.final_plan:
                raise ValueError("Chat summary must match its first request and latest reply.")
        elif self.messages or self.context_ids:
            raise ValueError("Only chat sessions can contain chat messages or references.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoachingSession":
        if not isinstance(data, dict):
            raise ValueError("A session must be a JSON object.")
        try:
            values = dict(data)
            values["profile"] = AthleteProfile.from_dict(values["profile"])
            values["turns"] = [CoachTurn(**turn) for turn in values["turns"]]
            values["messages"] = [ChatMessage(**message) for message in values.get("messages", [])]
            return cls(**values)
        except (KeyError, TypeError) as exc:
            raise ValueError("Stored session is incomplete or invalid.") from exc
