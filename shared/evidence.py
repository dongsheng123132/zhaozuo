"""Success evidence engine.

v1 规范说 `validated` 档案不得只靠窗口标题证明成功，但执行器过去只实现了
`window.title_contains` —— 规范要求的证据，执行器一种都验不了。这个模块补上这段。

三条设计原则：

1. **验不了就是没验过。** 不支持的证据种类返回 ok=False 并注明原因，
   绝不因为"检查不了"而默认通过。默默放行比没有证据更危险 —— 它看起来像验过了。
2. **可无界面测试。** 所有系统访问经过 EvidenceProbe 接口注入。业务判断对不对，
   用假 probe 毫秒级断言；真机只用来验 probe 本身。截图猜按钮不是业务测试的地基。
3. **变化类证据需要基线。** "文件变了""内容更新了"必须在动作执行**之前**拍一次快照，
   否则无从判断变化是不是这次动作造成的。
"""

from __future__ import annotations

import fnmatch
import time
import unicodedata
from hashlib import blake2b
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit


#: 需要在动作执行前拍基线的证据种类。
BASELINE_KINDS = frozenset({"file.hash_changed", "file.modified_since"})

#: 已知但本执行器尚未实现的证据种类。它们是真证据，只是需要目标软件的领域知识。
#: 列在这里而不是悄悄跳过 —— 缺口要看得见才会被补上。
UNSUPPORTED_KINDS: dict[str, str] = {
    "message.outgoing_visible": "需要目标聊天软件的会话流读取，尚未实现",
    "publication.status": "需要目标发布平台的状态查询，尚未实现",
}


class EvidenceProbe(Protocol):
    """The only surface through which evidence touches the live system."""

    def window_titles(self) -> list[str]: ...

    def window_matches(self, locator: dict[str, Any]) -> int: ...

    def process_names(self) -> set[str]: ...

    def clipboard_text(self) -> str: ...

    def uia_value(self, locator: dict[str, Any]) -> str | None: ...

    def uia_exists(self, locator: dict[str, Any]) -> bool: ...

    def uia_toggle_state(self, locator: dict[str, Any]) -> str | None: ...


def _normalize(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value if value is not None else "")).casefold()


def _file_digest(path: Path) -> str | None:
    try:
        return blake2b(path.read_bytes(), digest_size=16).hexdigest()
    except OSError:
        return None


def _baseline_key(spec: dict[str, Any]) -> str:
    return f"{spec.get('kind')}::{spec.get('path', '')}"


def capture_baseline(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Snapshot the world *before* the action runs.

    只对确实需要"变化"语义的证据拍快照。没有基线的 file.hash_changed 无法区分
    "这次动作改了文件"和"文件本来就长这样"。
    """

    baseline: dict[str, Any] = {"captured_at": time.time()}
    for spec in evidence:
        if not isinstance(spec, dict) or spec.get("kind") not in BASELINE_KINDS:
            continue
        path_text = str(spec.get("path") or "").strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser()
        baseline[_baseline_key(spec)] = {
            "exists": path.is_file(),
            "digest": _file_digest(path) if path.is_file() else None,
            "mtime": path.stat().st_mtime if path.is_file() else None,
        }
    return baseline


def _result(
    spec: dict[str, Any],
    ok: bool,
    observed: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "kind": spec.get("kind"),
        "ok": bool(ok),
        "expected": spec.get("expected"),
        "observed": observed,
        "reason": reason,
    }


def _check_window_title_contains(spec, probe, _baseline):
    expected = _normalize(spec.get("expected"))
    if not expected:
        return _result(spec, False, None, "window.title_contains 缺少 expected")
    matched = next(
        (title for title in probe.window_titles() if expected in _normalize(title)), ""
    )
    return _result(spec, bool(matched), matched, "" if matched else "没有窗口标题包含该文字")


def _check_window_exists(spec, probe, _baseline):
    locator = spec.get("window") or spec.get("locator") or {}
    if spec.get("process") and not locator.get("process"):
        locator = {**locator, "process": spec["process"]}
    if not locator:
        return _result(spec, False, None, "window.exists 缺少窗口定位器")
    count = probe.window_matches(locator)
    return _result(spec, count > 0, count, "" if count else "找不到匹配的窗口")


def _check_process_running(spec, probe, _baseline):
    wanted = _normalize(spec.get("expected") or spec.get("process"))
    if not wanted:
        return _result(spec, False, None, "process.running 缺少进程名")
    running = {_normalize(name) for name in probe.process_names()}
    hit = wanted in running or any(fnmatch.fnmatch(name, wanted) for name in running)
    return _result(spec, hit, wanted if hit else None, "" if hit else "进程未在运行")


def _resolved_path(spec: dict[str, Any]) -> Path | None:
    text = str(spec.get("path") or spec.get("expected") or "").strip()
    return Path(text).expanduser() if text else None


def _check_file_exists(spec, _probe, _baseline):
    path = _resolved_path(spec)
    if not path:
        return _result(spec, False, None, "file.exists 缺少 path")
    exists = path.is_file()
    return _result(spec, exists, str(path), "" if exists else "文件不存在")


def _check_file_size_greater_than(spec, _probe, _baseline):
    path = _resolved_path(spec)
    if not path or not path.is_file():
        return _result(spec, False, None, "文件不存在，无法比较大小")
    try:
        minimum = int(spec.get("expected") or 0)
    except (TypeError, ValueError):
        return _result(spec, False, None, "expected 不是整数字节数")
    size = path.stat().st_size
    return _result(spec, size > minimum, size, "" if size > minimum else f"仅 {size} 字节")


def _check_file_hash_changed(spec, _probe, baseline):
    path = _resolved_path(spec)
    if not path:
        return _result(spec, False, None, "file.hash_changed 缺少 path")
    before = baseline.get(_baseline_key(spec))
    if before is None:
        # 没有基线就无法归因。这不是"通过"，是"没验"。
        return _result(spec, False, None, "缺少执行前基线，无法判断文件是否因本次动作而变")
    if not path.is_file():
        return _result(spec, False, None, "文件不存在")
    now_digest = _file_digest(path)
    changed = now_digest is not None and now_digest != before.get("digest")
    return _result(
        spec, changed, now_digest, "" if changed else "文件内容与执行前一致"
    )


def _check_file_modified_since(spec, _probe, baseline):
    path = _resolved_path(spec)
    if not path:
        return _result(spec, False, None, "file.modified_since 缺少 path")
    before = baseline.get(_baseline_key(spec))
    if before is None:
        return _result(spec, False, None, "缺少执行前基线，无法判断修改时间是否推进")
    if not path.is_file():
        return _result(spec, False, None, "文件不存在")
    mtime = path.stat().st_mtime
    previous = before.get("mtime")
    moved = previous is None or mtime > previous
    return _result(spec, moved, mtime, "" if moved else "修改时间未推进")


def _check_clipboard_contains(spec, probe, _baseline):
    expected = _normalize(spec.get("expected"))
    if not expected:
        return _result(spec, False, None, "clipboard.contains 缺少 expected")
    text = probe.clipboard_text() or ""
    hit = expected in _normalize(text)
    # 剪贴板可能含敏感内容，只回报是否命中与长度，不回显原文。
    return _result(spec, hit, f"<{len(text)} chars>", "" if hit else "剪贴板中没有该文字")


def _check_uia_element_exists(spec, probe, _baseline):
    locator = spec.get("locator") or {}
    if not locator:
        return _result(spec, False, None, "uia.element_exists 缺少 locator")
    exists = probe.uia_exists(locator)
    return _result(spec, exists, exists, "" if exists else "找不到该控件")


def _check_uia_value_equals(spec, probe, _baseline):
    locator = spec.get("locator") or {}
    if not locator:
        return _result(spec, False, None, "uia.value_equals 缺少 locator")
    value = probe.uia_value(locator)
    if value is None:
        return _result(spec, False, None, "控件不存在或没有可读值")
    ok = _normalize(value) == _normalize(spec.get("expected"))
    return _result(spec, ok, value, "" if ok else "控件值与期望不符")


def _check_uia_text_contains(spec, probe, _baseline):
    locator = spec.get("locator") or {}
    if not locator:
        return _result(spec, False, None, "uia.text_contains 缺少 locator")
    value = probe.uia_value(locator)
    if value is None:
        return _result(spec, False, None, "控件不存在或没有可读值")
    ok = _normalize(spec.get("expected")) in _normalize(value)
    return _result(spec, ok, value, "" if ok else "控件文本不含期望内容")


def _check_control_toggle_state(spec, probe, _baseline):
    """点赞/关注/收藏这类动作真正的成功判据 —— 控件被切到了 on。"""

    locator = spec.get("locator") or {}
    if not locator:
        return _result(spec, False, None, "control.toggle_state 缺少 locator")
    state = probe.uia_toggle_state(locator)
    if state is None:
        return _result(spec, False, None, "控件不存在或不支持 Toggle")
    expected = _normalize(spec.get("expected") or "on")
    ok = _normalize(state) == expected
    return _result(spec, ok, state, "" if ok else f"控件状态为 {state}")


def _split_url(text: Any) -> tuple[str, str, str] | None:
    """(host, path, query)。地址栏普遍省略 scheme，所以 scheme 不参与比较。"""

    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "//" + raw.lstrip("/")
    parsed = urlsplit(raw)
    host = parsed.netloc.casefold()
    if "@" in host:
        # userinfo 是经典的伪装位：evil.com@real.com 真正去的是 real.com。
        host = host.split("@", 1)[1]
    host = host.removeprefix("www.")
    if not host:
        return None
    return host, parsed.path, parsed.query


def _check_browser_address_matches(spec, probe, _baseline):
    locator = spec.get("locator") or {}
    if not locator:
        return _result(
            spec, False, None,
            "browser.address_matches 需要地址栏控件 locator（不同浏览器不一样，本执行器不猜）",
        )
    value = probe.uia_value(locator)
    if value is None:
        return _result(spec, False, None, "读不到地址栏内容")

    want = _split_url(spec.get("expected"))
    if want is None:
        return _result(spec, False, value, "browser.address_matches 缺少可解析的 expected")
    got = _split_url(value)
    if got is None:
        return _result(spec, False, value, "地址栏内容不是可解析的网址")

    if want[0] != got[0]:
        # 过去这里是子串比较，于是期望 example.com 会被
        # example.com.attacker.io 命中 —— 主机必须整段相等。
        return _result(spec, False, value, f"主机不符：期望 {want[0]}，实际 {got[0]}")

    want_path, got_path = want[1], got[1]
    if str(spec.get("normalization") or "") == "ignore_trailing_slash":
        want_path, got_path = want_path.rstrip("/"), got_path.rstrip("/")
    # 期望没写路径 = 只要求落在这个站点上；写了就必须一致。
    if want_path not in ("", "/") and want_path != got_path:
        return _result(spec, False, value, f"路径不符：期望 {want_path}，实际 {got_path}")
    if want[2] and want[2] != got[2]:
        return _result(spec, False, value, "查询串与期望不符")
    return _result(spec, True, value, "")


CHECKERS = {
    "window.title_contains": _check_window_title_contains,
    "window.exists": _check_window_exists,
    "process.running": _check_process_running,
    "file.exists": _check_file_exists,
    "file.size_greater_than": _check_file_size_greater_than,
    "file.hash_changed": _check_file_hash_changed,
    "file.modified_since": _check_file_modified_since,
    "clipboard.contains": _check_clipboard_contains,
    "uia.element_exists": _check_uia_element_exists,
    "uia.value_equals": _check_uia_value_equals,
    "uia.text_contains": _check_uia_text_contains,
    "control.toggle_state": _check_control_toggle_state,
    "browser.address_matches": _check_browser_address_matches,
}


def check_one(
    spec: dict[str, Any],
    probe: EvidenceProbe,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = str(spec.get("kind") or "")
    if kind in UNSUPPORTED_KINDS:
        return _result(spec, False, None, f"未实现的证据种类：{UNSUPPORTED_KINDS[kind]}")
    checker = CHECKERS.get(kind)
    if checker is None:
        return _result(spec, False, None, f"未知的证据种类：{kind or '(空)'}")
    try:
        return checker(spec, probe, baseline or {})
    except Exception as exc:  # 证据检查失败 == 未通过，绝不吞掉当成通过
        return _result(spec, False, None, f"证据检查出错：{exc}")


def check_all(
    evidence: list[dict[str, Any]],
    probe: EvidenceProbe,
    baseline: dict[str, Any] | None = None,
    *,
    wait: Any = None,
    timeout_default_ms: int = 5000,
) -> list[dict[str, Any]]:
    """Check every declared evidence, retrying each until its own timeout.

    `wait(seconds)` 由调用方注入，使执行器能在等待期间响应 Esc 中止；
    不传则不重试（无界面测试用这条路径，毫秒级完成）。
    """

    results: list[dict[str, Any]] = []
    for spec in evidence:
        if not isinstance(spec, dict):
            results.append({"kind": None, "ok": False, "reason": "证据必须是对象"})
            continue
        result = check_one(spec, probe, baseline)
        if not result["ok"] and wait is not None:
            timeout = int(spec.get("timeout_ms", timeout_default_ms)) / 1000
            deadline = time.monotonic() + max(timeout, 0.0)
            while not result["ok"] and time.monotonic() < deadline:
                wait(0.15)
                result = check_one(spec, probe, baseline)
        results.append(result)
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall verdict. 空证据不算成功 —— 没有证据就是没有证明。"""

    passed = [item for item in results if item.get("ok")]
    failed = [item for item in results if not item.get("ok")]
    return {
        "ok": bool(results) and not failed,
        "checked": len(results),
        "passed": len(passed),
        "failed": [
            {"kind": item.get("kind"), "reason": item.get("reason")} for item in failed
        ],
        "reason": (
            "" if results and not failed
            else "没有声明任何成功证据" if not results
            else f"{len(failed)}/{len(results)} 条证据未通过"
        ),
    }
