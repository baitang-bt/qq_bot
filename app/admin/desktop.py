"""Native PyQt6 desktop admin for the QQ bot."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon, QMouseEvent
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.admin import bot_control, log_view, store, theme
from app.admin.collapsible import CollapsibleSection


class AdminWindow(QMainWindow):
    """Main window: bot control, persona, policy, and impressions."""

    def __init__(self) -> None:
        super().__init__()
        self._selected_openid = ""
        self._selected_admin_qq = ""
        self._imp_editing_new = False
        self._records: list[dict] = []
        self._admin_records: list[dict] = []
        self._log_clear_armed = False
        self._build_ui()
        self._wire_actions()
        self.refresh_all()

        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Cancel armed clear-log when the user clicks anywhere except that button."""
        if (
            self._log_clear_armed
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
        ):
            clicked = QApplication.widgetAt(event.globalPosition().toPoint())
            if not self._widget_is_clear_log_button(clicked):
                self._disarm_clear_logs()
        return super().eventFilter(watched, event)

    def _widget_is_clear_log_button(self, widget: QWidget | None) -> bool:
        """True when widget is the clear-log button or one of its children."""
        while widget is not None:
            if widget is self._btn_clear_log:
                return True
            widget = widget.parentWidget()
        return False

    def _on_poll(self) -> None:
        """Periodically refresh run-tab status and message logs."""
        self.refresh_status()
        self.refresh_logs()

    def _build_ui(self) -> None:
        """Lay out tabs and controls."""
        self.setWindowTitle("QQ 机器人管理")
        icon_path = bot_control.project_root() / "assets" / "app-icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(920, 640)

        tabs = QTabWidget()
        tabs.addTab(self._build_run_tab(), "运行")
        tabs.addTab(self._build_persona_tab(), "人设")
        tabs.addTab(self._build_policy_tab(), "回复策略")
        tabs.addTab(self._build_impressions_tab(), "印象")
        tabs.addTab(self._build_admins_tab(), "管理员")
        self.setCentralWidget(tabs)

    def _build_run_tab(self) -> QWidget:
        """Bot start/stop and log tail."""
        root = QWidget()
        layout = QVBoxLayout(root)

        box = QGroupBox("机器人状态")
        row = QHBoxLayout(box)
        self._status_label = QLabel("检查中…")
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._status_label, stretch=1)
        self._btn_toggle = QPushButton("启动")
        self._btn_refresh = QPushButton("刷新")
        row.addWidget(self._btn_toggle)
        row.addWidget(self._btn_refresh)
        layout.addWidget(box)

        self._gateway_hint = QLabel("")
        self._gateway_hint.setWordWrap(True)
        self._gateway_hint.setProperty("role", "warning")
        layout.addWidget(self._gateway_hint)

        self._monitor_label = QLabel("")
        self._monitor_label.setWordWrap(True)
        self._monitor_label.setProperty("role", "accent")
        layout.addWidget(self._monitor_label)

        hint = QLabel("人设与 reply_policy 保存后下一条消息即生效，一般不必重启。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        layout.addWidget(QLabel("消息监控日志（已过滤 /health 噪音）"))
        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log_view.setPlaceholderText(
            "发群消息后应出现 gateway chat → inbound → 正在回复 → 正在发送 → replied"
        )
        layout.addWidget(self._log_view, stretch=1)

        log_actions = QHBoxLayout()
        self._btn_log = QPushButton("立即刷新日志")
        self._btn_log.setToolTip("日志每 3 秒自动刷新；也可手动点此立即更新")
        self._btn_clear_log = QPushButton("清空日志")
        self._btn_clear_log.setToolTip("按一次进入确认，再按一次清空 data/uvicorn.log；机器人继续运行")
        log_actions.addWidget(self._btn_log)
        log_actions.addWidget(self._btn_clear_log)
        layout.addLayout(log_actions)
        return root

    def _build_persona_tab(self) -> QWidget:
        """bot_prompt.json editor."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("persona（整体人设）"))
        self._persona = QPlainTextEdit()
        layout.addWidget(self._persona, stretch=1)
        ui = store.read_admin_ui()
        self._anti = QPlainTextEdit()
        self._anti_section = CollapsibleSection(
            "anti_injection（每行一条）",
            self._anti,
            expanded=ui.persona_anti_injection_expanded,
            content_min_height=100,
        )
        layout.addWidget(self._anti_section)
        self._stay = QPlainTextEdit()
        self._stay_section = CollapsibleSection(
            "stay_on_prompt（每行一条）",
            self._stay,
            expanded=ui.persona_stay_on_prompt_expanded,
            content_min_height=120,
        )
        layout.addWidget(self._stay_section)
        self._anti_section.toggled.connect(self._save_persona_section_state)
        self._stay_section.toggled.connect(self._save_persona_section_state)
        prompt_actions = QHBoxLayout()
        self._btn_save_prompt = QPushButton("保存人设")
        self._btn_import_prompt = QPushButton("导入人设")
        prompt_actions.addWidget(self._btn_save_prompt)
        prompt_actions.addWidget(self._btn_import_prompt)
        layout.addLayout(prompt_actions)
        return root

    def _build_policy_tab(self) -> QWidget:
        """reply_policy.toml editor."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("reply_policy.toml"))
        self._policy = QPlainTextEdit()
        self._policy.setPlaceholderText("回复开关、频率、未@策略…")
        layout.addWidget(self._policy, stretch=1)
        self._btn_save_policy = QPushButton("保存策略")
        layout.addWidget(self._btn_save_policy)
        return root

    def _build_impressions_tab(self) -> QWidget:
        """User impression list and editor."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._imp_search = QLineEdit()
        self._imp_search.setPlaceholderText("按昵称搜索…")
        left_layout.addWidget(self._imp_search)
        self._imp_list = QListWidget()
        left_layout.addWidget(self._imp_list)
        imp_actions = QHBoxLayout()
        self._btn_new_imp = QPushButton("新建印象")
        self._btn_import_imp = QPushButton("导入印象")
        imp_actions.addWidget(self._btn_new_imp)
        imp_actions.addWidget(self._btn_import_imp)
        left_layout.addLayout(imp_actions)
        split.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self._imp_openid = QLineEdit()
        self._imp_openid.setPlaceholderText("新建时填写；选中已有用户时为只读")
        self._imp_username = QLineEdit()
        self._imp_qq = QLineEdit()
        self._imp_text = QPlainTextEdit()
        self._imp_text.setPlaceholderText("印象正文…")
        form.addRow("openid", self._imp_openid)
        form.addRow("昵称", self._imp_username)
        form.addRow("QQ", self._imp_qq)
        form.addRow("印象", self._imp_text)
        self._btn_save_imp = QPushButton("保存印象")
        form.addRow(self._btn_save_imp)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        outer = QVBoxLayout(root)
        outer.addWidget(split)
        return root

    def _build_admins_tab(self) -> QWidget:
        """Slash-command admin list (QQ + nickname + bind status)."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        hint = QLabel(
            "可在此配置能使用 /bind、/impression 等指令的用户。"
            "添加 QQ 号后，让对方私聊发送 /bind QQ号 完成绑定。"
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        left_layout.addWidget(hint)
        self._admin_list = QListWidget()
        left_layout.addWidget(self._admin_list)
        self._btn_add_admin = QPushButton("添加管理员")
        self._btn_delete_admin = QPushButton("删除选中")
        left_layout.addWidget(self._btn_add_admin)
        left_layout.addWidget(self._btn_delete_admin)
        split.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self._admin_username = QLineEdit()
        self._admin_username.setPlaceholderText("昵称（便于识别）")
        self._admin_qq = QLineEdit()
        self._admin_qq.setPlaceholderText("QQ 号，5–11 位数字")
        self._admin_openid = QLineEdit()
        self._admin_openid.setPlaceholderText("绑定后自动填入；也可从「印象」页复制 openid")
        form.addRow("昵称", self._admin_username)
        form.addRow("QQ 号", self._admin_qq)
        form.addRow("openid", self._admin_openid)
        self._btn_save_admin = QPushButton("保存管理员")
        form.addRow(self._btn_save_admin)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        outer = QVBoxLayout(root)
        outer.addWidget(split)
        return root

    def _wire_actions(self) -> None:
        """Connect buttons and list selection."""
        self._btn_toggle.clicked.connect(self.on_toggle_power)
        self._btn_refresh.clicked.connect(self.refresh_status)
        self._btn_log.clicked.connect(self.refresh_logs)
        self._btn_clear_log.clicked.connect(self.on_clear_logs)
        self._btn_save_prompt.clicked.connect(self.on_save_prompt)
        self._btn_import_prompt.clicked.connect(self.on_import_prompt)
        self._btn_save_policy.clicked.connect(self.on_save_policy)
        self._btn_save_imp.clicked.connect(self.on_save_impression)
        self._btn_new_imp.clicked.connect(self.on_new_impression)
        self._btn_import_imp.clicked.connect(self.on_import_impressions)
        self._imp_search.textChanged.connect(self.render_impressions)
        self._imp_list.currentItemChanged.connect(self.on_impression_selected)
        self._btn_add_admin.clicked.connect(self.on_add_admin)
        self._btn_delete_admin.clicked.connect(self.on_delete_admin)
        self._btn_save_admin.clicked.connect(self.on_save_admin)
        self._admin_list.currentItemChanged.connect(self.on_admin_selected)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        self.menuBar().addAction(quit_action)

    def refresh_all(self) -> None:
        """Reload every tab from disk."""
        self.refresh_status()
        self.refresh_logs()
        self.load_prompt()
        self.load_policy()
        self.load_impressions()
        self.load_command_admins()

    def refresh_status(self) -> None:
        """Update the run tab status line."""
        snap = store.bot_snapshot()
        if snap.running and snap.healthy:
            state = "运行中"
        elif snap.running:
            state = "进程在，但未就绪"
        else:
            state = "已停止"
        pid = f" · pid {snap.pid}" if snap.pid else ""
        cfg = []
        if snap.qq_configured:
            cfg.append("QQ 已配置")
        if snap.llm_configured:
            cfg.append(f"模型 {snap.llm_model}")
        extra = " · ".join(cfg)
        self._status_label.setText(f"{state}{pid}" + (f" · {extra}" if extra else ""))
        self._update_toggle_button(snap.running)
        hint = store.group_message_hint()
        self._gateway_hint.setText(hint)
        self._gateway_hint.setVisible(bool(hint))
        monitor = store.gateway_status_line()
        self._monitor_label.setText(monitor)
        self._monitor_label.setVisible(bool(monitor))

    def _update_toggle_button(self, running: bool) -> None:
        """Show 启动 or 停止 on the single power button."""
        self._btn_toggle.setText("停止" if running else "启动")
        self._btn_toggle.setEnabled(True)

    def on_toggle_power(self) -> None:
        """Start or stop the bot depending on current process state."""
        snap = store.bot_snapshot()
        if snap.running:
            self.on_stop()
        else:
            self.on_start()

    def refresh_logs(self) -> None:
        """Show monitor logs with newest lines at the top."""
        lines = list(reversed(store.tail_log_lines()))
        self._log_view.setHtml(log_view.format_logs_html(lines))
        self._log_view.verticalScrollBar().setValue(0)

    def _style_clear_log_button(self) -> None:
        """Re-apply stylesheet after toggling the clear-log confirm state."""
        self._btn_clear_log.style().unpolish(self._btn_clear_log)
        self._btn_clear_log.style().polish(self._btn_clear_log)
        self._btn_clear_log.update()

    def _arm_clear_logs(self) -> None:
        """First click: show red confirm label before truncating the log file."""
        self._log_clear_armed = True
        self._btn_clear_log.setText("确认清空")
        self._btn_clear_log.setObjectName("dangerConfirm")
        self._style_clear_log_button()

    def _disarm_clear_logs(self) -> None:
        """Restore the default clear-log button appearance."""
        self._log_clear_armed = False
        self._btn_clear_log.setText("清空日志")
        self._btn_clear_log.setObjectName("")
        self._style_clear_log_button()

    def on_clear_logs(self) -> None:
        """Two-step clear: arm on first click, truncate uvicorn.log on second."""
        if not self._log_clear_armed:
            self._arm_clear_logs()
            return
        self._disarm_clear_logs()
        try:
            store.clear_monitor_logs()
        except OSError as exc:
            self._alert("清空失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self.refresh_logs()

    def _save_persona_section_state(self, _expanded: bool = False) -> None:
        """Persist anti_injection / stay_on_prompt fold state for the next launch."""
        store.save_admin_ui(
            store.AdminUiState(
                persona_anti_injection_expanded=self._anti_section.is_expanded,
                persona_stay_on_prompt_expanded=self._stay_section.is_expanded,
            )
        )

    def load_prompt(self) -> None:
        """Fill persona tab from bot_prompt.json."""
        try:
            data = store.read_prompt()
        except (OSError, ValueError) as exc:
            self._alert("读取人设失败", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._persona.setPlainText(data.persona)
        self._anti.setPlainText("\n".join(data.anti_injection))
        self._stay.setPlainText("\n".join(data.stay_on_prompt))

    def load_policy(self) -> None:
        """Fill policy tab from reply_policy.toml."""
        text, _path = store.read_reply_policy()
        self._policy.setPlainText(text)

    def load_impressions(self) -> None:
        """Reload impression list."""
        self._records = store.list_impressions()
        self._records.sort(key=lambda item: str(item.get("username") or ""))
        self.render_impressions()

    def render_impressions(self) -> None:
        """Filter and show impression rows."""
        needle = self._imp_search.text().strip().casefold()
        self._imp_list.clear()
        for record in self._records:
            username = str(record.get("username") or "（无昵称）")
            openid = str(record.get("user_openid") or "")
            if needle and needle not in username.casefold() and needle not in openid.casefold():
                continue
            item = QListWidgetItem(f"{username}\n{openid}")
            item.setData(Qt.ItemDataRole.UserRole, openid)
            self._imp_list.addItem(item)
            if openid == self._selected_openid:
                self._imp_list.setCurrentItem(item)

    def _set_impression_openid_editable(self, editable: bool) -> None:
        """Toggle openid field between new-record entry and read-only display."""
        self._imp_editing_new = editable
        self._imp_openid.setReadOnly(not editable)

    def on_impression_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """Load one impression into the editor."""
        if current is None:
            return
        openid = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if not openid:
            return
        self._selected_openid = openid
        self._set_impression_openid_editable(False)
        record = store.load_impression(openid)
        self._imp_openid.setText(openid)
        self._imp_username.setText(str(record.get("username") or ""))
        self._imp_qq.setText(str(record.get("qq") or ""))
        self._imp_text.setPlainText(str(record.get("impression") or ""))

    def on_new_impression(self) -> None:
        """Clear the editor so the user can enter a new openid and impression."""
        self._imp_list.clearSelection()
        self._selected_openid = ""
        self._set_impression_openid_editable(True)
        self._imp_openid.clear()
        self._imp_username.clear()
        self._imp_qq.clear()
        self._imp_text.clear()
        self._imp_openid.setFocus()

    def on_import_impressions(self) -> None:
        """Import one or more impression JSON files from disk."""
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "导入印象",
            "",
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not paths:
            return
        try:
            imported, skipped, errors = store.import_impression_files([Path(path) for path in paths])
        except OSError as exc:
            self._alert("导入失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self.load_impressions()
        summary = f"成功导入 {imported} 条"
        if skipped:
            summary += f"，跳过 {skipped} 条"
        if errors:
            preview = "\n".join(errors[:8])
            if len(errors) > 8:
                preview += f"\n…共 {len(errors)} 条提示"
            summary += f"\n\n{preview}"
        icon = QMessageBox.Icon.Information if imported else QMessageBox.Icon.Warning
        self._alert("导入完成", summary, icon)

    def load_command_admins(self) -> None:
        """Reload slash-command admin list."""
        self._admin_records = store.list_command_admins()
        self.render_command_admins()

    def render_command_admins(self) -> None:
        """Show admin rows in the list widget."""
        self._admin_list.clear()
        for record in self._admin_records:
            username = str(record.get("username") or "（无昵称）")
            qq = str(record.get("qq") or "")
            openid = str(record.get("user_openid") or record.get("openid_hint") or "")
            bound = "已绑定" if record.get("user_openid") else ("未绑定" if not openid else "未绑定·有印象")
            label = f"{username} · QQ {qq}\n{bound}"
            if openid:
                label += f" · {openid[:16]}…" if len(openid) > 16 else f" · {openid}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, qq)
            self._admin_list.addItem(item)
            if qq == self._selected_admin_qq:
                self._admin_list.setCurrentItem(item)

    def on_admin_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """Load one admin row into the editor."""
        if current is None:
            return
        qq = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if not qq:
            return
        self._selected_admin_qq = qq
        record = next((row for row in self._admin_records if row.get("qq") == qq), {})
        self._admin_username.setText(str(record.get("username") or ""))
        self._admin_qq.setText(qq)
        openid = str(record.get("user_openid") or "")
        hint = str(record.get("openid_hint") or "")
        self._admin_openid.setText(openid or hint)
        if openid:
            self._admin_openid.setToolTip("已绑定 openid")
        elif hint:
            self._admin_openid.setToolTip("来自印象的 openid 提示，保存后会写入绑定")
        else:
            self._admin_openid.setToolTip("")

    def on_add_admin(self) -> None:
        """Append a blank admin row and select it."""
        self._admin_records.append({"qq": "", "username": "", "user_openid": ""})
        self._selected_admin_qq = ""
        self.render_command_admins()
        self._admin_username.clear()
        self._admin_qq.clear()
        self._admin_openid.clear()
        self._admin_qq.setFocus()

    def on_delete_admin(self) -> None:
        """Remove the selected admin from the list."""
        qq = self._admin_qq.text().strip() or self._selected_admin_qq
        if not qq:
            self._alert("未选择", "请先在左侧选中一名管理员。", QMessageBox.Icon.Warning)
            return
        self._admin_records = [row for row in self._admin_records if row.get("qq") != qq]
        self._selected_admin_qq = ""
        try:
            store.save_command_admins(self._admin_records)
        except OSError as exc:
            self._alert("删除失败", store.format_error(exc), QMessageBox.Icon.Critical)
            self.load_command_admins()
            return
        self.load_command_admins()
        self._admin_username.clear()
        self._admin_qq.clear()
        self._admin_openid.clear()

    def on_save_admin(self) -> None:
        """Persist the admin being edited."""
        qq = self._admin_qq.text().strip()
        if not qq.isdigit() or len(qq) < 5:
            self._alert("QQ 无效", "请填写 5–11 位数字 QQ 号。", QMessageBox.Icon.Warning)
            return
        username = self._admin_username.text().strip()
        openid = self._admin_openid.text().strip()
        replaced = False
        rows: list[dict] = []
        for row in self._admin_records:
            if row.get("qq") == self._selected_admin_qq and self._selected_admin_qq:
                rows.append({"qq": qq, "username": username, "user_openid": openid})
                replaced = True
            elif row.get("qq") != qq:
                rows.append(row)
        if not replaced:
            rows.append({"qq": qq, "username": username, "user_openid": openid})
        try:
            store.save_command_admins(rows)
        except OSError as exc:
            self._alert("保存失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self._selected_admin_qq = qq
        self.load_command_admins()
        self._alert("已保存", "管理员列表已更新，下一条 /bind 或指令即按新名单生效。", QMessageBox.Icon.Information)

    def on_start(self) -> None:
        """Start the bot process."""
        self._btn_toggle.setEnabled(False)
        try:
            store.start_bot()
        except (OSError, subprocess.CalledProcessError) as exc:
            self._alert("启动失败", store.format_error(exc), QMessageBox.Icon.Critical)
            self.refresh_status()
            return
        self.refresh_status()
        self.refresh_logs()

    def on_stop(self) -> None:
        """Stop the bot process."""
        self._btn_toggle.setEnabled(False)
        try:
            store.stop_bot()
        except (OSError, subprocess.CalledProcessError) as exc:
            self._alert("停止失败", store.format_error(exc), QMessageBox.Icon.Critical)
            self.refresh_status()
            return
        self.refresh_status()

    def on_save_prompt(self) -> None:
        """Persist bot_prompt.json."""
        try:
            path = store.save_prompt(
                self._persona.toPlainText(),
                self._anti.toPlainText().splitlines(),
                self._stay.toPlainText().splitlines(),
            )
        except OSError as exc:
            self._alert("保存失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self._alert("已保存", f"人设已写入\n{path}", QMessageBox.Icon.Information)

    def on_import_prompt(self) -> None:
        """Import bot_prompt.json from a local file."""
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "导入人设",
            "",
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not path:
            return
        try:
            saved = store.import_prompt_file(Path(path))
        except (OSError, ValueError) as exc:
            self._alert("导入失败", str(exc) or store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self.load_prompt()
        self._alert("导入完成", f"人设已写入\n{saved}", QMessageBox.Icon.Information)

    def on_save_policy(self) -> None:
        """Persist reply_policy.toml."""
        try:
            path = store.save_reply_policy(self._policy.toPlainText())
        except OSError as exc:
            self._alert("保存失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self._alert("已保存", f"策略已写入\n{path}", QMessageBox.Icon.Information)

    def on_save_impression(self) -> None:
        """Persist the selected or newly entered impression."""
        openid = self._imp_openid.text().strip()
        if not openid:
            self._alert("缺少 openid", "请填写 user_openid，或从左侧列表选择已有用户。", QMessageBox.Icon.Warning)
            return
        try:
            path = store.save_impression(
                openid,
                self._imp_username.text(),
                self._imp_qq.text(),
                self._imp_text.toPlainText(),
            )
        except OSError as exc:
            self._alert("保存失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self._selected_openid = openid
        self._set_impression_openid_editable(False)
        self.load_impressions()
        self._alert("已保存", f"印象已写入\n{path}", QMessageBox.Icon.Information)

    def _alert(self, title: str, text: str, icon: QMessageBox.Icon) -> None:
        """Show a native message box."""
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop polling when the window closes."""
        self._poll.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)


def main() -> None:
    """Launch the native admin application."""
    _configure_qt_paths()
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("QQ机器人管理")
        app.setApplicationDisplayName("QQ机器人管理")
        app.setOrganizationName("Potatoblock")
        if sys.platform == "darwin":
            app.setStyle("Fusion")
        theme.apply_dark_theme(app)
        if not _acquire_single_instance():
            _admin_log("second instance blocked")
            QMessageBox.information(None, "QQ机器人管理", "管理窗口已在运行，请看 Dock 或切到已有窗口。")
            raise SystemExit(0)
        window = AdminWindow()
        _center_on_screen(window)
        window.showNormal()
        window.raise_()
        window.activateWindow()
        _pin_window_front(window)
        _admin_log(f"window shown pid={os.getpid()}")
        raise SystemExit(app.exec())
    except Exception as exc:
        _admin_log(f"desktop crash: {exc!r}")
        raise


def _configure_qt_paths() -> None:
    """Set Qt plugin search path before QApplication starts."""
    if sys.platform != "darwin":
        return
    import PyQt6

    plugins = Path(PyQt6.__file__).resolve().parent / "Qt6" / "plugins"
    if plugins.is_dir():
        os.environ.setdefault("QT_PLUGIN_PATH", str(plugins))


def _acquire_single_instance() -> bool:
    """Return False when another admin window is already running."""
    key = "qq-chat-bot-admin"
    probe = QLocalSocket()
    probe.connectToServer(key)
    if probe.waitForConnected(300):
        probe.close()
        return False
    server = QLocalServer()
    if not server.listen(key):
        server.removeServer(key)
        return server.listen(key)
    return True


def _pin_window_front(window: AdminWindow) -> None:
    """Keep the window above others briefly so double-click launches are visible."""
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.show()

    def _release() -> None:
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        window.show()
        window.raise_()
        window.activateWindow()

    QTimer.singleShot(1500, _release)


def _center_on_screen(window: AdminWindow) -> None:
    """Place the window on the primary display so it is not off-screen."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    frame = window.frameGeometry()
    frame.moveCenter(area.center())
    window.move(frame.topLeft())


def _admin_log(message: str) -> None:
    """Append one admin lifecycle line for desktop.app troubleshooting."""
    log_path = bot_control.project_root() / "data" / "admin-desktop.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


if __name__ == "__main__":
    main()
