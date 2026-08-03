"""Outward-effect classification (风险守卫).

一个动作是否"对外产生了不可撤销的影响"，决定了回放要不要停在最后一步等人确认。
早期实现只匹配一小张中文关键词表（"发送""点赞""公众号"），于是：

    "把这条消息发给客户群"        -> 不守卫（表里只有"发送"，没有"发给"）
    "reply to boss on WeChat"    -> 不守卫（整表没有英文）
    钉钉 / 飞书 / QQ / Outlook    -> 不守卫（兜底只认微信窗口标题）

守卫依赖用户怎么措辞，是安全设计上最不该有的依赖。这里改成三路证据 + fail-closed：

1. **声明意图** —— 目标描述里的动词（中英双语，覆盖发送/发布/互动/转发/支付/删除）；
2. **应用族**   —— 最后一步落在哪个软件里（进程名 > 窗口类名 > 标题），
   通讯、社交、邮件、支付类软件里的终态操作**默认就是对外动作**，
   不需要用户在目标描述里说对词；
3. **控件身份** —— 最后一步点的控件叫什么（UIA name），"发送/Send/送信/Publier"。

任意一路命中即守卫。三路都没命中才放行，且放行结论会带上理由，可被审阅。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


# --------------------------------------------------------------------------
# 效果分类
# --------------------------------------------------------------------------

#: kind -> (默认中文标签, 通用 Action ID)。具体软件的语义 ID 在 _SPECIFIC_ACTIONS 里覆盖。
EFFECT_KINDS: dict[str, tuple[str, str]] = {
    "send_message": ("发送消息", "message.send"),
    "publish_content": ("发布内容", "content.publish"),
    "social_reaction": ("社交互动", "social.react"),
    "share_content": ("转发分享", "content.share"),
    "payment": ("资金操作", "payment.transfer"),
    "destructive": ("删除或清空", "data.destroy"),
    "external_effect": ("最终对外动作", "external.effect"),
}


def _pattern(*fragments: str) -> re.Pattern[str]:
    return re.compile("|".join(fragments))


#: 目标描述里的意图动词。中文不加词边界（无空格分词），英文加 \b 防止误伤。
INTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "payment",
        _pattern(
            "支付", "付款", "转账", "汇款", "打款", "下单", "充值", "提现", "结算", "报销",
            r"\bpay\b", r"\bpayment\b", r"\bcheckout\b", r"\btransfer\s+money\b",
            r"\bwire\b", r"\bplace\s+(?:an\s+)?order\b", r"\bwithdraw\b",
        ),
    ),
    (
        "destructive",
        _pattern(
            "删除", "移除", "清空", "销毁", "注销", "撤销", "作废", "格式化",
            r"\bdelete\b", r"\bremove\b", r"\bwipe\b", r"\bpurge\b",
            r"\bdrop\s+(?:table|database)\b", r"\bdeactivate\b",
        ),
    ),
    (
        "publish_content",
        _pattern(
            "发布", "发表", "发文", "投稿", "上架", "上传作品", "推送", "群发", "提交订单",
            "公众号", "发动态", "发朋友圈", "发微博", "发帖",
            r"\bpublish\b", r"\bpost\b", r"\btweet\b", r"\bsubmit\b",
            r"\bupload\b", r"\brelease\b", r"\bbroadcast\b",
        ),
    ),
    (
        "share_content",
        _pattern(
            "转发", "分享", "转载", "@全体", "艾特",
            r"\bshare\b", r"\bforward\b", r"\bretweet\b", r"\brepost\b",
        ),
    ),
    (
        "social_reaction",
        _pattern(
            "点赞", "点个赞", "喜欢", "关注", "收藏", "投票", "评论", "留言", "打赏",
            r"\blike\b", r"\bfollow\b", r"\bfavorite\b", r"\bupvote\b",
            r"\bcomment\b", r"\bsubscribe\b", r"\btip\b",
        ),
    ),
    (
        "send_message",
        _pattern(
            "发送", "发出", "发给", "发到", "发条", "发个", "回复", "回消息", "回信",
            "私信", "催一下", "通知一下", "告诉",
            r"\bsend\b", r"\breply\b", r"\brespond\b", r"\bmessage\b",
            r"\bdm\b", r"\bnotify\b", r"\bemail\b",
        ),
    ),
)


#: (应用族, 效果 kind)。落在这些软件里的终态操作默认对外，与措辞无关。
OUTWARD_FAMILIES: dict[str, str] = {
    "messaging": "send_message",
    "mail": "send_message",
    "social": "publish_content",
    "finance": "payment",
}

#: 进程名 -> 应用族。进程名是最可靠的身份，优先于类名和标题。
PROCESS_FAMILIES: dict[str, str] = {
    "wechat.exe": "messaging",
    "weixin.exe": "messaging",
    "wechatapp.exe": "messaging",
    "wxwork.exe": "messaging",
    "dingtalk.exe": "messaging",
    "feishu.exe": "messaging",
    "lark.exe": "messaging",
    "qq.exe": "messaging",
    "tim.exe": "messaging",
    "telegram.exe": "messaging",
    "slack.exe": "messaging",
    "whatsapp.exe": "messaging",
    "discord.exe": "messaging",
    "outlook.exe": "mail",
    "hxoutlook.exe": "mail",
    "thunderbird.exe": "mail",
    "foxmail.exe": "mail",
    "emclient.exe": "mail",
    "alipay.exe": "finance",
    "netsign.exe": "finance",
}

#: 窗口类名 -> 应用族。进程名拿不到时（旧档案、权限不足）的次选证据。
CLASS_FAMILIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^WeChatMainWndForPC$", re.I), "messaging"),
    (re.compile(r"^WeWorkWindow", re.I), "messaging"),
    (re.compile(r"^StandardFrame_DingTalk", re.I), "messaging"),
    (re.compile(r"^TXGuiFoundation$", re.I), "messaging"),
    (re.compile(r"^rctrl_renwnd32$", re.I), "mail"),  # Outlook classic
    (re.compile(r"^MozillaWindowClass$", re.I), ""),  # 浏览器/雷鸟，靠标题再判
)

#: 浏览器进程 —— 本身无所谓，危险的是它开着哪个站点。
BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe",
    "360se.exe", "360chrome.exe", "qqbrowser.exe", "sogouexplorer.exe",
    "iexplore.exe", "safari.exe",
}

#: 窗口标题里的站点线索 -> 应用族。浏览器里的社交/邮件/网银同样是对外动作。
TITLE_FAMILIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_pattern("公众号", "mp.weixin", "微博", "小红书", "抖音", "快手", "知乎", "b站", "哔哩",
              "小红書", r"\btwitter\b", r"\bx\.com\b", r"\bfacebook\b", r"\blinkedin\b",
              r"\binstagram\b", r"\breddit\b", r"\byoutube\s+studio\b", r"\btiktok\b"), "social"),
    (_pattern("网上银行", "网银", "支付宝", "云闪付", "收银台", "订单确认", "确认支付",
              r"\bonline\s+banking\b", r"\bpaypal\b", r"\bstripe\b", r"\bcheckout\b"), "finance"),
    (_pattern("邮箱", "邮件", r"\bgmail\b", r"\boutlook\b", r"\bmail\b"), "mail"),
    (_pattern("微信", r"\bwechat\b", r"\bweixin\b", "钉钉", r"\bdingtalk\b", "飞书",
              r"\blark\b", "企业微信", r"\btelegram\b", r"\bslack\b", r"\bwhatsapp\b"), "messaging"),
)

#: 控件名（UIA name）里的对外动作。点到叫这个名字的按钮，无论在哪个软件都要守卫。
CONTROL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("payment", _pattern("确认支付", "立即支付", "确认付款", "立即购买", "确认转账", "提交订单",
                         r"\bpay\s+now\b", r"\bconfirm\s+payment\b", r"\bplace\s+order\b")),
    ("destructive", _pattern("^删除$", "^移除$", "永久删除", "清空", r"^\s*delete\s*$", r"^\s*remove\s*$")),
    ("publish_content", _pattern("发布", "发表", "^提交$", "^投稿$", "群发",
                                 r"\bpublish\b", r"^\s*post\s*$", r"^\s*submit\s*$", r"\btweet\b")),
    ("share_content", _pattern("转发", "分享", r"\bshare\b", r"\bforward\b", r"\bretweet\b")),
    ("social_reaction", _pattern("^赞$", "点赞", "^关注$", "^收藏$", r"^\s*like\s*$", r"^\s*follow\b")),
    ("send_message", _pattern("^发送", "发送\\(", "送信", r"^\s*send\b", r"^\s*reply\b")),
)

#: 具体软件 + 具体动作的语义 Action ID。比通用 ID 更有价值，尽量命中。
_SPECIFIC_ACTIONS: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (_pattern("抖音", r"\bdouyin\b", r"\btiktok\b"), "social_reaction",
     "douyin.like_video", "点赞这条抖音视频"),
    (_pattern("公众号", "mp.weixin"), "publish_content",
     "wechat_official.publish_article", "发布公众号文章"),
    (_pattern("微博", r"\bweibo\b"), "publish_content", "weibo.publish_post", "发布微博"),
    (_pattern("朋友圈"), "publish_content", "wechat.publish_moment", "发布朋友圈"),
    (_pattern("微信", r"\bwechat\b", r"\bweixin\b"), "send_message",
     "wechat.reply_message", "发送微信回复"),
    (_pattern("钉钉", r"\bdingtalk\b"), "send_message", "dingtalk.send_message", "发送钉钉消息"),
    (_pattern("飞书", r"\blark\b", r"\bfeishu\b"), "send_message", "feishu.send_message", "发送飞书消息"),
    (_pattern("邮件", "邮箱", r"\bemail\b", r"\bmail\b"), "send_message", "mail.send_message", "发送邮件"),
)


def _normalize(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _window_of(event: dict[str, Any]) -> dict[str, Any]:
    window = event.get("window")
    return window if isinstance(window, dict) else {}


def app_family(window: dict[str, Any]) -> tuple[str, str]:
    """Identify the application family behind a window.

    Returns (family, evidence). family 为空表示无法判定 —— 这本身是重要信息，
    调用方不应把"判不出来"当成"安全"。
    """

    process = _normalize(window.get("process")).strip()
    title = _normalize(window.get("title"))
    class_name = str(window.get("class_name") or "")

    if process and process in PROCESS_FAMILIES:
        return PROCESS_FAMILIES[process], f"process={process}"

    for pattern, family in CLASS_FAMILIES:
        if family and pattern.search(class_name):
            return family, f"class_name={class_name}"

    # 浏览器：进程名不说明问题，看它开着什么站点。
    for pattern, family in TITLE_FAMILIES:
        if pattern.search(title):
            evidence = f"browser_title~{family}" if process in BROWSER_PROCESSES else f"title~{family}"
            return family, evidence

    return "", f"unknown_app(process={process or '?'}, class={class_name or '?'})"


def _specific_action(text: str, kind: str) -> tuple[str, str] | None:
    for pattern, specific_kind, action_id, label in _SPECIFIC_ACTIONS:
        if specific_kind == kind and pattern.search(text):
            return action_id, label
    return None


def _decision(
    kind: str,
    reasons: list[str],
    confidence: str,
    context_text: str = "",
) -> dict[str, Any]:
    label, action_id = EFFECT_KINDS.get(kind, EFFECT_KINDS["external_effect"])
    specific = _specific_action(context_text, kind)
    if specific:
        action_id, label = specific
    return {
        "type": "representational_communication",
        "kind": kind,
        "label": label,
        "confirmation": "always",
        "action_id": action_id,
        "confidence": confidence,
        "reasons": reasons,
    }


def classify_effect(goal: str) -> dict[str, Any] | None:
    """Classify the *declared* intent only. 返回 None 只代表描述里没说，不代表安全。"""

    text = _normalize(goal)
    if not text:
        return None
    for kind, pattern in INTENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return _decision(kind, [f"goal~{match.group(0).strip()}"], "declared", text)
    return None


def _terminal_control_effect(event: dict[str, Any]) -> dict[str, Any] | None:
    uia = event.get("uia")
    name = _normalize(uia.get("name")) if isinstance(uia, dict) else ""
    if not name:
        return None
    for kind, pattern in CONTROL_PATTERNS:
        if pattern.search(name):
            return _decision(kind, [f"control_name~{name}"], "control", name)
    return None


def _is_terminal_commit(event: dict[str, Any]) -> bool:
    """Whether the final recorded gesture is the kind that commits something."""

    kind = event.get("kind")
    if kind == "keyboard.press":
        return str(event.get("key", "")).upper() in {"ENTER", "TAB"}
    if kind == "keyboard.shortcut":
        keys = {str(key).upper() for key in event.get("keys", [])}
        return "ENTER" in keys or ("CTRL" in keys and "ENTER" in keys)
    return kind == "pointer.click"


def infer_effect(
    goal: str,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Decide whether the recorded action ends in an outward effect.

    三路证据任一命中即守卫。声明意图优先（它带着用户真实想做的事），
    应用族与控件身份负责兜住用户没说对词的情况。
    """

    declared = classify_effect(goal)
    if declared:
        return declared
    if not events:
        return None

    final = events[-1]
    control_effect = _terminal_control_effect(final)
    if control_effect:
        return control_effect

    window = _window_of(final)
    family, evidence = app_family(window)
    if family in OUTWARD_FAMILIES and _is_terminal_commit(final):
        kind = OUTWARD_FAMILIES[family]
        context = f"{_normalize(goal)} {_normalize(window.get('title'))}"
        return _decision(kind, [f"app_family={family}", evidence], "app_family", context)
    return None


def assess(goal: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Full, auditable verdict. 界面用它解释"为什么要（不要）确认"。"""

    effect = infer_effect(goal, events)
    if effect:
        return {"guarded": True, **effect}

    window = _window_of(events[-1]) if events else {}
    family, evidence = app_family(window)
    return {
        "guarded": False,
        "kind": None,
        "confidence": "none",
        "reasons": [
            "goal~no_outward_verb",
            f"app_family={family or 'unknown'}",
            evidence,
        ],
    }
