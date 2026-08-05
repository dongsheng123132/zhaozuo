"""Stable Action ID derivation.

影核（ActionParity）要求每个业务动作有一个稳定且唯一的 Action ID。早期实现只从
目标描述里抽取 ASCII 单词，导致中文目标全部塌陷成同一个 ID：

    "给张总回复微信"     -> workflow.recorded_task
    "在Excel里做月度报表" -> workflow.excel
    "在Excel里做年度报表" -> workflow.excel   # 与上一条撞车

ID 撞车意味着两个不同的业务动作在协议层无法区分，档案库、回归记录和调用方
全部会认错对象。这里改成"可读前缀 + 目标摘要指纹"：前缀尽量保留人能看懂的
线索，指纹保证不同目标永远得到不同 ID。
"""

from __future__ import annotations

import re
import unicodedata
from hashlib import blake2s


#: Namespaced, lowercase, ASCII-only. 至少一个点，避免裸名字污染全局命名空间。
#: Action ID 是被调用的接口名，按函数命名习惯只收下划线。
ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

#: profile_id 是文档标识而非可调用接口，额外允许连字符。
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")

#: 指纹长度。6 个十六进制字符 = 24 bit，单个档案库里撞车概率可以忽略，
#: 又短到人能在界面上一眼扫过。
_DIGEST_CHARS = 6

_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_MAX_PREFIX_WORDS = 5
_MAX_PREFIX_CHARS = 40


def normalize_goal(goal: str) -> str:
    """Normalize a goal so cosmetic edits do not change the Action ID.

    统一 Unicode 形态（全角/半角）、压缩空白、去掉首尾空格。大小写保留：
    英文里 "Send Invoice" 和 "send invoice" 视为同一个动作。
    """

    folded = unicodedata.normalize("NFKC", goal or "").casefold()
    return re.sub(r"\s+", " ", folded).strip()


def goal_digest(goal: str, size: int = _DIGEST_CHARS) -> str:
    """Return a short, stable, cross-platform fingerprint of a goal.

    用 blake2s 而不是内置 hash()：内置 hash 对 str 加了每进程随机盐，
    同一个目标在两次运行里会得到不同的值，做不了稳定 ID。
    """

    normalized = normalize_goal(goal)
    digest_bytes = max(1, (size + 1) // 2)
    return blake2s(normalized.encode("utf-8"), digest_size=digest_bytes).hexdigest()[:size]


def slug_prefix(goal: str) -> str:
    """Human-readable ASCII hint extracted from the goal, may be empty."""

    words = _ASCII_WORD_RE.findall(normalize_goal(goal))
    if not words:
        return ""
    prefix = "_".join(words[:_MAX_PREFIX_WORDS])[:_MAX_PREFIX_CHARS].strip("_")
    # ID 段必须以字母开头，"2024_report" 这类要补前缀。
    return prefix if prefix[:1].isalpha() else f"t_{prefix}"


def action_slug(goal: str) -> str:
    """Unique, stable slug for one goal. Chinese-only goals included."""

    prefix = slug_prefix(goal)
    digest = goal_digest(goal)
    if not normalize_goal(goal):
        # 空目标没有可区分的语义，不假装它是一个独立动作。
        return "unnamed_action"
    return f"{prefix}_{digest}" if prefix else f"task_{digest}"


def demo_suffix(goal: str) -> str:
    """ID segment marking "this instance came from one recorded demonstration".

    ID 每一段必须以字母开头，而指纹可能以数字开头；前缀 d 同时解决语法问题和
    可读性问题 —— 看到 `wechat.reply_message.d8ab776` 就知道这是演示实例，
    而不是那个被承诺的规范接口 `wechat.reply_message`。
    """

    return f"d{goal_digest(goal)}"


def derive_action_id(goal: str, namespace: str = "workflow") -> str:
    """Derive a namespaced Action ID from a free-form business goal."""

    return f"{namespace}.{action_slug(goal)}"


def derive_profile_id(goal: str, namespace: str = "windows.zhaozuo") -> str:
    return f"{namespace}.{action_slug(goal)}"


def is_valid_action_id(action_id: str) -> bool:
    return bool(ACTION_ID_RE.fullmatch(action_id or ""))


def sanitize_action_id(action_id: str, fallback: str) -> str:
    """Accept a caller-supplied Action ID, or fall back when it is unusable.

    人工命名优先于自动派生 —— 协议里 Action ID 是一等字段，不是从描述里
    猜出来的副产品。但坏格式不能进档案，否则下游没法按 ID 索引。
    """

    candidate = (action_id or "").strip()
    return candidate if is_valid_action_id(candidate) else fallback
