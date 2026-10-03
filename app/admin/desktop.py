"""Native PyQt6 desktop admin for the QQ bot."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon, QMouseEvent
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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

from app.admin import bot_control, env_settings, log_view, store, theme


class _BotPowerWorker(QObject):
    """Run start/stop scripts off the UI thread so the window stays responsive."""

    finished = pyqtSignal(object)

    def __init__(self, action: str) -> None:
        super().__init__()
        self._action = action

    def run(self) -> None:
        """Execute store.start_bot or store.stop_bot and emit None or an exception."""
        try:
            if self._action == "start":
                store.start_bot()
            else:
                store.stop_bot()
            self.finished.emit(None)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.finished.emit(exc)


class AdminWindow(QMainWindow):
    """Main window: run control, Bot 功能, API/.env, persona, policy, and impressions."""

    def __init__(self) -> None:
        super().__init__()
        self._selected_openid = ""
        self._selected_admin_qq = ""
        self._imp_editing_new = False
        self._records: list[dict] = []
        self._admin_records: list[dict] = []
        self._log_clear_armed = False
        self._persona_id = ""
        self._persona_file = ""
        self._persona_dirty = False
        self._persona_loading = False
        self._power_thread: QThread | None = None
        self._power_worker: _BotPowerWorker | None = None
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
        tabs.addTab(self._build_features_tab(), "Bot 功能")
        tabs.addTab(self._build_api_tab(), "API")
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

        hint = QLabel("人设与 reply_policy 保存后下一条消息即生效。改代码或表情发送异常时，先点停止再启动。")
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

    def _build_features_tab(self) -> QWidget:
        """Speak-mode switch and Finder shortcuts for bot data folders."""
        root = QWidget()
        layout = QVBoxLayout(root)
        speak = QGroupBox("群发言")
        feat = QVBoxLayout(speak)
        self._btn_speak = QPushButton("发言模式：自动")
        self._btn_speak.setToolTip(
            "点击切换群发言：自动（未@规则+工作日高峰）→ 全部尝试回复 → 仅@和引用回复"
        )
        feat.addWidget(self._btn_speak)
        feat_hint = QLabel("写入 reply_policy.toml，下一条消息生效，不必重启。")
        feat_hint.setWordWrap(True)
        feat_hint.setProperty("role", "muted")
        feat.addWidget(feat_hint)
        layout.addWidget(speak)

        files = QGroupBox("本地文件")
        file_row = QHBoxLayout(files)
        self._btn_stickers = QPushButton("打开表情包")
        self._btn_stickers.setToolTip("访达打开 data/stickers（PNG/JPG/GIF）")
        self._btn_personas_folder = QPushButton("打开人设")
        self._btn_personas_folder.setToolTip("访达打开 data/personas（每套人设一个文件夹）")
        self._btn_project_folder = QPushButton("打开项目根")
        self._btn_project_folder.setToolTip("访达打开仓库根目录（personas.toml、.env、reply_policy.toml）")
        file_row.addWidget(self._btn_stickers)
        file_row.addWidget(self._btn_personas_folder)
        file_row.addWidget(self._btn_project_folder)
        layout.addWidget(files)
        layout.addStretch(1)
        return root

    def _build_api_tab(self) -> QWidget:
        """Edit project .env API / QQ credentials with secrets masked."""
        root = QWidget()
        layout = QVBoxLayout(root)
        hint = QLabel(
            "写入项目根目录 .env（不进 git）。密钥框留空表示保持原值不改写；"
            "保存后需在「运行」页重启机器人进程才会生效。"
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._api_qq_app_id = QLineEdit()
        self._api_qq_app_id.setPlaceholderText("QQ 开放平台 AppID")
        self._api_qq_secret = QLineEdit()
        self._api_qq_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_qq_secret.setPlaceholderText("已保存则留空不改")
        self._api_qq_id = QLineEdit()
        self._api_qq_id.setPlaceholderText("主人 QQ 号（可选）")

        self._api_llm_base = QLineEdit()
        self._api_llm_base.setPlaceholderText("例如 https://api.deepseek.com/v1")
        self._api_llm_key = QLineEdit()
        self._api_llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_llm_key.setPlaceholderText("已保存则留空不改")
        self._api_llm_model = QLineEdit()
        self._api_llm_model.setPlaceholderText("DeepSeek 官方 ID：deepseek-flash")
        self._api_vision_model = QLineEdit()
        self._api_vision_model.setPlaceholderText("可留空，默认跟 LLM（聊天已多模态识图）")
        self._api_timeout = QLineEdit()
        self._api_timeout.setPlaceholderText("秒，默认 25")

        self._api_secret_hints: dict[str, QLabel] = {}
        qq_secret_hint = QLabel("")
        qq_secret_hint.setProperty("role", "muted")
        llm_key_hint = QLabel("")
        llm_key_hint.setProperty("role", "muted")
        self._api_secret_hints["QQ_APP_SECRET"] = qq_secret_hint
        self._api_secret_hints["LLM_API_KEY"] = llm_key_hint

        form.addRow("QQ_APP_ID", self._api_qq_app_id)
        form.addRow("QQ_APP_SECRET", self._api_qq_secret)
        form.addRow("", qq_secret_hint)
        form.addRow("QQ_ID", self._api_qq_id)
        form.addRow("LLM_BASE_URL", self._api_llm_base)
        form.addRow("LLM_API_KEY", self._api_llm_key)
        form.addRow("", llm_key_hint)
        form.addRow("LLM_MODEL", self._api_llm_model)
        form.addRow("VISION_MODEL", self._api_vision_model)
        form.addRow("LLM_TIMEOUT_SECONDS", self._api_timeout)
        layout.addLayout(form)
        layout.addStretch(1)

        actions = QHBoxLayout()
        self._btn_reload_api = QPushButton("重新加载")
        self._btn_save_api = QPushButton("保存 API 配置")
        actions.addWidget(self._btn_reload_api)
        actions.addWidget(self._btn_save_api)
        layout.addLayout(actions)
        return root

    def _build_persona_tab(self) -> QWidget:
        """Persona pack list plus one txt editor."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        hint = QLabel("点选只用来编辑。线上口吻以「启用」的那一套为准，下一条消息生效。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        left_layout.addWidget(hint)
        self._persona_list = QListWidget()
        left_layout.addWidget(self._persona_list)
        pack_actions = QHBoxLayout()
        self._btn_new_persona = QPushButton("新建")
        self._btn_enable_persona = QPushButton("启用")
        self._btn_delete_persona = QPushButton("删除")
        pack_actions.addWidget(self._btn_new_persona)
        pack_actions.addWidget(self._btn_enable_persona)
        pack_actions.addWidget(self._btn_delete_persona)
        left_layout.addLayout(pack_actions)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("文件"))
        self._persona_file_combo = QComboBox()
        file_row.addWidget(self._persona_file_combo, stretch=1)
        self._btn_add_persona_file = QPushButton("+ txt")
        self._btn_open_persona_pack = QPushButton("打开本组")
        file_row.addWidget(self._btn_add_persona_file)
        file_row.addWidget(self._btn_open_persona_pack)
        right_layout.addLayout(file_row)
        self._persona_editor = QPlainTextEdit()
        self._persona_editor.setPlaceholderText("选中人设和文件后编辑…")
        right_layout.addWidget(self._persona_editor, stretch=1)
        self._btn_save_persona_file = QPushButton("保存本文件")
        right_layout.addWidget(self._btn_save_persona_file)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        outer = QVBoxLayout(root)
        outer.addWidget(split)
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
        self._btn_stickers.clicked.connect(self.on_open_stickers)
        self._btn_personas_folder.clicked.connect(self.on_open_personas_folder)
        self._btn_project_folder.clicked.connect(self.on_open_project_folder)
        self._btn_speak.clicked.connect(self.on_cycle_speak_mode)
        self._btn_log.clicked.connect(self.refresh_logs)
        self._btn_clear_log.clicked.connect(self.on_clear_logs)
        self._btn_reload_api.clicked.connect(self.load_api_settings)
        self._btn_save_api.clicked.connect(self.on_save_api_settings)
        self._btn_new_persona.clicked.connect(self.on_new_persona)
        self._btn_enable_persona.clicked.connect(self.on_enable_persona)
        self._btn_delete_persona.clicked.connect(self.on_delete_persona)
        self._btn_add_persona_file.clicked.connect(self.on_add_persona_file)
        self._btn_open_persona_pack.clicked.connect(self.on_open_persona_pack)
        self._btn_save_persona_file.clicked.connect(self.on_save_persona_file)
        self._persona_list.currentItemChanged.connect(self.on_persona_selected)
        self._persona_file_combo.currentTextChanged.connect(self.on_persona_file_changed)
        self._persona_editor.textChanged.connect(self.on_persona_text_changed)
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
        self.load_api_settings()
        self.load_personas()
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
        self._refresh_speak_mode_button()

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

    def on_open_stickers(self) -> None:
        """Reveal the local sticker cache folder in Finder."""
        try:
            path = store.reveal_in_file_manager(store.stickers_cache_dir())
        except (OSError, subprocess.CalledProcessError) as exc:
            self._alert("无法打开表情包文件夹", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._btn_stickers.setToolTip(f"访达打开 {path}")

    def on_open_personas_folder(self) -> None:
        """Reveal data/personas in Finder."""
        try:
            path = store.reveal_in_file_manager(store.personas_dir())
        except (OSError, subprocess.CalledProcessError) as exc:
            self._alert("无法打开人设文件夹", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._btn_personas_folder.setToolTip(f"访达打开 {path}")

    def on_open_project_folder(self) -> None:
        """Reveal the repository root in Finder."""
        try:
            path = store.reveal_in_file_manager(bot_control.project_root())
        except (OSError, subprocess.CalledProcessError) as exc:
            self._alert("无法打开项目根目录", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._btn_project_folder.setToolTip(f"访达打开 {path}")

    def _refresh_speak_mode_button(self) -> None:
        """Show the current group speak mode on the cycle button."""
        try:
            self._btn_speak.setText(store.speak_mode_button_text())
        except OSError:
            self._btn_speak.setText("发言模式：自动")

    def on_cycle_speak_mode(self) -> None:
        """Cycle auto / all / mention-quote and persist speak_mode in toml."""
        try:
            mode = store.cycle_speak_mode()
        except OSError as exc:
            self._alert("无法切换发言模式", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._btn_speak.setText(store.speak_mode_button_text(mode))
        self.load_policy()

    def load_api_settings(self) -> None:
        """Fill API tab from .env; secrets stay empty with a masked status hint."""
        try:
            view = env_settings.read_api_settings()
        except OSError as exc:
            self._alert("读取 API 配置失败", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        vals = view.values
        self._api_qq_app_id.setText(vals.get("QQ_APP_ID", ""))
        self._api_qq_id.setText(vals.get("QQ_ID", ""))
        self._api_llm_base.setText(vals.get("LLM_BASE_URL", ""))
        self._api_llm_model.setText(vals.get("LLM_MODEL", ""))
        self._api_vision_model.setText(vals.get("VISION_MODEL", ""))
        self._api_timeout.setText(vals.get("LLM_TIMEOUT_SECONDS", ""))
        self._api_qq_secret.clear()
        self._api_llm_key.clear()
        self._set_secret_hint("QQ_APP_SECRET", view)
        self._set_secret_hint("LLM_API_KEY", view)

    def _set_secret_hint(self, key: str, view: env_settings.ApiSettingsView) -> None:
        """Show masked status under a secret field without exposing the raw value."""
        label = self._api_secret_hints[key]
        if view.secret_set.get(key):
            mask = view.secret_masks.get(key) or "****"
            label.setText(f"已保存：{mask}（输入框留空则不修改）")
        else:
            label.setText("未配置")

    def on_save_api_settings(self) -> None:
        """Write public fields and any newly typed secrets into .env."""
        updates = {
            "QQ_APP_ID": self._api_qq_app_id.text(),
            "QQ_ID": self._api_qq_id.text(),
            "LLM_BASE_URL": self._api_llm_base.text(),
            "LLM_MODEL": self._api_llm_model.text(),
            "VISION_MODEL": self._api_vision_model.text(),
            "LLM_TIMEOUT_SECONDS": self._api_timeout.text(),
            "QQ_APP_SECRET": self._api_qq_secret.text(),
            "LLM_API_KEY": self._api_llm_key.text(),
        }
        try:
            path = env_settings.save_api_settings(updates)
        except OSError as exc:
            self._alert("保存失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self.load_api_settings()
        self.refresh_status()
        self._alert(
            "已保存",
            f"已写入 {path.name}。若机器人正在运行，请到「运行」页重启后生效。",
            QMessageBox.Icon.Information,
        )

    def load_personas(self) -> None:
        """Reload the persona pack list and keep the current selection when possible."""
        try:
            packs = store.list_persona_packs()
        except (OSError, ValueError) as exc:
            self._alert("读取人设失败", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        keep_id = self._persona_id
        active = ""
        self._persona_list.blockSignals(True)
        self._persona_list.clear()
        selected: QListWidgetItem | None = None
        for pack in packs:
            pack_id = str(pack.get("id") or "")
            title = str(pack.get("title") or pack_id)
            suffix = " · 启用" if pack.get("active") else ""
            item = QListWidgetItem(f"{title}{suffix}\n{pack_id}")
            item.setData(Qt.ItemDataRole.UserRole, pack_id)
            self._persona_list.addItem(item)
            if pack.get("active"):
                active = pack_id
            if keep_id and pack_id == keep_id:
                selected = item
            elif selected is None and pack.get("active"):
                selected = item
        if selected is None and self._persona_list.count():
            selected = self._persona_list.item(0)
        self._persona_list.blockSignals(False)
        if selected is not None:
            self._persona_list.setCurrentItem(selected)
        elif not packs:
            self._persona_id = ""
            self._persona_file = ""
            self._persona_file_combo.clear()
            self._persona_editor.setPlainText("")
        if not keep_id and active:
            self._persona_id = active

    def on_persona_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """Load txt files for the pack selected in the list (does not enable it)."""
        if current is None:
            return
        self._flush_persona_editor()
        pack_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self._persona_id = pack_id
        self._fill_persona_files(pack_id)

    def _fill_persona_files(self, pack_id: str, prefer: str = "") -> None:
        """Populate the file combo for a pack and load one txt."""
        names = store.list_persona_files(pack_id) if pack_id else []
        want = prefer or self._persona_file
        if want not in names:
            want = names[0] if names else ""
        self._persona_file_combo.blockSignals(True)
        self._persona_file_combo.clear()
        self._persona_file_combo.addItems(names)
        if want:
            self._persona_file_combo.setCurrentText(want)
        self._persona_file_combo.blockSignals(False)
        self._load_persona_file(pack_id, want)

    def _load_persona_file(self, pack_id: str, filename: str) -> None:
        """Put one txt into the editor without marking it dirty."""
        self._persona_loading = True
        self._persona_file = filename
        if pack_id and filename:
            self._persona_editor.setPlainText(store.read_persona_file(pack_id, filename))
        else:
            self._persona_editor.setPlainText("")
        self._persona_dirty = False
        self._persona_loading = False

    def on_persona_file_changed(self, filename: str) -> None:
        """Switch the editor to another txt in the current pack."""
        if self._persona_loading:
            return
        self._flush_persona_editor()
        self._load_persona_file(self._persona_id, filename.strip())

    def on_persona_text_changed(self) -> None:
        """Remember that the current txt has unsaved edits."""
        if not self._persona_loading:
            self._persona_dirty = True

    def _flush_persona_editor(self) -> None:
        """Write the current editor buffer if it changed."""
        if not self._persona_dirty or not self._persona_id or not self._persona_file:
            return
        try:
            store.save_persona_file(
                self._persona_id,
                self._persona_file,
                self._persona_editor.toPlainText(),
            )
        except (OSError, ValueError) as exc:
            self._alert("保存人设文件失败", str(exc) or store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._persona_dirty = False

    def on_save_persona_file(self) -> None:
        """Persist the txt currently in the editor."""
        if not self._persona_id or not self._persona_file:
            self._alert("未选中文件", "先在左侧选一套人设，再选一个 txt。", QMessageBox.Icon.Warning)
            return
        try:
            path = store.save_persona_file(
                self._persona_id,
                self._persona_file,
                self._persona_editor.toPlainText(),
            )
        except (OSError, ValueError) as exc:
            self._alert("保存失败", str(exc) or store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self._persona_dirty = False
        self._alert("已保存", f"已写入\n{path}", QMessageBox.Icon.Information)

    def on_new_persona(self) -> None:
        """Create a new pack folder with an empty persona.txt."""
        persona_id, ok = QInputDialog.getText(self, "新建人设", "id（小写字母、数字、下划线）：")
        if not ok:
            return
        try:
            row = store.new_persona_pack(persona_id.strip())
        except (OSError, ValueError) as exc:
            self._alert("无法新建", str(exc) or store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._persona_id = str(row.get("id") or "")
        self.load_personas()

    def on_enable_persona(self) -> None:
        """Make the selected pack the one the bot uses."""
        pack_id = self._persona_id
        if not pack_id:
            self._alert("未选中人设", "先在左侧选一套再启用。", QMessageBox.Icon.Warning)
            return
        self._flush_persona_editor()
        try:
            store.set_persona_active(pack_id)
        except (OSError, ValueError) as exc:
            self._alert("无法启用", str(exc) or store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self.load_personas()

    def on_delete_persona(self) -> None:
        """Delete the selected pack if it is not active and not the last one."""
        pack_id = self._persona_id
        if not pack_id:
            return
        confirm = QMessageBox.question(
            self,
            "删除人设",
            f"删除 {pack_id} 及其全部 txt？启用中的不能删。",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            store.delete_persona_pack(pack_id)
        except (OSError, ValueError) as exc:
            self._alert("无法删除", str(exc) or store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._persona_id = ""
        self._persona_file = ""
        self.load_personas()

    def on_add_persona_file(self) -> None:
        """Add an empty txt to the selected pack."""
        if not self._persona_id:
            self._alert("未选中人设", "先在左侧选一套人设。", QMessageBox.Icon.Warning)
            return
        name, ok = QInputDialog.getText(self, "新建 txt", "文件名（如 extra_lore.txt）：")
        if not ok:
            return
        try:
            path = store.add_persona_file(self._persona_id, name.strip())
        except (OSError, ValueError) as exc:
            self._alert("无法添加文件", str(exc) or store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._fill_persona_files(self._persona_id, prefer=path.name)

    def on_open_persona_pack(self) -> None:
        """Reveal the selected pack folder in Finder."""
        if not self._persona_id:
            self._alert("未选中人设", "先在左侧选一套人设。", QMessageBox.Icon.Warning)
            return
        folder = store.personas_dir() / self._persona_id
        try:
            path = store.reveal_in_file_manager(folder)
        except (OSError, subprocess.CalledProcessError) as exc:
            self._alert("无法打开本组", store.format_error(exc), QMessageBox.Icon.Warning)
            return
        self._btn_open_persona_pack.setToolTip(f"访达打开 {path}")

    def load_policy(self) -> None:
        """Fill policy tab from reply_policy.toml."""
        text, _path = store.read_reply_policy()
        self._policy.setPlainText(text)

    def on_save_policy(self) -> None:
        """Persist reply_policy.toml."""
        try:
            path = store.save_reply_policy(self._policy.toPlainText())
        except OSError as exc:
            self._alert("保存失败", store.format_error(exc), QMessageBox.Icon.Critical)
            return
        self._alert("已保存", f"策略已写入\n{path}", QMessageBox.Icon.Information)

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
        """Start the bot process in a background thread."""
        self._run_power_action("start")

    def on_stop(self) -> None:
        """Stop the bot process in a background thread."""
        self._run_power_action("stop")

    def _run_power_action(self, action: str) -> None:
        """Disable the power button and run start/stop off the UI thread."""
        if self._power_thread is not None and self._power_thread.isRunning():
            return
        self._btn_toggle.setEnabled(False)
        self._btn_toggle.setText("启动中…" if action == "start" else "停止中…")
        thread = QThread(self)
        worker = _BotPowerWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            lambda err, a=action, t=thread, w=worker: self._on_power_finished(a, err, t, w)
        )
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._power_thread = thread
        self._power_worker = worker
        thread.start()

    def _on_power_finished(
        self,
        action: str,
        error: object,
        thread: QThread,
        worker: _BotPowerWorker,
    ) -> None:
        """Re-enable controls after background start/stop completes."""
        if self._power_thread is thread:
            self._power_thread = None
        if self._power_worker is worker:
            self._power_worker = None
        self.refresh_status()
        self.refresh_logs()
        if error is not None:
            title = "启动失败" if action == "start" else "停止失败"
            self._alert(title, store.format_error(error), QMessageBox.Icon.Critical)
            return
        snap = store.bot_snapshot()
        if action == "start" and not (snap.running and snap.healthy):
            self._alert(
                "启动未就绪",
                "进程未通过健康检查。请查看「运行」页日志或 data/uvicorn.log。",
                QMessageBox.Icon.Warning,
            )

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
