"""Request and response schemas. Input models forbid unknown fields, so a client
cannot smuggle extra attributes such as ``role`` into a request."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Strategy = Literal["fcfs", "need_weighted", "priority"]
Role = Literal["admin", "member", "viewer"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Text = Annotated[str, StringConstraints(max_length=2000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]
Quantity = Annotated[int, Field(ge=1, le=1_000_000)]
Stock = Annotated[int, Field(ge=0, le=100_000_000)]
Weight = Annotated[int, Field(ge=1, le=1000)]
Priority = Annotated[int, Field(ge=1, le=10)]
# Empty string clears the region.
Region = Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- auth / directory


class LoginIn(In):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class RegisterIn(In):
    email: EmailStr
    display_name: Name
    password: Password


class OrganizationOut(BaseModel):
    id: int
    name: str
    default_weight: int | None = None
    default_priority: int | None = None
    region: str | None = None
    is_active: bool | None = None
    version: int | None = None


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: Role
    organization_id: int | None
    is_active: bool
    version: int


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut | None
    csrf_token: str
    session_expires_at: datetime


class OrganizationCreate(In):
    name: Name
    default_weight: Weight = 1
    default_priority: Priority = 5
    region: Region | None = None


class OrganizationUpdate(In):
    version: int
    name: Name | None = None
    default_weight: Weight | None = None
    default_priority: Priority | None = None
    region: Region | None = None
    is_active: bool | None = None
    reason: Reason | None = None


class UserCreate(In):
    email: EmailStr
    display_name: Name
    password: Password
    role: Role
    organization_id: int | None = None


class UserUpdate(In):
    version: int
    display_name: Name | None = None
    role: Role | None = None
    organization_id: int | None = None
    is_active: bool | None = None
    password: Password | None = None
    reason: Reason | None = None


# ---------------------------------------------------------------- inventory


class LotCreate(In):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Text = ""
    unit: Unit
    quantity_on_hand: Stock
    best_before: date | None = None


class LotUpdate(In):
    version: int
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
        | None
    ) = None
    description: Text | None = None
    unit: Unit | None = None
    best_before: date | None = None
    clear_best_before: bool = False
    quantity_on_hand: Stock | None = None
    reason: Reason | None = None


class LotOut(BaseModel):
    id: int
    name: str
    description: str
    unit: str
    quantity_on_hand: int
    quantity_reserved: int
    quantity_available: int
    best_before: date | None
    reserved_by_round_id: int | None
    version: int


# ---------------------------------------------------------------- rounds


class RoundCreate(In):
    name: Name
    description: Text = ""
    strategy: Strategy
    request_deadline: datetime


class RoundUpdate(In):
    version: int  # the round's public_version the edit is based on
    name: Name | None = None
    description: Text | None = None
    strategy: Strategy | None = None
    request_deadline: datetime | None = None
    reason: Reason | None = None


class TransitionIn(In):
    version: int | None = None


class CancelIn(In):
    reason: Reason


class ReserveIn(In):
    lot_id: int
    quantity: Quantity


class ReservationUpdate(In):
    quantity: Quantity
    reason: Reason | None = None


class ParticipantIn(In):
    eligible: bool
    weight: Weight
    priority: Priority
    reason: Reason


class RequestCreate(In):
    lot_id: int
    quantity: Quantity


class RequestUpdate(In):
    quantity: Quantity
    version: int


class WithdrawIn(In):
    version: int


class RoundLotOut(BaseModel):
    lot_id: int
    name: str
    description: str
    unit: str
    best_before: date | None
    quantity: int
    status: str
    allocated_quantity: int | None


class RequestOut(BaseModel):
    id: int
    round_id: int
    lot_id: int
    organization_id: int
    organization_name: str | None = None
    quantity: int
    status: str
    version: int
    accepted_seq: int | None = None  # admins only
    created_at: datetime
    updated_at: datetime


class ParticipantOut(BaseModel):
    organization_id: int
    organization_name: str
    organization_active: bool
    eligible: bool
    weight: int
    priority: int
    version: int


class RoundSummary(BaseModel):
    id: int
    name: str
    description: str
    status: str
    strategy: Strategy
    strategy_label: str
    request_deadline: datetime
    deadline_passed: bool
    public_version: int
    version: int | None = None  # admins
    org_version: int | None = None  # members
    lot_count: int
    total_quantity: int
    my_request_count: int | None = None
    opened_at: datetime | None
    closed_at: datetime | None
    allocated_at: datetime | None
    cancelled_at: datetime | None


class SnapshotOut(BaseModel):
    strategy: Strategy
    strategy_version: int
    input_hash: str
    result_hash: str | None
    created_at: datetime
    finalized_at: datetime | None


class RoundDetail(RoundSummary):
    lots: list[RoundLotOut]
    requests: list[RequestOut]
    participation: ParticipantOut | None = None  # members: own organization
    participants: list[ParticipantOut] | None = None  # admins
    snapshot: SnapshotOut | None = None


class ResultLine(BaseModel):
    request_id: int
    organization_id: int
    organization_name: str | None
    requested: int
    allocated: int
    explanation: str
    details: dict[str, Any]


class LotOutcome(BaseModel):
    lot_id: int
    name: str
    unit: str
    supply: int
    allocated_total: int
    unallocated: int
    lines: list[ResultLine]


class AllocationOut(BaseModel):
    round_id: int
    is_preview: bool
    strategy: Strategy
    strategy_label: str
    strategy_version: int
    frozen_strategy: Strategy
    input_hash: str
    result_hash: str
    lots: list[LotOutcome]


class PublicLot(BaseModel):
    name: str
    unit: str
    quantity: int
    allocated_total: int | None
    organizations_served: int | None


class PublicRound(BaseModel):
    id: int
    name: str
    description: str
    status: str
    strategy_label: str
    request_deadline: datetime
    public_version: int
    lots: list[PublicLot]


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor_user_id: int | None
    actor_email: str | None
    actor_name: str | None
    action: str
    target_type: str
    target_id: str
    round_id: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    request_id: str | None


class VersionsOut(BaseModel):
    rounds: list[dict[str, int]]
    # Changes whenever any conversation the user can read changes; null for viewers.
    chat: str | None = None
    resync_interval_seconds: int


# ---------------------------------------------------------------- messaging

ChannelKind = Literal["global", "local", "group", "direct"]
MessageBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
ClientId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{8,64}$")]
GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
UserIds = Annotated[list[int], Field(min_length=1, max_length=50)]


class Person(BaseModel):
    id: int
    display_name: str
    role: Role
    organization_name: str | None


class MessageOut(BaseModel):
    id: int
    channel_id: int
    kind: Literal["user", "system"]
    sender: Person | None
    body: str
    created_at: datetime
    deleted: bool
    client_id: str | None


class ChannelOut(BaseModel):
    id: int
    kind: ChannelKind
    title: str
    subtitle: str
    region: str | None
    members: list[Person] | None
    member_count: int
    last_message: MessageOut | None
    last_activity_at: datetime
    last_read_message_id: int
    unread_count: int
    version: int


class MessagePage(BaseModel):
    messages: list[MessageOut]
    has_more: bool


class MessageIn(In):
    body: MessageBody
    client_id: ClientId | None = None


class ReadIn(In):
    message_id: int


class DirectIn(In):
    user_id: int


class GroupIn(In):
    name: GroupName
    member_ids: UserIds


class MembersIn(In):
    user_ids: UserIds


# ---------------------------------------------------------------- planning

Category = Literal["produce", "protein", "dairy", "grains", "canned", "baby", "hygiene", "other"]
Capacity = Annotated[int, Field(ge=0, le=1_000_000)]


class Preferences(In):
    households_served: Capacity = 0
    priority_categories: Annotated[list[Category], Field(max_length=8)] = []
    avoid_categories: Annotated[list[Category], Field(max_length=8)] = []
    dry_storage_capacity: Capacity | None = None
    cold_storage_capacity: Capacity | None = None
    frozen_storage_capacity: Capacity | None = None
    max_share_percent: Annotated[int, Field(ge=5, le=100)] = 40
    dietary_notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class ProfileOut(BaseModel):
    organization_id: int
    organization_name: str
    preferences: Preferences
    version: int
    updated_at: datetime | None
    updated_by_name: str | None


class ProfileIn(In):
    version: int
    preferences: Preferences


class SuggestedProfile(BaseModel):
    preferences: Preferences
    rationale: dict[str, str]
    based_on_rounds: int


class NeedOut(BaseModel):
    item: str
    quantity: int | None
    category: Category
    source: str


class DocumentOut(BaseModel):
    id: int
    kind: Literal["upload", "note"]
    title: str
    filename: str | None
    content_type: str | None
    size_bytes: int
    created_at: datetime
    created_by_name: str | None
    analysis: dict[str, Any]


class DocumentDetail(DocumentOut):
    text: str


class NoteIn(In):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]


class PlanLine(BaseModel):
    round_id: int
    lot_id: int
    lot_name: str
    unit: str
    category: Category
    storage: Literal["dry", "cold", "frozen"]
    offered: int
    suggested: int
    current_quantity: int | None
    current_request_id: int | None
    current_request_version: int | None
    reasons: list[str]


class PlanRound(BaseModel):
    round_id: int
    round_name: str
    strategy: Strategy
    strategy_label: str
    request_deadline: datetime
    tip: str
    lines: list[PlanLine]


class Plan(BaseModel):
    key: Literal["balanced", "priorities", "lean"]
    title: str
    tagline: str
    summary: str
    total_units: int
    line_count: int
    rounds: list[PlanRound]
    warnings: list[str]


class PlanInsights(BaseModel):
    rounds_participated: int
    requested_total: int
    allocated_total: int
    fill_rate_percent: int | None
    top_categories: list[str]


class PlansOut(BaseModel):
    organization_id: int
    organization_name: str
    generated_at: datetime
    inputs: dict[str, Any]
    needs: list[NeedOut]
    insights: PlanInsights
    plans: list[Plan]
