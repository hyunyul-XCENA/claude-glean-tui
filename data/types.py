"""Structured return types for the data layer.

All public ``get_*()`` functions return TypedDicts instead of bare
``Dict[str, Any]``.  At runtime these are plain dicts — the types
exist purely for static analysis and IDE autocompletion.
"""
from __future__ import annotations

from typing import List, TypedDict, Union


# ── Delete ───────────────────────────────────────────────────────────────────

class DeleteResult(TypedDict, total=False):
    ok: bool
    error: str


# ── Usage ────────────────────────────────────────────────────────────────────

class UsageWindowLive(TypedDict):
    usage_pct: float
    resets_at: str


class UsageContextWindow(TypedDict):
    used_pct: int
    remaining_pct: int


class UsageCost(TypedDict):
    total_usd: float


class UsageStatsLive(TypedDict):
    source: str
    window_5h: UsageWindowLive
    window_weekly: UsageWindowLive
    context_window: UsageContextWindow
    cost: UsageCost
    model: str
    timestamp: str


class UsageWindowEstimated(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    remaining_tokens: int
    usage_pct: float
    session_count: int


class UsageTier(TypedDict):
    name: str
    limit_5h: int
    limit_weekly: int


class UsagePerSession(TypedDict):
    session_id: str
    project_name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_tokens: int
    message_count: int
    last_activity: str


class UsageCostEstimate(TypedDict):
    window_5h_usd: float
    window_weekly_usd: float


class UsageStatsEstimated(TypedDict):
    source: str
    tier: UsageTier
    window_5h: UsageWindowEstimated
    window_weekly: UsageWindowEstimated
    per_session: List[UsagePerSession]
    cost_estimate: UsageCostEstimate
    timestamp: str


UsageStats = Union[UsageStatsLive, UsageStatsEstimated]


# ── Sessions ─────────────────────────────────────────────────────────────────

class ProcessInfo(TypedDict):
    pid: int
    state: str
    tty: str
    started: str
    command: str
    cwd: str


class SessionsResult(TypedDict):
    sessions: List[ProcessInfo]


class SessionDetail(TypedDict):
    session_id: str
    slug: str
    project_name: str
    message_count: int
    is_active: bool
    context_tokens: int
    context_pct: int
    last_timestamp: str


class SessionDetailResult(TypedDict):
    sessions: List[SessionDetail]


class ActivityEntry(TypedDict):
    text: str
    project: str
    ageSeconds: int


class ActivityResult(TypedDict):
    today_count: int
    recent: List[ActivityEntry]


class XrayBreakdownEntry(TypedDict):
    name: str
    tokens: int
    pct: float
    display: str


class SessionXray(TypedDict, total=False):
    session_id: str
    context_tokens: int
    context_max: int
    context_pct: int
    breakdown: List[XrayBreakdownEntry]
    compacts_total: int
    messages_since_compact: int
    recommendation: str
    error: str


# ── Health ───────────────────────────────────────────────────────────────────

class HealthItems(TypedDict):
    claude_md: bool
    permissions: bool
    hooks: bool
    agents: bool
    skills: bool
    connectors: bool
    plugins: bool


class HealthResult(TypedDict):
    score: int
    total: int
    items: HealthItems


# ── Components ───────────────────────────────────────────────────────────────

class PluginInfo(TypedDict):
    key: str
    name: str
    version: str
    enabled: bool
    skills_count: int
    agents_count: int
    connectors_count: int


class PluginsResult(TypedDict):
    plugins: List[PluginInfo]


class SkillInfo(TypedDict):
    name: str
    description: str
    path: str
    source: str
    content: str


class SkillsResult(TypedDict):
    skills: List[SkillInfo]


class AgentInfo(TypedDict):
    name: str
    description: str
    model: str
    tools: List[str]
    source: str
    content: str


class AgentsResult(TypedDict):
    agents: List[AgentInfo]


class HookInfo(TypedDict):
    event: str
    matcher: str
    type: str
    command: str
    description: str
    source: str
    _event_index: int


class HooksResult(TypedDict):
    hooks: List[HookInfo]


# ── Connectors ───────────────────────────────────────────────────────────────

class ConnectorInfo(TypedDict):
    name: str
    command: str
    type: str
    args: List[str]
    source: str
    tools: List[str]
    tool_count: int


class ConnectorsResult(TypedDict):
    connectors: List[ConnectorInfo]
