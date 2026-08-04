from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from desktop_app import dpi as dpi_module
from desktop_app.capture import EventRecorder, ImageGrab
from desktop_app.replay import ReplayEngine
from desktop_app.workflow import (
    build_profile,
    classify_effect,
    merge_recording_segments,
    save_recording,
)
from shared.profile import ProfileError
from shared.runtime import data_root


RECORDINGS = data_root() / "recordings"

BG = "#0b1020"
CARD = "#151c30"
CARD_2 = "#1d2740"
TEXT = "#f4f7fb"
MUTED = "#98a5bd"
GREEN = "#35d0a0"
GREEN_DARK = "#174d43"
RED = "#ff5d6c"
AMBER = "#ffca5c"
FONT = "Microsoft YaHei UI"


class ZhaozuoApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        # 进程声明了 per-monitor 感知，窗口尺寸从此是物理像素 —— 界面必须自己按
        # 缩放放大，否则在 125%/150% 的机器上会比以前小一圈。
        self.scale = dpi_module.scale_for(
            dpi_module.window_dpi(self.root.winfo_id()) or dpi_module.system_dpi()
        )
        self.root.tk.call("tk", "scaling", self.scale * 96 / 72)
        self.root.title("照做")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.geometry(self._default_pill_geometry())

        self.state = "idle"
        self.started_at = 0.0
        self.session_id = ""
        self.session_dir: Path | None = None
        self.recorder: EventRecorder | None = None
        self.recorded_events: list[dict] = []
        self.recording_mode = "new"
        self.current_segment_index = 0
        self.profile: dict | None = None
        self.paths: dict[str, Path] = {}
        self.replay = ReplayEngine()
        self.pending_effect_step_id: str | None = None
        self.pending_effect_label = ""
        self.pending_target_fingerprint = ""
        self.worker_messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._drag_origin: tuple[int, int, int, int] | None = None

        self._build_pill()
        self._build_dashboard()
        self._restore_latest_recording()
        self._refresh_pill()
        self.root.after(250, self._tick)

    def _px(self, value: float) -> int:
        """Logical pixels → physical pixels for this display."""

        return round(value * self.scale)

    def _default_pill_geometry(self) -> str:
        width, height = self._px(184), self._px(56)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        return f"{width}x{height}+{screen_w - width - 24}+{screen_h // 2 - 28}"

    def _build_pill(self) -> None:
        self.pill = tk.Canvas(
            self.root,
            width=self._px(184),
            height=self._px(56),
            bg=BG,
            highlightthickness=0,
            cursor="hand2",
        )
        self.pill.pack(fill="both", expand=True)
        self.pill.bind("<ButtonPress-1>", self._pill_press)
        self.pill.bind("<B1-Motion>", self._pill_drag)
        self.pill.bind("<ButtonRelease-1>", self._pill_release)
        self.pill.bind("<Button-3>", self._show_menu)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="打开任务面板", command=self.show_dashboard)
        self.menu.add_separator()
        self.menu.add_command(label="退出照做", command=self.quit)

    def _rounded_rect(
        self, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: object
    ) -> int:
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return self.pill.create_polygon(points, smooth=True, **kwargs)

    def _refresh_pill(self) -> None:
        self.pill.delete("all")
        color = RED if self.state == "recording" else GREEN
        if self.state == "executing":
            color = AMBER
        px = self._px
        self._rounded_rect(px(2), px(2), px(182), px(54), px(18),
                           fill=CARD, outline="#303b58", width=1)
        self.pill.create_oval(px(14), px(16), px(38), px(40), fill=color, outline="")
        if self.state == "recording":
            elapsed = int(time.monotonic() - self.started_at)
            label = f"停止 · {elapsed // 60:02d}:{elapsed % 60:02d}"
            sub = f"已捕获 {self.recorder.event_count if self.recorder else 0} 步"
        elif self.state == "executing":
            label = "停止执行"
            sub = "Esc 也可随时停止"
        else:
            label = "开始演示"
            sub = "你做一遍，它照做"
        self.pill.create_text(
            px(50),
            px(22),
            text=label,
            anchor="w",
            fill=TEXT,
            font=(FONT, 11, "bold"),
        )
        self.pill.create_text(
            px(50),
            px(39),
            text=sub,
            anchor="w",
            fill=MUTED,
            font=(FONT, 8),
        )
        self.pill.create_text(px(168), px(28), text="⋮", fill=MUTED, font=(FONT, 16))

    def _pill_press(self, event: tk.Event) -> None:
        self._drag_origin = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
        )

    def _pill_drag(self, event: tk.Event) -> None:
        if not self._drag_origin:
            return
        start_x, start_y, window_x, window_y = self._drag_origin
        dx, dy = event.x_root - start_x, event.y_root - start_y
        if abs(dx) + abs(dy) > 4:
            self.root.geometry(f"+{window_x + dx}+{window_y + dy}")

    def _pill_release(self, event: tk.Event) -> None:
        if not self._drag_origin:
            return
        start_x, start_y, _window_x, _window_y = self._drag_origin
        moved = abs(event.x_root - start_x) + abs(event.y_root - start_y)
        self._drag_origin = None
        if moved <= 4:
            self._pill_action()

    def _show_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _build_dashboard(self) -> None:
        self.dashboard = tk.Toplevel(self.root)
        self.dashboard.title("照做 · 任务工作台")
        self.dashboard.geometry(f"{self._px(640)}x{self._px(760)}")
        self.dashboard.minsize(self._px(580), self._px(660))
        self.dashboard.configure(bg=BG)
        self.dashboard.protocol("WM_DELETE_WINDOW", self.dashboard.withdraw)

        container = tk.Frame(self.dashboard, bg=BG, padx=24, pady=20)
        container.pack(fill="both", expand=True)

        tk.Label(
            container,
            text="照做",
            bg=BG,
            fg=TEXT,
            font=(FONT, 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            container,
            text="Windows 兼容动作学习器 · Powered by ActionParity",
            bg=BG,
            fg=MUTED,
            font=(FONT, 10),
        ).pack(anchor="w", pady=(0, 18))

        goal_card = tk.Frame(container, bg=CARD, padx=16, pady=14)
        goal_card.pack(fill="x")
        self._field_label(goal_card, "这次要教它完成什么？")
        self.goal_var = tk.StringVar(value="在目标软件中完成一个可验证任务")
        self.goal_entry = self._entry(goal_card, self.goal_var)
        self.goal_entry.pack(fill="x", pady=(6, 12))

        self._field_label(goal_card, "成功标志（回放后，任一窗口标题包含这段文字）")
        self.evidence_var = tk.StringVar()
        self.evidence_entry = self._entry(goal_card, self.evidence_var)
        self.evidence_entry.pack(fill="x", pady=(6, 10))

        self.screenshot_var = tk.BooleanVar(value=False)
        screenshot = tk.Checkbutton(
            goal_card,
            text="保存点击时截图（可能包含敏感信息，默认关闭）",
            variable=self.screenshot_var,
            bg=CARD,
            fg=MUTED,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD_2,
            font=(FONT, 9),
        )
        screenshot.pack(anchor="w")
        if ImageGrab is None:
            screenshot.configure(state="disabled", text="保存步骤截图（未安装 Pillow）")

        self.effect_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            goal_card,
            text="最后一步会发送 / 发布 / 点赞（执行到按钮前必须再次确认）",
            variable=self.effect_var,
            bg=CARD,
            fg=AMBER,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD_2,
            font=(FONT, 9),
        ).pack(anchor="w")

        control_row = tk.Frame(goal_card, bg=CARD)
        control_row.pack(fill="x", pady=(12, 0))
        self.record_button = self._button(
            control_row, "开始演示", self._pill_action, GREEN, "#08251d"
        )
        self.record_button.pack(side="left")
        self.append_button = self._button(
            control_row, "继续补录", self.continue_recording, CARD_2, TEXT
        )
        self.append_button.pack(side="left", padx=(8, 0))
        self.append_button.configure(state="disabled")
        self.status_var = tk.StringVar(value="准备就绪 · 输入正文始终遮蔽")
        tk.Label(
            control_row,
            textvariable=self.status_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(side="left", padx=14)

        learned_card = tk.Frame(container, bg=CARD, padx=16, pady=14)
        learned_card.pack(fill="both", expand=True, pady=14)
        header = tk.Frame(learned_card, bg=CARD)
        header.pack(fill="x")
        self._field_label(header, "理解出的动作草案").pack(side="left")
        tk.Label(
            header,
            text="当前为本地规则提炼 · AI 修复接口待接入",
            bg=CARD,
            fg=AMBER,
            font=(FONT, 8),
        ).pack(side="right")

        self.steps_text = tk.Text(
            learned_card,
            height=4,
            bg="#0f1628",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=10,
            pady=10,
            font=(FONT, 9),
            state="disabled",
        )
        self.steps_text.pack(fill="both", expand=True, pady=(10, 8))
        self._set_text(self.steps_text, "录制结束后，这里会出现语义步骤和可变输入。")

        self._field_label(learned_card, "本次回放输入（每行 name=value，录制原文不会保存）")
        self.inputs_text = tk.Text(
            learned_card,
            height=2,
            bg="#0f1628",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=10,
            pady=8,
            font=("Cascadia Mono", 9),
        )
        self.inputs_text.pack(fill="x", pady=(6, 0))

        replay_card = tk.Frame(container, bg=CARD, padx=16, pady=14)
        replay_card.pack(fill="x")
        row = tk.Frame(replay_card, bg=CARD)
        row.pack(fill="x")
        self.plan_button = self._button(row, "检查计划", self.check_plan, CARD_2, TEXT)
        self.plan_button.pack(side="left")
        self.execute_button = self._button(
            row, "真实执行", self.execute_workflow, RED, "#2c0910"
        )
        self.execute_button.pack(side="left", padx=8)
        self.confirm_var = tk.BooleanVar(value=False)
        self.confirm_check = tk.Checkbutton(
            row,
            text="我已检查并允许真实键鼠操作",
            variable=self.confirm_var,
            bg=CARD,
            fg=MUTED,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD_2,
            font=(FONT, 8),
        )
        self.confirm_check.pack(side="left", padx=4)
        self.report_var = tk.StringVar(value="执行结果会在这里显示")
        tk.Label(
            replay_card,
            textvariable=self.report_var,
            bg=CARD,
            fg=MUTED,
            justify="left",
            anchor="w",
            wraplength=560,
            font=(FONT, 9),
        ).pack(fill="x", pady=(10, 0))

    @staticmethod
    def _field_label(parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg=parent.cget("bg"),
            fg=TEXT,
            font=(FONT, 10, "bold"),
        )
        label.pack(anchor="w")
        return label

    @staticmethod
    def _entry(parent: tk.Widget, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            bg="#0f1628",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=(FONT, 10),
        )

    @staticmethod
    def _button(
        parent: tk.Widget,
        text: str,
        command: object,
        bg: str,
        fg: str,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="flat",
            padx=14,
            pady=7,
            cursor="hand2",
            font=(FONT, 9, "bold"),
        )

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _restore_latest_recording(self) -> None:
        """Restore the newest saved action after the desktop process restarts."""

        if not RECORDINGS.exists():
            return
        candidates = sorted(
            (
                path
                for path in RECORDINGS.iterdir()
                if path.is_dir()
                and (path / "session.json").exists()
                and (path / "events.jsonl").exists()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return
        session_dir = candidates[0]
        try:
            summary = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            events = [
                json.loads(line)
                for line in (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            profile_path = session_dir / "draft.action-profile.json"
            if not profile_path.exists():
                profile_path = session_dir / "draft.shadow.json"
            existing = json.loads(profile_path.read_text(encoding="utf-8"))
            existing_action = next(iter(existing.get("actions", {}).values()), {})
            title_evidence = next(
                (
                    str(item.get("expected", ""))
                    for item in existing_action.get("success_evidence", [])
                    if item.get("kind") == "window.title_contains"
                ),
                "",
            )
            goal = str(summary.get("goal", "")).strip() or "恢复的录制任务"
            self.session_id = str(summary.get("session_id", session_dir.name))
            self.session_dir = session_dir
            self.recorded_events = merge_recording_segments([], events)
            self.current_segment_index = max(
                (
                    int(event.get("segment_index") or 1)
                    for event in self.recorded_events
                ),
                default=0,
            )
            self.goal_var.set(goal)
            self.evidence_var.set(title_evidence)
            self.profile = build_profile(
                self.session_id,
                goal,
                events,
                title_evidence,
                final_effect=None,
            )
        except (OSError, ValueError, StopIteration, TypeError):
            return

        self.paths = {
            "events": session_dir / "events.jsonl",
            "profile": profile_path,
            "summary": session_dir / "session.json",
        }
        action = next(iter(self.profile["actions"].values()))
        detected_effect = any(isinstance(step.get("effect"), dict) for step in action["steps"])
        self.effect_var.set(detected_effect)
        lines = [
            f"{index:02d}. {step.get('description', step['kind'])}"
            for index, step in enumerate(action["steps"], 1)
        ]
        if detected_effect and lines:
            lines[-1] += "  ⚠ 最终对外动作"
        self._set_text(self.steps_text, "\n".join(lines) or "最近录制没有有效步骤。")
        self.inputs_text.delete("1.0", "end")
        placeholders = list(action["input_schema"]["properties"])
        if placeholders:
            self.inputs_text.insert("1.0", "\n".join(f"{name}=" for name in placeholders))
        self.status_var.set(f"已恢复最近动作 · {len(action['steps'])} 步")
        self.report_var.set(f"已恢复会话 {self.session_id}；请重新填写回放输入。")
        self.append_button.configure(state="normal")

    def show_dashboard(self) -> None:
        self.dashboard.deiconify()
        self.dashboard.lift()
        self.dashboard.focus_force()

    def _pill_action(self) -> None:
        if self.state == "idle":
            if not self.goal_var.get().strip():
                self.show_dashboard()
                self.status_var.set("请先描述任务目标")
                self.goal_entry.focus_set()
                return
            self.start_recording(append=False)
        elif self.state == "recording":
            self.stop_recording()
        elif self.state == "executing":
            self.replay.cancel()
            self.status_var.set("正在停止执行…")

    @staticmethod
    def _describe_target(target: dict) -> str:
        """人能读懂的目标身份。空字段不编造，直接说不知道。"""

        if not target:
            return "未解析到目标窗口"
        title = str(target.get("title") or "").strip() or "(无标题)"
        process = str(target.get("process") or "").strip() or "进程未知"
        confidence = target.get("confidence") or "unknown"
        note = ""
        if target.get("ambiguous"):
            note = f"⚠ 有 {target.get('candidates')} 个同名候选"
        elif confidence == "weak":
            note = "⚠ 仅靠标题匹配"
        return f"{title}（{process}，身份 {confidence}）{note}".strip()

    def continue_recording(self) -> None:
        if not self.profile or not self.recorded_events:
            self.report_var.set("请先完成第一段演示，再继续补录。")
            return
        self.start_recording(append=True)

    def start_recording(self, append: bool = False) -> None:
        if classify_effect(self.goal_var.get()):
            self.effect_var.set(True)
        self.pending_effect_step_id = None
        self.pending_effect_label = ""
        self.pending_target_fingerprint = ""
        self.execute_button.configure(text="真实执行")
        self.confirm_check.configure(text="我已检查并允许真实键鼠操作")
        self.recording_mode = "append" if append else "new"
        if not append:
            self.session_id = (
                datetime.now().strftime("%Y%m%d-%H%M%S")
                + "-"
                + uuid.uuid4().hex[:6]
            )
            self.session_dir = RECORDINGS / self.session_id
            self.recorded_events = []
            self.current_segment_index = 0
        elif not self.session_dir:
            self.report_var.set("找不到当前动作的录制目录，请重新演示。")
            return
        self.current_segment_index += 1
        self.recorder = EventRecorder(
            f"{self.session_id}-segment-{self.current_segment_index:02d}",
            self.session_dir / "screenshots" / f"segment-{self.current_segment_index:02d}",
            capture_screenshots=self.screenshot_var.get(),
        )
        self.recorder.start()
        self.started_at = time.monotonic()
        self.state = "recording"
        self.record_button.configure(
            text="停止补录" if append else "停止演示", bg=RED, fg="#2c0910"
        )
        self.append_button.configure(state="disabled")
        self.status_var.set(
            f"正在录制第 {self.current_segment_index} 段 · 点击漂浮按钮结束"
        )
        self._refresh_pill()

    def stop_recording(self) -> None:
        if not self.recorder or not self.session_dir:
            return
        captured_events = self.recorder.stop()
        self.recorded_events = merge_recording_segments(
            self.recorded_events if self.recording_mode == "append" else [],
            captured_events,
        )
        events = self.recorded_events
        self.state = "idle"
        self.record_button.configure(text="重新演示", bg=GREEN, fg="#08251d")
        self.append_button.configure(state="normal")

        final_title = ""
        if events:
            final_title = str(events[-1].get("window", {}).get("title", ""))
        if not self.evidence_var.get().strip():
            self.evidence_var.set(final_title)
        self.profile = build_profile(
            self.session_id,
            self.goal_var.get(),
            events,
            self.evidence_var.get(),
            # An unchecked box means "infer from the recording", not "force
            # unsafe". A checked box remains an explicit effect declaration.
            final_effect=True if self.effect_var.get() else None,
        )
        self.paths = save_recording(
            self.session_dir,
            self.session_id,
            self.goal_var.get(),
            events,
            self.profile,
        )
        action = next(iter(self.profile["actions"].values()))
        detected_effect = any(isinstance(step.get("effect"), dict) for step in action["steps"])
        if detected_effect:
            self.effect_var.set(True)
        lines = [
            f"{index:02d}. {step.get('description', step['kind'])}"
            for index, step in enumerate(action["steps"], 1)
        ]
        if not lines:
            lines = ["没有捕获到目标软件操作，请重新演示。"]
        elif detected_effect:
            lines[-1] += "  ⚠ 最终对外动作"
        self._set_text(self.steps_text, "\n".join(lines))
        placeholders = list(
            next(iter(self.profile["actions"].values()))["input_schema"]["properties"]
        )
        self.inputs_text.delete("1.0", "end")
        if placeholders:
            self.inputs_text.insert("1.0", "\n".join(f"{name}=" for name in placeholders))
        segment_count = max(
            (int(event.get("segment_index") or 1) for event in events),
            default=0,
        )
        self.status_var.set(
            f"已追加第 {segment_count} 段 · 共 {len(action['steps'])} 步"
            if self.recording_mode == "append"
            else f"已生成草案 · {len(action['steps'])} 步"
        )
        self.report_var.set(
            f"已保存动作草案 · 会话 {self.session_id} · {segment_count} 个片段"
        )
        self.confirm_var.set(False)
        self.pending_effect_step_id = None
        self.pending_effect_label = ""
        self.pending_target_fingerprint = ""
        self.execute_button.configure(text="真实执行")
        self.confirm_check.configure(text="我已检查并允许真实键鼠操作")
        self.show_dashboard()
        self._refresh_pill()

    def _inputs(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw in self.inputs_text.get("1.0", "end").splitlines():
            line = raw.strip()
            if not line:
                continue
            if "=" not in line:
                raise ProfileError(f"回放输入必须使用 name=value：{line}")
            name, value = line.split("=", 1)
            result[name.strip()] = value
        return result

    def _sync_evidence(self) -> None:
        if not self.profile:
            return
        action = next(iter(self.profile["actions"].values()))
        expected = self.evidence_var.get().strip()
        action["success_evidence"] = (
            [{"kind": "window.title_contains", "expected": expected, "timeout_ms": 5000}]
            if expected
            else []
        )

    def check_plan(self) -> None:
        if not self.profile:
            self.report_var.set("请先录制一个任务")
            return
        try:
            self.replay.reset()
            self._sync_evidence()
            report = self.replay.run(self.profile, self._inputs(), execute=False)
        except (ProfileError, ValueError) as exc:
            self.report_var.set(f"计划不可执行：{exc}")
            return
        self.report_var.set(
            f"计划有效：{report['step_count']} 步；需要输入 "
            f"{', '.join(report['required_inputs']) or '无'}；"
            f"最终确认点 {len(report.get('effects_requiring_confirmation', []))} 个；"
            "尚未执行键鼠。"
        )

    def execute_workflow(self) -> None:
        if not self.profile:
            self.report_var.set("请先录制一个任务")
            return
        if not self.confirm_var.get():
            self.report_var.set("真实执行前，请先检查计划并勾选授权。")
            return
        try:
            self.replay.reset()
            inputs = self._inputs()
            self._sync_evidence()
            self.replay.run(self.profile, inputs, execute=False)
        except (ProfileError, ValueError) as exc:
            self.report_var.set(f"无法执行：{exc}")
            return

        self.state = "executing"
        self.dashboard.withdraw()
        self.status_var.set("3 秒后开始；按 Esc 随时停止")
        self._refresh_pill()
        profile = json.loads(json.dumps(self.profile, ensure_ascii=False))
        pending_step_id = self.pending_effect_step_id
        confirmed_target = self.pending_target_fingerprint

        def worker() -> None:
            try:
                for remaining in (3, 2, 1):
                    self.worker_messages.put(("progress", f"{remaining} 秒后开始执行"))
                    time.sleep(1)
                    if self.replay.cancelled.is_set():
                        raise InterruptedError("执行已停止")
                report = self.replay.run(
                    profile,
                    inputs,
                    execute=True,
                    progress=lambda text: self.worker_messages.put(("progress", text)),
                    confirmed_effect_step_id=pending_step_id,
                    start_step_id=pending_step_id,
                    confirmed_target=confirmed_target or None,
                )
                self.worker_messages.put(("complete", report))
            except Exception as exc:  # execution boundary: turn all failures into a report
                self.worker_messages.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _tick(self) -> None:
        if self.state in {"recording", "executing"}:
            self._refresh_pill()
        while True:
            try:
                kind, payload = self.worker_messages.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.status_var.set(str(payload))
            elif kind == "complete":
                report = payload
                assert isinstance(report, dict)
                self.state = "idle"
                self.confirm_var.set(False)
                if report.get("mode") == "awaiting_confirmation":
                    effect = report.get("pending_effect") or {}
                    target = report.get("target") or {}
                    self.pending_effect_step_id = str(effect.get("step_id", ""))
                    self.pending_effect_label = str(effect.get("label", "最终对外动作"))
                    # 人要确认的是"发给谁"，不只是"要不要发"。目标写进确认文案本身。
                    self.pending_target_fingerprint = str(target.get("fingerprint", ""))
                    target_text = self._describe_target(target)
                    self.status_var.set("已停在最后一步前，等待当下确认")
                    self.execute_button.configure(text=f"确认{self.pending_effect_label}")
                    self.confirm_check.configure(
                        text=f"我确认现在执行：{self.pending_effect_label} → {target_text}"
                    )
                    self.report_var.set(
                        f"前置步骤已执行 {report.get('executed_step_count', 0)} 步；"
                        f"尚未执行“{self.pending_effect_label}”。\n"
                        f"目标：{target_text}\n"
                        f"请确认目标、内容和账号无误后再继续。"
                    )
                    self.show_dashboard()
                    self._refresh_pill()
                    continue
                if report.get("mode") == "readiness_timeout":
                    self.status_var.set("前置条件未满足，已停止")
                    self.report_var.set(
                        f"{report.get('error', '前置条件未满足')}\n"
                        f"已执行 {report.get('executed_step_count', 0)} 步后停下，"
                        f"没有继续往下点。"
                    )
                    self.execute_button.configure(text="真实执行")
                    self.confirm_check.configure(text="我已检查并允许真实键鼠操作")
                    self.pending_effect_step_id = None
                    self.pending_effect_label = ""
                    self.pending_target_fingerprint = ""
                    self.show_dashboard()
                    self._refresh_pill()
                    continue
                if report.get("mode") == "target_unverified":
                    # 拒绝执行不是失败，是守卫生效。文案必须让人看懂为什么被拦。
                    target = report.get("target") or {}
                    self.status_var.set("已拒绝执行对外动作")
                    self.report_var.set(
                        f"{report.get('error', '目标无法确认')}\n"
                        f"解析到的目标：{self._describe_target(target)}\n"
                        f"请让目标窗口处于确定状态后重试，或重新录制带进程身份的档案。"
                    )
                    self.execute_button.configure(text="真实执行")
                    self.confirm_check.configure(text="我已检查并允许真实键鼠操作")
                    self.pending_effect_step_id = None
                    self.pending_effect_label = ""
                    self.pending_target_fingerprint = ""
                    self.show_dashboard()
                    self._refresh_pill()
                    continue
                self.pending_effect_step_id = None
                self.pending_effect_label = ""
                self.pending_target_fingerprint = ""
                self.execute_button.configure(text="真实执行")
                self.confirm_check.configure(text="我已检查并允许真实键鼠操作")
                self.status_var.set("执行完成" if report.get("ok") else "执行后证据未通过")
                degraded = report.get("degraded_steps") or []
                locator_results = report.get("locator_results") or []
                uia_count = sum(1 for item in locator_results if item.get("used") == "uia")
                timed = report.get("timed_steps") or []
                unready = report.get("unready_steps") or []
                self.report_var.set(
                    f"执行 {'成功' if report.get('ok') else '未验证成功'} · "
                    f"{report.get('step_count')} 步 · {report.get('duration_ms')}ms · "
                    f"UIA 命中 {uia_count} 步 · 坐标兜底 {len(degraded)} 步\n"
                    # 照秒表的步骤越多，这份档案在别的机器上越不可靠。
                    f"等到状态 {len(report.get('readiness_results') or []) - len(timed)} 步 · "
                    f"照秒表 {len(timed)} 步 · 等超时 {len(unready)} 步"
                )
                self.show_dashboard()
                self._refresh_pill()
            elif kind == "error":
                self.state = "idle"
                self.confirm_var.set(False)
                self.status_var.set("执行已停止或失败")
                self.report_var.set(f"执行失败：{payload}")
                self.show_dashboard()
                self._refresh_pill()
        self.root.after(250, self._tick)

    def quit(self) -> None:
        if self.recorder and self.recorder.running:
            if not messagebox.askyesno("退出照做", "演示仍在录制，确定退出吗？"):
                return
            self.recorder.stop()
        self.replay.cancel()
        self.root.destroy()

    def run(self) -> int:
        self.show_dashboard()
        self.root.mainloop()
        return 0


def main() -> int:
    return ZhaozuoApp().run()
