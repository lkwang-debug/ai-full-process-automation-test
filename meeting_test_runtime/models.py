"""Shared models for the external sessionmanager test runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Tuple


class ClosureType(str, Enum):
    FM_TO_FM = "FM_TO_FM"
    HTTP_TO_FM = "HTTP_TO_FM"
    HTTP_TO_FM_JOIN = "HTTP_TO_FM_JOIN"
    HTTP_RESULT = "HTTP_RESULT"


class DeliveryPolicy(str, Enum):
    SENDER_ONLY = "sender_only"
    OTHERS_ONLY = "others_only"
    ALL_PARTICIPANTS = "all_participants"
    TARGET_ONLY = "target_only"
    SENDER_AND_TARGET = "sender_and_target"
    ROLE_TARGETED = "role_targeted"
    NONE = "none"


class CaseType(str, Enum):
    NORMAL = "NORMAL"
    BUSINESS_EXCEPTION = "BUSINESS_EXCEPTION"


class EnvironmentRisk(str, Enum):
    SAFE_REUSABLE = "SAFE_REUSABLE"
    ISOLATED_STATEFUL = "ISOLATED_STATEFUL"
    DESTRUCTIVE_ISOLATED = "DESTRUCTIVE_ISOLATED"
    RISKY_MANUAL = "RISKY_MANUAL"
    BLOCKED = "BLOCKED"


class CaseStatus(str, Enum):
    DESIGNED = "DESIGNED"
    IMPLEMENTED = "IMPLEMENTED"
    COVERED_BY_CASES = "COVERED_BY_CASES"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    PASS = "PASS"
    FAIL_TEST = "FAIL_TEST"
    FAIL_SERVER = "FAIL_SERVER"
    SKIP_BLOCKED = "SKIP_BLOCKED"
    SKIP_ENV = "SKIP_ENV"


class ExpectedOutcome(str, Enum):
    SUCCESS_EVENT = "SUCCESS_EVENT"
    SUCCESS_RESPONSE = "SUCCESS_RESPONSE"
    ERROR_RESPONSE = "ERROR_RESPONSE"
    ERROR_AND_NO_EVENT = "ERROR_AND_NO_EVENT"
    ERROR_EVENT = "ERROR_EVENT"
    NO_SUCCESS_EVENT = "NO_SUCCESS_EVENT"
    EXPECTED_DISCONNECT = "EXPECTED_DISCONNECT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EVENT_SEQUENCE = "EVENT_SEQUENCE"


class MissingSessionManagerApi(RuntimeError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"MISSING_SESSIONMANAGER_API: {operation}")


@dataclass(frozen=True)
class ClientProfile:
    config_id: str
    client_type: str
    account: Dict[str, Any]
    endpoint: Dict[str, Any]
    roles: FrozenSet[str] = field(default_factory=frozenset)
    enabled: bool = True


@dataclass(frozen=True)
class ServiceProfile:
    config_id: str
    service_type: str
    config: Dict[str, Any]
    enabled: bool = True


@dataclass(frozen=True)
class EventExpectation:
    source: Optional[str] = "conf"
    cmdid: Optional[int] = None
    kind: Optional[str] = None
    match: Dict[str, Any] = field(default_factory=dict)
    # Optional per-notify routing overrides. They are needed for one operation
    # that emits different notifications to the host and waiting participant.
    receivers: Tuple[str, ...] = ()
    forbidden_receivers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationExpectation:
    operation: str
    closure_type: ClosureType
    request_cmdid: Optional[int]
    response: Optional[EventExpectation] = None
    notifies: Tuple[EventExpectation, ...] = ()
    delivery: DeliveryPolicy = DeliveryPolicy.NONE
    forbidden_delivery: Tuple[str, ...] = ()
    key_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FunctionalCase:
    case_id: str
    module: str
    case_type: CaseType
    scene_type: str
    precondition: str
    actor: str
    target: Optional[str]
    operation: str
    params: Dict[str, Any]
    expected_business: str
    expected_fields: Dict[str, Any]
    expected_outcome: ExpectedOutcome = ExpectedOutcome.SUCCESS_EVENT
    expected_error_codes: Tuple[int, ...] = ()
    isolated: bool = False
    priority: str = "P1"
    enabled: bool = True
    closure_type: ClosureType = ClosureType.FM_TO_FM
    expected_response: Dict[str, Any] = field(default_factory=dict)
    expected_notifies: Tuple[EventExpectation, ...] = ()
    expected_receivers: Tuple[str, ...] = ()
    forbidden_receivers: Tuple[str, ...] = ()
    environment_risk: EnvironmentRisk = EnvironmentRisk.SAFE_REUSABLE
    status: CaseStatus = CaseStatus.IMPLEMENTED
    blocker_id: Optional[str] = None
    flow_id: Optional[str] = None


@dataclass(frozen=True)
class FlowStep:
    sequence: int
    actor: str
    operation: str
    params: Dict[str, Any] = field(default_factory=dict)
    output_variables: Dict[str, str] = field(default_factory=dict)
    expected_response: Dict[str, Any] = field(default_factory=dict)
    expected_notifies: Tuple[EventExpectation, ...] = ()
    expected_receivers: Tuple[str, ...] = ()
    forbidden_receivers: Tuple[str, ...] = ()
    expected_error_codes: Tuple[int, ...] = ()
    expected_nonzero_result: bool = False
    stop_on_failure: bool = True


@dataclass(frozen=True)
class DependencyFlow:
    flow_id: str
    name: str
    priority: str
    environment_risk: EnvironmentRisk
    cleanup_requirement: str
    status: CaseStatus
    steps: Tuple[FlowStep, ...]


@dataclass(frozen=True)
class SessionKey:
    scene_type: str
    client_combination: str
    room_id: int
    roles: Tuple[str, ...]
