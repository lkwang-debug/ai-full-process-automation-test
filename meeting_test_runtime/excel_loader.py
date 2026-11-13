"""Read generated functional cases and coverage ledgers from Excel."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from openpyxl import load_workbook

from .models import (CaseStatus, CaseType, ClientProfile, ClosureType,
                     DependencyFlow, EnvironmentRisk, EventExpectation,
                     ExpectedOutcome, FlowStep, FunctionalCase)


class ExcelCaseLoader:
    PROFILE_SHEET = "终端配置"; DOMAIN_SHEET = "参数域"; CASE_SHEET = "功能用例"
    SIGNAL_SHEET = "Conf信令检查"; METHOD_SHEET = "业务方法检查"
    FLOW_SHEET = "依赖流程"

    def __init__(self, path) -> None: self.path = Path(path)

    def load_profiles(self, accounts: Mapping[str, Mapping[str, Any]],
                      environments: Mapping[str, Mapping[str, Any]]) -> List[ClientProfile]:
        profiles = []
        for row in self._read_sheet(self.PROFILE_SHEET):
            if not self._enabled(row.get("启用")): continue
            account_ref = self._required(row, "账号引用"); env_ref = self._required(row, "环境引用")
            if account_ref not in accounts: raise ValueError(f"账号引用不存在: {account_ref}")
            if env_ref not in environments: raise ValueError(f"环境引用不存在: {env_ref}")
            profiles.append(ClientProfile(
                self._required(row, "配置ID"), self._required(row, "终端类型").lower(),
                dict(accounts[account_ref]), dict(environments[env_ref]),
                frozenset(self._csv(row.get("可承担角色"))), True))
        return profiles

    def load_cases(self, *, include_disabled: bool = False) -> List[FunctionalCase]:
        cases, seen = [], set()
        for row in self._read_sheet(self.CASE_SHEET):
            row_enabled = self._enabled(row.get("是否执行"))
            if not row_enabled and not include_disabled:
                continue
            case_id = self._required(row, "用例ID")
            if case_id in seen: raise ValueError(f"重复用例ID: {case_id}")
            seen.add(case_id)
            notifies = tuple(EventExpectation(
                source=item.get("source", "conf"), cmdid=item.get("cmdid"),
                kind=item.get("kind", "notify"), match=item.get("match", {}),
                receivers=tuple(self._json_aliases(item.get("receivers"))),
                forbidden_receivers=tuple(
                    self._json_aliases(item.get("forbidden_receivers"))))
                for item in self._json_list(row.get("预期Notify序列JSON"), "预期Notify序列JSON", case_id))
            cases.append(FunctionalCase(
                case_id=case_id, module=self._required(row, "模块"),
                case_type=CaseType(self._required(row, "用例类型").upper()),
                scene_type=self._required(row, "场景类型"),
                precondition=self._optional(row.get("前置模板")) or "NONE",
                actor=self._required(row, "操作角色"), target=self._optional(row.get("目标角色")),
                operation=self._required(row, "统一操作名"),
                params=self._json_object(row.get("参数JSON"), "参数JSON", case_id),
                expected_business=self._optional(row.get("预期业务键")) or case_id,
                expected_fields=self._json_object(row.get("字段断言JSON"), "字段断言JSON", case_id),
                expected_outcome=ExpectedOutcome(self._required(row, "预期结果类型").upper()),
                expected_error_codes=self._error_codes(row.get("预期错误码JSON"), case_id),
                isolated=self._enabled(row.get("独立执行")),
                priority=self._optional(row.get("执行级别")) or "P1",
                enabled=row_enabled,
                closure_type=ClosureType(self._required(row, "闭环类型").upper()),
                expected_response=self._json_object(row.get("预期Response"), "预期Response", case_id),
                expected_notifies=notifies,
                expected_receivers=tuple(self._csv(row.get("预期接收端"))),
                forbidden_receivers=tuple(self._csv(row.get("不应接收端"))),
                environment_risk=EnvironmentRisk(
                    (self._optional(row.get("环境风险")) or "SAFE_REUSABLE").upper()),
                status=CaseStatus(
                    (self._optional(row.get("用例状态")) or "IMPLEMENTED").upper()),
                blocker_id=self._optional(row.get("阻塞ID")),
                flow_id=self._optional(row.get("依赖流程ID"))))
        return cases

    def load_parameter_domains(self): return self._read_sheet(self.DOMAIN_SHEET)
    def load_dependency_flows(self) -> List[DependencyFlow]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in self._read_sheet(self.FLOW_SHEET):
            flow_id = self._required(row, "流程ID")
            grouped.setdefault(flow_id, []).append(row)
        flows = []
        for flow_id, rows in grouped.items():
            rows.sort(key=lambda item: int(item.get("步骤序号") or 0))
            sequences = [int(item.get("步骤序号") or 0) for item in rows]
            if sequences != list(range(1, len(rows) + 1)):
                raise ValueError(f"{flow_id}的步骤序号必须从1连续递增")
            first = rows[0]
            steps = []
            for row in rows:
                step_id = f"{flow_id}#{row.get('步骤序号')}"
                steps.append(FlowStep(
                    sequence=int(row["步骤序号"]), actor=self._required(row, "执行端"),
                    operation=self._required(row, "统一操作名"),
                    params=self._json_object(row.get("参数JSON"), "参数JSON", step_id),
                    output_variables=self._json_object(
                        row.get("输出变量JSON"), "输出变量JSON", step_id),
                    expected_response=self._json_object(
                        row.get("预期Response"), "预期Response", step_id),
                    expected_notifies=tuple(EventExpectation(
                        source=item.get("source", "conf"), cmdid=item.get("cmdid"),
                        kind=item.get("kind", "notify"), match=item.get("match", {}),
                        receivers=tuple(self._json_aliases(item.get("receivers"))),
                        forbidden_receivers=tuple(
                            self._json_aliases(item.get("forbidden_receivers"))))
                        for item in self._json_list(
                            row.get("预期Notify序列JSON"), "预期Notify序列JSON", step_id)),
                    expected_receivers=tuple(self._csv(row.get("预期接收端"))),
                    forbidden_receivers=tuple(self._csv(row.get("不应接收端"))),
                    expected_error_codes=self._error_codes(
                        row.get("预期错误码JSON"), step_id),
                    expected_nonzero_result=self._enabled(
                        row.get("预期非零Result")),
                    stop_on_failure=self._enabled(row.get("失败是否终止"))))
            flows.append(DependencyFlow(
                flow_id=flow_id, name=self._required(first, "流程名称"),
                priority=self._required(first, "优先级").upper(),
                environment_risk=EnvironmentRisk(self._required(first, "环境风险").upper()),
                cleanup_requirement=self._optional(first.get("清理要求")) or "",
                status=CaseStatus(self._required(first, "实现状态").upper()),
                steps=tuple(steps)))
        return flows
    def load_signal_checklist(self): return self._read_sheet(self.SIGNAL_SHEET)
    def load_method_checklist(self): return self._read_sheet(self.METHOD_SHEET)

    def _read_sheet(self, name: str) -> List[Dict[str, Any]]:
        if not self.path.exists(): raise FileNotFoundError(self.path)
        book = load_workbook(self.path, read_only=True, data_only=True)
        try:
            if name not in book.sheetnames: raise ValueError(f"Excel缺少Sheet: {name}")
            rows = book[name].iter_rows(values_only=True)
            try: headers = [str(v).strip() if v is not None else "" for v in next(rows)]
            except StopIteration: return []
            if not all(headers): raise ValueError(f"Sheet表头不能为空: {name}")
            return [dict(zip(headers, values)) for values in rows
                    if any(value not in (None, "") for value in values)]
        finally: book.close()

    @staticmethod
    def _parse_json(value, field, case_id, default):
        if value in (None, ""): return default
        try: return value if isinstance(value, (dict, list)) else json.loads(str(value))
        except json.JSONDecodeError as exc: raise ValueError(f"{case_id}的{field}不是合法JSON") from exc

    @classmethod
    def _json_object(cls, value, field, case_id):
        parsed = cls._parse_json(value, field, case_id, {})
        if not isinstance(parsed, dict): raise ValueError(f"{case_id}的{field}必须是JSON对象")
        return dict(parsed)

    @classmethod
    def _json_list(cls, value, field, case_id):
        parsed = cls._parse_json(value, field, case_id, [])
        if not isinstance(parsed, list): raise ValueError(f"{case_id}的{field}必须是JSON数组")
        return parsed

    @classmethod
    def _error_codes(cls, value, case_id):
        values = cls._json_list(value, "预期错误码JSON", case_id)
        result = []
        for item in values:
            result.append(int(item, 0) if isinstance(item, str) else int(item))
        return tuple(result)

    @staticmethod
    def _required(row, field):
        value = row.get(field)
        if value in (None, ""): raise ValueError(f"必填字段为空: {field}")
        return str(value).strip()

    @staticmethod
    def _optional(value) -> Optional[str]:
        return None if value in (None, "") else str(value).strip()

    @staticmethod
    def _enabled(value):
        return str(value or "").strip().lower() in {"是", "yes", "true", "1", "y"}

    @staticmethod
    def _csv(value) -> Iterable[str]:
        return [part.strip() for part in str(value or "").split(",") if part.strip()]

    @classmethod
    def _json_aliases(cls, value) -> Iterable[str]:
        """Accept a compact alias string or a JSON array in notify metadata."""
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return cls._csv(value)
