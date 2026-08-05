from __future__ import annotations

import threading
from typing import Any


try:
    import comtypes
    from comtypes.client import CreateObject, GetModule

    GetModule("UIAutomationCore.dll")
    from comtypes.gen.UIAutomationClient import (  # type: ignore[import-not-found]
        CUIAutomation,
        IUIAutomation,
        tagPOINT,
    )
except (ImportError, OSError):  # pragma: no cover - depends on Windows install
    comtypes = None
    CreateObject = None
    CUIAutomation = None
    IUIAutomation = None
    tagPOINT = None


UIA_CONTROL_TYPE_PROPERTY_ID = 30003
UIA_NAME_PROPERTY_ID = 30005
UIA_AUTOMATION_ID_PROPERTY_ID = 30011
UIA_CLASS_NAME_PROPERTY_ID = 30012
TREE_SCOPE_SUBTREE = 0x4

CONTROL_TYPES = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}

# Names for these controls normally describe actions, not document/contact text.
SAFE_NAME_TYPES = {
    "Button",
    "CheckBox",
    "ComboBox",
    "Hyperlink",
    "MenuItem",
    "RadioButton",
    "SplitButton",
    "TabItem",
}

_local = threading.local()


def available() -> bool:
    return bool(comtypes and CreateObject and CUIAutomation and IUIAutomation)


def _automation() -> Any:
    if not available():
        return None
    if not getattr(_local, "automation", None):
        assert comtypes is not None and CreateObject is not None
        comtypes.CoInitialize()
        _local.automation = CreateObject(CUIAutomation, interface=IUIAutomation)
    return _local.automation


def _string_property(element: Any, name: str) -> str:
    try:
        return str(getattr(element, name) or "")
    except (AttributeError, OSError):
        return ""


def _int_property(element: Any, name: str) -> int:
    try:
        return int(getattr(element, name))
    except (AttributeError, OSError, TypeError, ValueError):
        return 0


def _bounds(element: Any) -> list[int] | None:
    try:
        rect = element.CurrentBoundingRectangle
        values = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
        if values[2] <= values[0] or values[3] <= values[1]:
            return None
        return values
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _relative_center(bounds: list[int], window_rect: list[int] | None) -> dict[str, float] | None:
    if not window_rect:
        return None
    left, top, right, bottom = window_rect
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    center_x = (bounds[0] + bounds[2]) / 2
    center_y = (bounds[1] + bounds[3]) / 2
    return {
        "x": round((center_x - left) / width, 6),
        "y": round((center_y - top) / height, 6),
    }


def _snapshot(element: Any, window_rect: list[int] | None) -> dict[str, Any] | None:
    if not element:
        return None
    control_type_id = _int_property(element, "CurrentControlType")
    control_type = CONTROL_TYPES.get(control_type_id, f"ControlType{control_type_id}")
    automation_id = _string_property(element, "CurrentAutomationId")
    class_name = _string_property(element, "CurrentClassName")
    framework_id = _string_property(element, "CurrentFrameworkId")
    name = _string_property(element, "CurrentName") if control_type in SAFE_NAME_TYPES else ""
    bounds = _bounds(element)
    locator: dict[str, Any] = {
        "control_type": control_type,
        "control_type_id": control_type_id,
    }
    if automation_id:
        locator["automation_id"] = automation_id
    if class_name:
        locator["class_name"] = class_name
    if framework_id:
        locator["framework_id"] = framework_id
    if name:
        locator["name"] = name
    if bounds:
        locator["recorded_relative_center"] = _relative_center(bounds, window_rect)

    # A generic Pane/Custom without another identity is not more stable than geometry.
    identity_fields = {"automation_id", "class_name", "name"} & locator.keys()
    if not identity_fields:
        return None
    return locator


def snapshot_at(x: int, y: int, window_rect: list[int] | None) -> dict[str, Any] | None:
    automation = _automation()
    if not automation or tagPOINT is None:
        return None
    try:
        return _snapshot(automation.ElementFromPoint(tagPOINT(int(x), int(y))), window_rect)
    except (AttributeError, OSError):
        return None


def snapshot_focused(window_rect: list[int] | None) -> dict[str, Any] | None:
    automation = _automation()
    if not automation:
        return None
    try:
        return _snapshot(automation.GetFocusedElement(), window_rect)
    except (AttributeError, OSError):
        return None


def _condition(automation: Any, locator: dict[str, Any]) -> Any:
    conditions: list[Any] = []
    if locator.get("automation_id"):
        conditions.append(
            automation.CreatePropertyCondition(
                UIA_AUTOMATION_ID_PROPERTY_ID, str(locator["automation_id"])
            )
        )
    if locator.get("name"):
        conditions.append(
            automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, str(locator["name"]))
        )
    if locator.get("class_name"):
        conditions.append(
            automation.CreatePropertyCondition(
                UIA_CLASS_NAME_PROPERTY_ID, str(locator["class_name"])
            )
        )
    if locator.get("control_type_id"):
        conditions.append(
            automation.CreatePropertyCondition(
                UIA_CONTROL_TYPE_PROPERTY_ID, int(locator["control_type_id"])
            )
        )
    if not conditions:
        return None
    combined = conditions[0]
    for next_condition in conditions[1:]:
        combined = automation.CreateAndCondition(combined, next_condition)
    return combined


def _distance_from_recorded(
    bounds: list[int],
    window_rect: list[int] | None,
    recorded: dict[str, float] | None,
) -> float:
    current = _relative_center(bounds, window_rect)
    if not current or not recorded:
        return 0.0
    return abs(current["x"] - float(recorded["x"])) + abs(
        current["y"] - float(recorded["y"])
    )


def find_bounds(
    hwnd: int,
    locator: dict[str, Any],
    window_rect: list[int] | None,
) -> list[int] | None:
    automation = _automation()
    if not automation or not hwnd:
        return None
    condition = _condition(automation, locator)
    if condition is None:
        return None
    try:
        root = automation.ElementFromHandle(hwnd)
        matches = root.FindAll(TREE_SCOPE_SUBTREE, condition)
        candidates: list[list[int]] = []
        for index in range(int(matches.Length)):
            bounds = _bounds(matches.GetElement(index))
            if bounds:
                candidates.append(bounds)
        if not candidates:
            return None
        recorded = locator.get("recorded_relative_center")
        return min(
            candidates,
            key=lambda item: _distance_from_recorded(item, window_rect, recorded),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


UIA_VALUE_PATTERN_ID = 10002
UIA_TOGGLE_PATTERN_ID = 10015
UIA_TEXT_PATTERN_ID = 10014

TOGGLE_STATES = {0: "off", 1: "on", 2: "indeterminate"}


def find_element(locator: dict[str, Any], hwnd: int | None = None) -> Any:
    """Find one element by locator, from a window or from the desktop root."""

    automation = _automation()
    if not automation:
        return None
    condition = _condition(automation, locator)
    if condition is None:
        return None
    try:
        root = (
            automation.ElementFromHandle(hwnd)
            if hwnd
            else automation.GetRootElement()
        )
        return root.FindFirst(TREE_SCOPE_SUBTREE, condition)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def element_value(locator: dict[str, Any], hwnd: int | None = None) -> str | None:
    """Readable value of a control: ValuePattern first, then Name.

    ValuePattern 是编辑框/地址栏的真实内容；Name 是无障碍标签。
    读不到返回 None，让上层把它记成"没验到"，而不是空字符串"验到了空值"。
    """

    element = find_element(locator, hwnd)
    if not element:
        return None
    try:
        pattern = element.GetCurrentPattern(UIA_VALUE_PATTERN_ID)
        if pattern:
            value = getattr(pattern.QueryInterface(_value_interface()), "CurrentValue", None)
            if value is not None:
                return str(value)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    name = _string_property(element, "CurrentName")
    return name or None


def toggle_state(locator: dict[str, Any], hwnd: int | None = None) -> str | None:
    """on / off / indeterminate —— 点赞、关注、收藏这类动作的真实成功判据。"""

    element = find_element(locator, hwnd)
    if not element:
        return None
    try:
        pattern = element.GetCurrentPattern(UIA_TOGGLE_PATTERN_ID)
        if not pattern:
            return None
        state = getattr(
            pattern.QueryInterface(_toggle_interface()), "CurrentToggleState", None
        )
        return TOGGLE_STATES.get(int(state)) if state is not None else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _value_interface() -> Any:
    from comtypes.gen.UIAutomationClient import IUIAutomationValuePattern

    return IUIAutomationValuePattern


def _toggle_interface() -> Any:
    from comtypes.gen.UIAutomationClient import IUIAutomationTogglePattern

    return IUIAutomationTogglePattern


def focus(hwnd: int, locator: dict[str, Any]) -> bool:
    automation = _automation()
    if not automation or not hwnd:
        return False
    condition = _condition(automation, locator)
    if condition is None:
        return False
    try:
        root = automation.ElementFromHandle(hwnd)
        element = root.FindFirst(TREE_SCOPE_SUBTREE, condition)
        if not element:
            return False
        element.SetFocus()
        return True
    except (AttributeError, OSError):
        return False
