"""External pytest/Excel runtime for TestClient packages."""
from .drivers import (
    TestClientDriver,
    create_client_driver,
    register_client_driver,
    registered_client_types,
)
from .expectations import OPERATION_EXPECTATIONS, get_expectation
from .excel_loader import ExcelCaseLoader
from .fm_driver import FmClientDriver, register_fm_driver
from .web_driver import WebClientDriver, register_web_driver
from .http_driver import WebApiServiceDriver
from .resource_manager import TestResourceManager
from .blockers import (
    BLOCKERS, BlockerKind, ProtocolBlocker, blocker_for_cmdid,
    blocker_for_operation,
)
from .config import TestEnvironmentConfig
from .recording_data import RecordingDataUnavailable, RecordingDataset, RecordingDatasetProvider
from .models import (
    ClientProfile,
    CaseType,
    ClosureType,
    DeliveryPolicy,
    EnvironmentRisk,
    CaseStatus,
    DependencyFlow,
    FlowStep,
    EventExpectation,
    ServiceProfile,
    ExpectedOutcome,
    FunctionalCase,
    MissingSessionManagerApi,
    OperationExpectation,
    SessionKey,
)
from .case_executor import CaseExecutionResult, FunctionalCaseExecutor
from .flow_executor import DependencyFlowExecutor
from .runtime import MeetingTestRuntime
from .session_pool import MeetingSessionPool

# The only currently available development TestClient is registered by default.
register_fm_driver()
register_web_driver()

__all__ = [
    "CaseExecutionResult", "CaseType", "ClientProfile", "ClosureType", "DeliveryPolicy",
    "DependencyFlowExecutor",
    "EventExpectation", "EnvironmentRisk", "CaseStatus", "DependencyFlow", "FlowStep",
    "ServiceProfile",
    "ExpectedOutcome", "ExcelCaseLoader", "FmClientDriver", "FunctionalCaseExecutor",
    "FunctionalCase", "MeetingSessionPool", "MeetingTestRuntime",
    "MissingSessionManagerApi", "OPERATION_EXPECTATIONS", "OperationExpectation",
    "SessionKey", "TestClientDriver", "create_client_driver", "get_expectation",
    "register_client_driver", "registered_client_types", "TestResourceManager",
    "WebApiServiceDriver",
    "WebClientDriver", "register_web_driver",
    "TestEnvironmentConfig",
    "RecordingDataUnavailable", "RecordingDataset", "RecordingDatasetProvider",
    "BLOCKERS", "BlockerKind", "ProtocolBlocker", "blocker_for_cmdid",
    "blocker_for_operation",
]
