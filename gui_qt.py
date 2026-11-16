"""PyQt6 interface for the Mole Warriors pet respec workflow."""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QCloseEvent, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from map_locations import MAP_LOCATIONS
from pet_prediction import (
    PetBasis,
    PetPrediction,
    apply_basis_wave,
    find_pet_basis,
    predict_pet,
    predict_pet_with_unknown_points,
    round_display,
    scenario,
    simulate_base,
)
from pet_respec import (
    BASE_FIELDS,
    DERIVED_FIELDS,
    ITEM_TYPES,
    RespecRunner,
    WashConfig,
    WashProgress,
    WashResult,
    is_pet_washable,
    target_fields,
)
from protocol_backend import (
    EXPERIENCE_BOOK_ID,
    GIANT_ROCK_FRAGMENT_ID,
    PET_SKILL_OPTIONS,
    ProtocolBackend,
    validate_pet_nickname,
)
from skill_names import load_skill_names


COLORS = {
    "background": "#eef3f3",
    "surface": "#ffffff",
    "surface_alt": "#f5f8f8",
    "text": "#163033",
    "muted": "#607477",
    "line": "#d5e1e1",
    "accent": "#087f78",
    "accent_dark": "#066a64",
    "accent_soft": "#e2f2f0",
    "success": "#17735c",
    "warning": "#9a5a12",
    "danger": "#a23b3b",
}

APP_USER_MODEL_ID = "mole-respec.desktop"
APP_ICON_PATH = Path(__file__).resolve().with_name("logo.webp")
THANKS_IMAGE_PATH = Path(__file__).resolve().with_name("thanks_header.png")
RESULT_REASON = {
    "target": "已达到期望值",
    "limit": "已达到次数上限",
    "inventory": "道具数量不足",
    "stopped": "已由用户停止",
}
AUTO_TARGET_WAVES = (
    (5, 5, -5, 0, -5),
    (-5, 5, 0, 5, -5),
    (5, 0, -5, 5, -5),
)
AUTO_TARGET_KEYS = {
    "base": (
        frozenset(("physique", "strength")),
        frozenset(("strength", "quick")),
        frozenset(("physique", "quick")),
    ),
    "derived": (
        frozenset(("hp_max", "attack")),
        frozenset(("attack", "speed")),
        frozenset(("hp_max", "speed")),
    ),
}


def automatic_target_groups(
    basis: PetBasis,
    pet: Mapping[str, object],
    item_index: int,
    mode: str,
) -> tuple[tuple[int, ...], ...]:
    if item_index in (0, 1):
        raise ValueError("一键填充仅适用于包含五项属性的洗点道具")
    level = int(pet["level"])
    rebirth = int(pet["reincarnation_degree"])
    strength_points = (0, level - 1, 0, 0, 0)
    fields = target_fields(item_index, mode)
    groups = []
    for wave, included_keys in zip(AUTO_TARGET_WAVES, AUTO_TARGET_KEYS[mode]):
        result = scenario(apply_basis_wave(basis.base, wave), level, rebirth, strength_points)
        values: dict[str, int] = {"grow_value": round_display(basis.growth)}
        values.update((key, value) for (key, _), value in zip(BASE_FIELDS, result.efforts))
        values.update((key, value) for (key, _), value in zip(DERIVED_FIELDS, result.battle))
        groups.append(
            tuple(
                values[field.key]
                if field.key == "grow_value" or field.key in included_keys
                else 0
                for field in fields
            )
        )
    return tuple(groups)


def pet_skill_rows(
    pet: Mapping[str, object],
    skill_names: Mapping[int, str] | None = None,
) -> tuple[tuple[str, str], ...]:
    raw_skills = pet.get("skills", ())
    if not isinstance(raw_skills, (list, tuple)):
        raise TypeError("宠物技能数据格式无效")
    rows = []
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, Mapping):
            raise TypeError("宠物技能数据格式无效")
        skill_id = int(raw_skill["skill_id"])
        level = int(raw_skill["level"])
        name = skill_names.get(skill_id) if skill_names is not None else None
        rows.append((name or f"ID {skill_id}", f"等级 {level}"))
    return tuple(rows)


@dataclass(frozen=True)
class PredictionCacheEntry:
    point_values: tuple[str, ...]
    growth_value: str
    growth: int | None
    growth_source: str
    prediction: PetPrediction | None
    error: str | None


def _clear_layout(layout: QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)  # type: ignore[arg-type]


def _line(value: str = "", width: int | None = None) -> QLineEdit:
    widget = QLineEdit(value)
    if width is not None:
        widget.setFixedWidth(width)
    return widget


class PetRespecApp(QMainWindow):
    """Main application window backed entirely by PyQt6 widgets."""

    def __init__(self) -> None:
        super().__init__()
        self.backend = ProtocolBackend()
        self.skill_names = load_skill_names()
        self.pet_egg_items = self.backend.pet_egg_items
        self.map_locations = MAP_LOCATIONS
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.busy_action: str | None = None
        self.connected = False
        self.bag_pets: list[dict[str, object]] = []
        self.storage_pets: list[dict[str, object]] = []
        self.selected_pet: dict[str, object] | None = None
        self.selected_pet_location: str | None = None
        self.remembered_pet_selection: tuple[int, int] | None = None
        self.storage_request_id = 0
        self.item_quantities: dict[int, int] = {}
        self.misc_item_quantities: dict[int, dict[str, int]] = {}
        self.exp_tree_experience: int | None = None
        self.target_group_entries: list[list[QLineEdit]] = []
        self.target_group_checks: list[QCheckBox] = []
        self.simulator_basis: PetBasis | None = None
        self.prediction_pet: dict[str, object] | None = None
        self.prediction_cache: dict[tuple[str, int, int], PredictionCacheEntry] = {}
        self.pet_sort_column: int | None = None
        self.pet_sort_order = Qt.SortOrder.AscendingOrder
        self.storage_sort_column: int | None = None
        self.storage_sort_order = Qt.SortOrder.AscendingOrder

        self._configure_window()
        self._build_ui()
        self._render_pet_list()
        self._render_storage_list()
        self._render_targets()
        self._sync_controls()
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._poll_events)
        self.event_timer.start(100)

    def _configure_window(self) -> None:
        self.setWindowTitle("摩尔勇士宠物洗点工具")
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1320, 824)
        self.setMinimumSize(1080, 780)
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget#root {{ background: {COLORS['background']}; color: {COLORS['text']}; }}
            QWidget {{ font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; letter-spacing: 0px; }}
            QFrame#surface {{ background: {COLORS['surface']}; border: 1px solid {COLORS['line']}; border-radius: 8px; }}
            QFrame#loginBar {{ background: #f9fbfb; border: 1px solid {COLORS['line']}; border-radius: 8px; }}
            QFrame#subtlePanel {{ background: {COLORS['surface_alt']}; border: 1px solid #e0e8e8; border-radius: 6px; }}
            QFrame#metricCard {{ background: {COLORS['surface_alt']}; border: 1px solid #e0e8e8; border-radius: 6px; }}
            QFrame#growthMetric {{ background: {COLORS['accent_soft']}; border: 1px solid #bcdedb; border-radius: 6px; }}
            QGroupBox {{ background: {COLORS['surface']}; border: 1px solid {COLORS['line']}; border-radius: 6px; margin-top: 11px; padding-top: 11px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {COLORS['text']}; }}
            QLabel#title {{ font-size: 24px; font-weight: 700; color: #102d2f; }}
            QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; color: #18383a; }}
            QLabel#pageTitle {{ font-size: 17px; font-weight: 700; color: #102d2f; }}
            QLabel#muted {{ color: {COLORS['muted']}; }}
            QLabel#metricValue {{ font-family: 'Segoe UI'; font-size: 18px; font-weight: 700; color: #193b3e; }}
            QLabel#badge {{ background: #e7eeee; color: {COLORS['muted']}; padding: 7px 13px; border-radius: 6px; font-weight: 600; }}
            QLabel#counterBadge {{ background: {COLORS['accent_soft']}; color: {COLORS['accent_dark']}; padding: 2px 8px; border-radius: 7px; font-size: 12px; font-weight: 600; }}
            QLabel#fieldLabel {{ color: #345154; font-weight: 600; }}
            QPushButton {{ min-height: 30px; padding: 2px 12px; border: 1px solid #b9c9ca; border-radius: 5px; background: #ffffff; color: #244245; font-weight: 500; }}
            QPushButton:hover {{ background: #f1f7f6; border-color: #86aaa7; }}
            QPushButton:pressed {{ background: #e4efee; border-color: {COLORS['accent']}; }}
            QPushButton:focus {{ border: 2px solid #4f9e98; padding: 1px 11px; }}
            QPushButton:disabled {{ color: #9aa8aa; background: #f2f5f5; border-color: #d9e1e1; }}
            QPushButton#accent {{ color: white; background: {COLORS['accent']}; border-color: {COLORS['accent']}; font-weight: 600; }}
            QPushButton#accent:hover {{ background: {COLORS['accent_dark']}; }}
            QPushButton#accent:disabled {{ color: #98a5a6; background: #e8eded; border-color: #d6dfdf; }}
            QPushButton#danger {{ color: {COLORS['danger']}; border-color: #dfc3c3; background: #fffafa; }}
            QPushButton#danger:hover {{ background: #f9eaea; border-color: #cf9999; }}
            QLineEdit {{ min-height: 30px; padding: 0 8px; border: 1px solid #b9c9ca; border-radius: 5px; background: white; selection-background-color: {COLORS['accent']}; }}
            QLineEdit:hover {{ border-color: #88aaa8; }}
            QLineEdit:focus {{ border: 2px solid #3c928c; padding-left: 7px; }}
            QLineEdit:disabled {{ background: #f1f4f4; color: #91a0a2; border-color: #dbe3e3; }}
            QComboBox {{ min-height: 30px; padding: 0 10px; border: 1px solid #b9c9ca; border-radius: 5px; background: white; color: #244245; }}
            QComboBox:hover {{ border-color: #78a6a2; background: #fbfdfd; }}
            QComboBox:focus, QComboBox:on {{ border: 2px solid #3c928c; padding: 0 9px; }}
            QComboBox:disabled {{ background: #f1f4f4; color: #91a0a2; border-color: #dbe3e3; }}
            QComboBox::drop-down {{ width: 0; border: 0; background: transparent; }}
            QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
            QComboBox QAbstractItemView {{ background: white; color: {COLORS['text']}; border: 1px solid #82aaa7; border-radius: 5px; padding: 4px; outline: 0; selection-background-color: #dcefed; selection-color: {COLORS['text']}; }}
            QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 3px 8px; border-radius: 3px; }}
            QComboBox QAbstractItemView::item:hover {{ background: #edf7f6; }}
            QComboBox QAbstractItemView::item:selected {{ background: #dcefed; color: {COLORS['accent_dark']}; }}
            QCheckBox, QRadioButton {{ spacing: 7px; }}
            QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
            QTabWidget::pane {{ border: 0; border-top: 1px solid {COLORS['line']}; background: white; top: -1px; }}
            QTabBar::tab {{ min-width: 78px; padding: 10px 14px; margin-right: 2px; color: #587174; background: transparent; border: 0; }}
            QTabBar::tab:hover {{ color: {COLORS['accent_dark']}; background: #f0f7f6; }}
            QTabBar::tab:selected {{ color: {COLORS['accent_dark']}; background: white; border-bottom: 3px solid {COLORS['accent']}; font-weight: 600; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: white; }}
            QScrollBar:vertical {{ background: #edf3f3; width: 11px; margin: 2px; border: 0; border-radius: 5px; }}
            QScrollBar::handle:vertical {{ background: #a9c2c0; min-height: 36px; border-radius: 4px; margin: 1px; }}
            QScrollBar::handle:vertical:hover {{ background: #719f9b; }}
            QScrollBar::handle:vertical:pressed {{ background: {COLORS['accent']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: transparent; border: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollBar:horizontal {{ background: #edf3f3; height: 11px; margin: 2px; border: 0; border-radius: 5px; }}
            QScrollBar::handle:horizontal {{ background: #a9c2c0; min-width: 36px; border-radius: 4px; margin: 1px; }}
            QScrollBar::handle:horizontal:hover {{ background: #719f9b; }}
            QScrollBar::handle:horizontal:pressed {{ background: {COLORS['accent']}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; background: transparent; border: 0; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
            QAbstractScrollArea::corner {{ background: transparent; border: 0; }}
            QTableWidget {{ background: white; alternate-background-color: #f8fafa; border: 1px solid {COLORS['line']}; border-radius: 5px; gridline-color: transparent; selection-background-color: #dcefed; selection-color: {COLORS['text']}; outline: 0; }}
            QTableWidget::item {{ padding: 5px; border-bottom: 1px solid #edf1f1; }}
            QTableWidget::item:selected {{ border-bottom: 1px solid #c7dfdd; }}
            QHeaderView::section {{ background: #edf3f3; color: #536e71; padding: 8px 6px; border: 0; border-right: 1px solid #dae4e4; border-bottom: 1px solid {COLORS['line']}; font-weight: 600; }}
            QProgressBar {{ min-height: 16px; border: 0; border-radius: 5px; text-align: center; background: #e7eeee; color: #234345; font-weight: 600; }}
            QProgressBar::chunk {{ background: {COLORS['accent']}; border-radius: 5px; }}
            QFrame#skillCard {{ background: {COLORS['surface_alt']}; border: 1px solid #e0e8e8; border-radius: 6px; }}
            QFrame#skillCard:hover {{ background: #edf7f6; border-color: #bdd8d5; }}
            QLabel#skillName {{ color: #345154; font-weight: 600; }}
            QLabel#skillLevel {{ color: {COLORS['accent_dark']}; font-family: 'Segoe UI'; font-size: 14px; font-weight: 700; }}
            QSplitter::handle {{ background: transparent; width: 8px; }}
            QToolTip {{ color: white; background: #234345; border: 0; padding: 5px 7px; }}
            """
        )

    @staticmethod
    def _button(text: str, callback: Callable[[], None], accent: bool = False) -> QPushButton:
        button = QPushButton(text)
        if accent:
            button.setObjectName("accent")
        button.clicked.connect(callback)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(16, 16))
        return button

    def _set_standard_icon(
        self, button: QPushButton, icon: QStyle.StandardPixmap
    ) -> None:
        button.setIcon(self.style().standardIcon(icon))

    @staticmethod
    def _polish_combo(combo: QComboBox) -> None:
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setMaxVisibleItems(12)
        combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        combo.view().setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _page_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("pageTitle")
        return label

    @staticmethod
    def _muted(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(20, 16, 20, 12)
        shell.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        if APP_ICON_PATH.exists():
            logo = QLabel()
            pixmap = QPixmap(str(APP_ICON_PATH))
            logo.setPixmap(
                pixmap.scaled(
                    46,
                    46,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            logo.setFixedSize(50, 50)
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("宠物洗点")
        title.setObjectName("title")
        title_box.addWidget(title)
        title_box.addWidget(self._muted("摩尔勇士宠物养成工作台"))
        header.addLayout(title_box, 1)
        if THANKS_IMAGE_PATH.exists():
            thanks = QLabel()
            thanks_pixmap = QPixmap(str(THANKS_IMAGE_PATH)).scaledToHeight(
                48, Qt.TransformationMode.SmoothTransformation
            )
            thanks.setPixmap(thanks_pixmap)
            thanks.setFixedSize(thanks_pixmap.size())
            thanks.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            header.addWidget(thanks)
        self.connection_badge = QLabel("未连接")
        self.connection_badge.setObjectName("badge")
        header.addWidget(self.connection_badge)
        shell.addLayout(header)

        shell.addWidget(self._build_connection())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_pet_sidebar())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setHandleWidth(10)
        splitter.setSizes([340, 960])
        shell.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status_label = self._muted("请输入账号并连接服务器")
        footer.addWidget(self.status_label)
        footer.addStretch()
        footer.addWidget(self._muted("密码仅用于本次登录，不会保存"))
        shell.addLayout(footer)

    def _build_connection(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("loginBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(8)
        layout.addWidget(self._field_label("米米号"))
        self.uid_entry = _line()
        self.uid_entry.setPlaceholderText("请输入米米号")
        layout.addWidget(self.uid_entry, 1)
        layout.addSpacing(4)
        layout.addWidget(self._field_label("密码"))
        self.password_entry = _line()
        self.password_entry.setPlaceholderText("请输入登录密码")
        self.password_entry.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password_entry, 1)
        self.show_password = QCheckBox("显示")
        self.show_password.toggled.connect(
            lambda checked: self.password_entry.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        layout.addWidget(self.show_password)
        layout.addSpacing(4)
        layout.addWidget(self._field_label("服务器"))
        self.server_combo = QComboBox()
        self._polish_combo(self.server_combo)
        self.server_combo.addItems(["随机活动服 (11-20)"] + [f"{i} 服" for i in range(1, 21)])
        layout.addWidget(self.server_combo)
        self.connect_button = self._button("连接", self._connect, True)
        self.disconnect_button = self._button("断开", self._disconnect)
        self._set_standard_icon(
            self.connect_button, QStyle.StandardPixmap.SP_DialogApplyButton
        )
        self._set_standard_icon(
            self.disconnect_button, QStyle.StandardPixmap.SP_DialogCloseButton
        )
        layout.addWidget(self.connect_button)
        layout.addWidget(self.disconnect_button)
        self.uid_entry.returnPressed.connect(self._connect)
        self.password_entry.returnPressed.connect(self._connect)
        return frame

    def _new_pet_table(self, headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.setWordWrap(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _build_pet_sidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("surface")
        frame.setMinimumWidth(310)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(self._section_title("背包宠物"))
        self.pet_count_label = QLabel("0")
        self.pet_count_label.setObjectName("counterBadge")
        heading.addWidget(self.pet_count_label)
        heading.addStretch()
        self.refresh_button = self._button("刷新", self._refresh)
        self._set_standard_icon(
            self.refresh_button, QStyle.StandardPixmap.SP_BrowserReload
        )
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.pet_table = self._new_pet_table(("昵称", "种类", "等级", "转生"))
        pet_header = self.pet_table.horizontalHeader()
        pet_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        pet_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        pet_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        pet_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        pet_header.resizeSection(0, 88)
        pet_header.resizeSection(2, 54)
        pet_header.resizeSection(3, 54)
        pet_header.sectionClicked.connect(self._sort_pet_table)
        for column in range(3):
            self.pet_table.horizontalHeaderItem(column).setToolTip("点击排序")
        self.pet_table.itemSelectionChanged.connect(self._select_pet)
        layout.addWidget(self.pet_table, 1)
        storage_heading = QHBoxLayout()
        storage_heading.addWidget(self._section_title("仓库宠物"))
        self.storage_count_label = QLabel("0")
        self.storage_count_label.setObjectName("counterBadge")
        storage_heading.addWidget(self.storage_count_label)
        storage_heading.addStretch()
        layout.addLayout(storage_heading)
        self.storage_table = self._new_pet_table(("昵称", "种类", "等级"))
        storage_header = self.storage_table.horizontalHeader()
        storage_header.sectionClicked.connect(self._sort_storage_table)
        for column in range(3):
            self.storage_table.horizontalHeaderItem(column).setToolTip("点击排序")
        self.storage_table.itemSelectionChanged.connect(self._select_storage_pet)
        layout.addWidget(self.storage_table, 1)
        return frame

    def _build_workspace(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("surface")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 6, 12, 10)
        self.notebook = QTabWidget()
        self.notebook.setDocumentMode(True)
        self.operation_tab = QWidget()
        self.prediction_tab = QWidget()
        self.simulator_tab = QWidget()
        self.misc_tab = QWidget()
        self.result_tab = QWidget()
        for page, title in (
            (self.operation_tab, "洗点设置"),
            (self.prediction_tab, "成长预测"),
            (self.simulator_tab, "基数与洗宠模拟"),
            (self.misc_tab, "杂项"),
            (self.result_tab, "洗点结果"),
        ):
            self.notebook.addTab(page, title)
        layout.addWidget(self.notebook)
        self._build_operation_tab()
        self._build_prediction_tab()
        self._build_simulator_tab()
        self._build_misc_tab()
        self._build_result_tab()
        self.notebook.currentChanged.connect(self._on_workspace_tab_changed)
        return frame

    def _metric_box(self, label: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("metricCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 5, 8, 5)
        name = self._muted(label)
        value = QLabel("-")
        value.setObjectName("metricValue")
        layout.addWidget(name)
        layout.addWidget(value)
        return frame, value

    def _build_operation_tab(self) -> None:
        layout = QVBoxLayout(self.operation_tab)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(10)
        header = QHBoxLayout()
        detail = QVBoxLayout()
        self.pet_title_label = QLabel("请先选择宠物")
        self.pet_title_label.setStyleSheet("font-size: 16px; font-weight: 600")
        self.pet_meta_label = self._muted("连接服务器后，从左侧宠物列表中选择")
        detail.addWidget(self.pet_title_label)
        detail.addWidget(self.pet_meta_label)
        header.addLayout(detail, 1)
        self.change_pet_name_button = self._button("修改昵称", self._change_selected_pet_name)
        self.to_storage_button = self._button("放回仓库", lambda: self._move_selected_pet("to_storage"))
        self.to_bag_button = self._button("取回背包", lambda: self._move_selected_pet("to_bag"))
        self.release_pet_button = self._button("放生宠物", self._release_selected_pet)
        self.release_pet_button.setObjectName("danger")
        self._set_standard_icon(
            self.change_pet_name_button, QStyle.StandardPixmap.SP_FileDialogContentsView
        )
        self._set_standard_icon(
            self.to_storage_button, QStyle.StandardPixmap.SP_ArrowDown
        )
        self._set_standard_icon(
            self.to_bag_button, QStyle.StandardPixmap.SP_ArrowUp
        )
        self._set_standard_icon(
            self.release_pet_button, QStyle.StandardPixmap.SP_TrashIcon
        )
        for button in (self.change_pet_name_button, self.to_storage_button, self.to_bag_button, self.release_pet_button):
            header.addWidget(button)
        layout.addLayout(header)

        overview = QHBoxLayout()
        metrics = QGridLayout()
        self.metric_labels: dict[str, QLabel] = {}
        defs = (("grow_value", "成长"), *BASE_FIELDS, *DERIVED_FIELDS)
        for index, (key, label) in enumerate(defs):
            card, value = self._metric_box(label)
            if index == 0:
                card.setObjectName("growthMetric")
            self.metric_labels[key] = value
            if index == 0:
                metrics.addWidget(card, 0, 0, 1, 5)
            elif index <= 5:
                metrics.addWidget(card, 1, index - 1)
            else:
                metrics.addWidget(card, 2, index - 6)
        overview.addLayout(metrics, 3)
        skills = QWidget()
        skills_layout = QVBoxLayout(skills)
        skills_layout.setContentsMargins(0, 0, 0, 0)
        skills_layout.setSpacing(6)
        skill_header = QHBoxLayout()
        skill_header.addWidget(self._section_title("技能"))
        self.skill_count_label = QLabel("-")
        self.skill_count_label.setObjectName("counterBadge")
        skill_header.addWidget(self.skill_count_label)
        self.learn_skills_button = self._button("学习技能", self._open_learn_skills_dialog)
        self._set_standard_icon(
            self.learn_skills_button, QStyle.StandardPixmap.SP_DialogApplyButton
        )
        skill_header.addStretch()
        skill_header.addWidget(self.learn_skills_button)
        skills_layout.addLayout(skill_header)
        self.skills_content = QWidget()
        self.skills_content_layout = QGridLayout(self.skills_content)
        self.skills_content_layout.setContentsMargins(0, 0, 3, 4)
        self.skills_content_layout.setHorizontalSpacing(6)
        self.skills_content_layout.setVerticalSpacing(6)
        self.skills_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.skills_content_layout.setColumnStretch(0, 1)
        self.skills_content_layout.setColumnStretch(1, 1)
        self.skills_scroll = QScrollArea()
        self.skills_scroll.setWidgetResizable(True)
        self.skills_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.skills_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.skills_scroll.setMinimumHeight(150)
        self.skills_scroll.setMaximumHeight(190)
        self.skills_scroll.setWidget(self.skills_content)
        skills_layout.addWidget(self.skills_scroll, 1)
        self._render_pet_skills(None)
        overview.addWidget(skills, 2)
        layout.addLayout(overview)

        config_panel = QFrame()
        config_panel.setObjectName("subtlePanel")
        config = QGridLayout(config_panel)
        config.setContentsMargins(12, 9, 12, 10)
        config.setHorizontalSpacing(14)
        config.addWidget(self._field_label("洗点道具"), 0, 0)
        config.addWidget(self._field_label("洗点方式"), 0, 1)
        config.addWidget(self._field_label("最多洗点次数"), 0, 2)
        self.item_combo = QComboBox()
        self._polish_combo(self.item_combo)
        self.item_combo.currentIndexChanged.connect(self._render_targets)
        config.addWidget(self.item_combo, 1, 0)
        mode_row = QHBoxLayout()
        self.base_mode = QRadioButton("基础五项")
        self.derived_mode = QRadioButton("战斗面板")
        self.base_mode.setChecked(True)
        self.base_mode.toggled.connect(self._render_targets)
        self.fill_targets_button = self._button("一键填充", self._fill_target_candidates)
        self._set_standard_icon(
            self.fill_targets_button, QStyle.StandardPixmap.SP_DialogResetButton
        )
        mode_row.addWidget(self.base_mode)
        mode_row.addWidget(self.derived_mode)
        mode_row.addWidget(self.fill_targets_button)
        config.addLayout(mode_row, 1, 1)
        self.max_attempts_entry = _line("100")
        config.addWidget(self.max_attempts_entry, 1, 2)
        config.setColumnStretch(0, 1)
        config.setColumnStretch(1, 1)
        config.setColumnStretch(2, 1)
        layout.addWidget(config_panel)

        target_header = QHBoxLayout()
        target_header.addWidget(self._section_title("期望数值"))
        target_header.addStretch()
        self.target_hint_label = self._muted()
        target_header.addWidget(self.target_hint_label)
        layout.addLayout(target_header)
        self.targets_widget = QWidget()
        self.targets_widget.setObjectName("targetPanel")
        self.targets_widget.setStyleSheet(
            f"QWidget#targetPanel {{ background: {COLORS['surface_alt']}; border: 1px solid #e0e8e8; border-radius: 6px; }}"
        )
        self.targets_layout = QVBoxLayout(self.targets_widget)
        self.targets_layout.setContentsMargins(12, 8, 12, 8)
        self.targets_layout.setSpacing(5)
        layout.addWidget(self.targets_widget)

        actions = QHBoxLayout()
        self.start_button = self._button("开始洗点", self._start_wash, True)
        self.stop_button = self._button("停止", self._stop_wash)
        self.stop_button.setObjectName("danger")
        self._set_standard_icon(
            self.start_button, QStyle.StandardPixmap.SP_MediaPlay
        )
        self._set_standard_icon(
            self.stop_button, QStyle.StandardPixmap.SP_MediaStop
        )
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress_text_label = self._muted("尚未开始")
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.progress, 1)
        actions.addWidget(self.progress_text_label)
        layout.addLayout(actions)
        layout.addStretch()

    def _build_prediction_tab(self) -> None:
        layout = QVBoxLayout(self.prediction_tab)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(9)
        layout.addWidget(self._page_title("等级与转生预测"))
        self.prediction_intro_label = self._muted("选择宠物后，自动按默认成长和全力加点计算预测")
        self.prediction_intro_label.setWordWrap(True)
        layout.addWidget(self.prediction_intro_label)
        editor = QFrame()
        editor.setObjectName("subtlePanel")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(10, 8, 10, 8)
        editor_layout.setSpacing(6)
        points_row = QHBoxLayout()
        points_row.addWidget(QLabel("当前自由加点"))
        self.prediction_point_entries = []
        for label in ("体", "力", "耐", "敏", "智"):
            points_row.addWidget(self._muted(label))
            entry = _line("", 52)
            self.prediction_point_entries.append(entry)
            points_row.addWidget(entry)
        points_row.addWidget(self._muted("成长"))
        self.prediction_growth_entry = _line("", 68)
        points_row.addWidget(self.prediction_growth_entry)
        points_row.addStretch()
        editor_layout.addLayout(points_row)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.prediction_calculate_button = self._button("重新计算", self._calculate_prediction)
        self.prediction_unknown_button = self._button("未知加点预测", self._calculate_unknown_point_prediction)
        self._set_standard_icon(
            self.prediction_calculate_button, QStyle.StandardPixmap.SP_BrowserReload
        )
        self._set_standard_icon(
            self.prediction_unknown_button, QStyle.StandardPixmap.SP_MessageBoxQuestion
        )
        action_row.addWidget(self.prediction_calculate_button)
        action_row.addWidget(self.prediction_unknown_button)
        editor_layout.addLayout(action_row)
        layout.addWidget(editor)
        self.prediction_base_label = QLabel("匹配到的 0 转基数：-")
        self.prediction_fit_label = self._muted()
        self.prediction_fit_label.setWordWrap(True)
        layout.addWidget(self.prediction_base_label)
        layout.addWidget(self.prediction_fit_label)
        self.prediction_scroll = QScrollArea()
        self.prediction_scroll.setWidgetResizable(True)
        self.prediction_content = QWidget()
        self.prediction_content_layout = QVBoxLayout(self.prediction_content)
        self.prediction_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.prediction_scroll.setWidget(self.prediction_content)
        layout.addWidget(self.prediction_scroll, 1)
        layout.addWidget(self._muted("大丸子与开蛋统一按原始五项整数 -5～+5 和成长双向均摊计算。预测优先要求十项完全一致，仅在无精确结果时使用显示容差。"))

    def _build_simulator_tab(self) -> None:
        layout = QVBoxLayout(self.simulator_tab)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._page_title("基数与洗宠模拟"))
        self.simulator_species_label = self._muted("选择宠物后，从本地基数库载入对应服务器的原始基数")
        layout.addWidget(self.simulator_species_label)
        editors = QHBoxLayout()
        basis_box = QGroupBox("基数与波动")
        basis_grid = QGridLayout(basis_box)
        for col, text in enumerate(("属性", "原始基数", "输入波动", "目标基数")):
            basis_grid.addWidget(self._muted(text), 0, col)
        self.simulator_original_labels = []
        self.simulator_wave_entries = []
        self.simulator_target_labels = []
        for row_index, label in enumerate(("体力", "力量", "耐力", "敏捷", "智力"), 1):
            basis_grid.addWidget(QLabel(label), row_index, 0)
            original = QLabel("-")
            wave = _line("0", 64)
            target = QLabel("-")
            wave.textChanged.connect(self._preview_simulator_target)
            basis_grid.addWidget(original, row_index, 1)
            basis_grid.addWidget(wave, row_index, 2)
            basis_grid.addWidget(target, row_index, 3)
            self.simulator_original_labels.append(original)
            self.simulator_wave_entries.append(wave)
            self.simulator_target_labels.append(target)
        editors.addWidget(basis_box)
        allocation_box = QGroupBox("自由加点")
        allocation_grid = QGridLayout(allocation_box)
        self.simulator_allocation_headers = [QLabel("0转99级\n最多98"), QLabel("0转50级\n最多49"), QLabel("10转99级\n最多98")]
        allocation_grid.addWidget(self._muted("属性"), 0, 0)
        for column, header in enumerate(self.simulator_allocation_headers, 1):
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            allocation_grid.addWidget(header, 0, column)
        self.simulator_point_entries: list[list[QLineEdit]] = [[], [], []]
        for row_index, label in enumerate(("体力", "力量", "耐力", "敏捷", "智力"), 1):
            allocation_grid.addWidget(QLabel(label), row_index, 0)
            for scenario_index in range(3):
                entry = _line("0", 58)
                self.simulator_point_entries[scenario_index].append(entry)
                allocation_grid.addWidget(entry, row_index, scenario_index + 1)
        editors.addWidget(allocation_box)
        layout.addLayout(editors)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("0转任意等级"))
        self.simulator_level_entry = _line("50", 64)
        self.simulator_level_entry.textChanged.connect(self._update_simulator_allocation_header)
        controls.addWidget(self.simulator_level_entry)
        calculate_button = self._button("计算模拟", self._calculate_base_simulation, True)
        fill_strength_button = self._button("一键全加力量", self._fill_simulator_strength)
        reset_wave_button = self._button("清空波动", self._reset_simulator_wave)
        self._set_standard_icon(calculate_button, QStyle.StandardPixmap.SP_MediaPlay)
        self._set_standard_icon(fill_strength_button, QStyle.StandardPixmap.SP_ArrowRight)
        self._set_standard_icon(reset_wave_button, QStyle.StandardPixmap.SP_DialogResetButton)
        controls.addWidget(calculate_button)
        controls.addWidget(fill_strength_button)
        controls.addWidget(reset_wave_button)
        controls.addStretch()
        self.simulator_status_label = self._muted("请先选择宠物")
        controls.addWidget(self.simulator_status_label)
        layout.addLayout(controls)
        self.simulator_results_grid = QGridLayout()
        self.simulator_result_values: list[tuple[QLabel, list[QLabel], list[QLabel]]] = []
        for column, title in enumerate(("0转1级", "0转99级", "0转50级", "10转99级")):
            card = QGroupBox(title)
            card_layout = QGridLayout(card)
            effort_values: list[QLabel] = []
            battle_values: list[QLabel] = []
            for row_index, label in enumerate(("体力", "力量", "耐力", "敏捷", "智力")):
                card_layout.addWidget(self._muted(label), row_index, 0)
                value = QLabel("-")
                card_layout.addWidget(value, row_index, 1)
                effort_values.append(value)
            for row_index, label in enumerate(("生命", "攻击", "防御", "速度", "魔力"), 5):
                card_layout.addWidget(self._muted(label), row_index, 0)
                value = QLabel("-")
                card_layout.addWidget(value, row_index, 1)
                battle_values.append(value)
            self.simulator_results_grid.addWidget(card, 0, column)
            self.simulator_result_values.append((card, effort_values, battle_values))  # type: ignore[arg-type]
        layout.addLayout(self.simulator_results_grid, 1)
        self._fill_simulator_strength()

    def _build_misc_tab(self) -> None:
        layout = QVBoxLayout(self.misc_tab)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._page_title("杂项操作"))
        self.fragment_quantity_label = self._muted("背包 - | 仓库 - | 合计 -")
        self.book_quantity_label = self._muted("背包 - | 仓库 - | 合计 -")
        self.exp_tree_quantity_label = self._muted("剩余经验 -")

        def operation_row(title: str, quantity: QLabel, default: str, button_text: str, callback: Callable[[], None]) -> tuple[QFrame, QLineEdit, QPushButton]:
            frame = QFrame()
            frame.setObjectName("subtlePanel")
            row = QHBoxLayout(frame)
            labels = QVBoxLayout()
            labels.addWidget(QLabel(title))
            labels.addWidget(quantity)
            row.addLayout(labels, 1)
            entry = _line(default, 90)
            button = self._button(button_text, callback, True)
            row.addWidget(entry)
            row.addWidget(button)
            entry.returnPressed.connect(callback)
            return frame, entry, button

        fragment, self.big_pill_count_entry, self.exchange_big_pill_button = operation_row("巨石碎片", self.fragment_quantity_label, "1", "兑换大丸子", self._exchange_big_pills)
        self._set_standard_icon(
            self.exchange_big_pill_button, QStyle.StandardPixmap.SP_DialogApplyButton
        )
        layout.addWidget(fragment)
        books, self.book_count_entry, self.use_book_button = operation_row("经验书", self.book_quantity_label, "1", "使用经验书", self._use_experience_books)
        self._set_standard_icon(
            self.use_book_button, QStyle.StandardPixmap.SP_DialogApplyButton
        )
        book_layout = books.layout()
        self.upgrade_ten_99_with_books_button = self._button("一键升级10转99级", self._upgrade_selected_pet_to_ten_99_with_books)
        self._set_standard_icon(
            self.upgrade_ten_99_with_books_button,
            QStyle.StandardPixmap.SP_ArrowUp,
        )
        book_layout.addWidget(self.upgrade_ten_99_with_books_button)  # type: ignore[union-attr]
        layout.addWidget(books)
        exp, self.pet_experience_entry, self.allocate_experience_button = operation_row("经验树经验", self.exp_tree_quantity_label, "1", "分配经验（不转生）", self._allocate_pet_experience)
        self._set_standard_icon(
            self.allocate_experience_button, QStyle.StandardPixmap.SP_ArrowRight
        )
        exp_layout = exp.layout()
        self.upgrade_zero_99_button = self._button("升级0转99级", self._upgrade_selected_pet_to_zero_99)
        self.allocate_all_experience_button = self._button("一键分配经验", self._allocate_all_pet_experience)
        self._set_standard_icon(
            self.upgrade_zero_99_button, QStyle.StandardPixmap.SP_ArrowUp
        )
        self._set_standard_icon(
            self.allocate_all_experience_button, QStyle.StandardPixmap.SP_ArrowRight
        )
        exp_layout.addWidget(self.upgrade_zero_99_button)  # type: ignore[union-attr]
        exp_layout.addWidget(self.allocate_all_experience_button)  # type: ignore[union-attr]
        layout.addWidget(exp)

        egg_map = QFrame()
        egg_map.setObjectName("subtlePanel")
        grid = QGridLayout(egg_map)
        grid.addWidget(QLabel("开启精灵蛋"), 0, 0)
        self.pet_egg_combo = QComboBox()
        self._polish_combo(self.pet_egg_combo)
        self.pet_egg_combo.addItems([f"{name}（{item_id}）" for item_id, name in self.pet_egg_items])
        self.pet_egg_count_entry = _line("1", 70)
        self.open_pet_eggs_button = self._button("开启精灵蛋", self._open_pet_eggs, True)
        self._set_standard_icon(
            self.open_pet_eggs_button, QStyle.StandardPixmap.SP_DialogOpenButton
        )
        grid.addWidget(self.pet_egg_combo, 1, 0)
        grid.addWidget(self.pet_egg_count_entry, 1, 1)
        grid.addWidget(self.open_pet_eggs_button, 1, 2)
        grid.addWidget(QLabel("地图传送"), 0, 3)
        self.map_combo = QComboBox()
        self._polish_combo(self.map_combo)
        self.map_combo.addItems([f"{name}（{map_id}）" for map_id, name, _x, _y in self.map_locations])
        self.teleport_button = self._button("传送", self._teleport_to_selected_map, True)
        self._set_standard_icon(
            self.teleport_button, QStyle.StandardPixmap.SP_ArrowRight
        )
        grid.addWidget(self.map_combo, 1, 3)
        grid.addWidget(self.teleport_button, 1, 4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(3, 1)
        layout.addWidget(egg_map)
        layout.addStretch()

    def _build_result_tab(self) -> None:
        layout = QVBoxLayout(self.result_tab)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._page_title("本次洗点结果"))
        self.result_summary_label = self._muted("开始洗点后，这里会展示结果与每次属性变化")
        self.result_summary_label.setWordWrap(True)
        layout.addWidget(self.result_summary_label)
        self.result_table = QTableWidget(0, 6)
        self.result_table.setHorizontalHeaderLabels(("次数", "成长", "基础五项", "战斗面板", "道具剩余", "判断"))
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setShowGrid(False)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.verticalHeader().setDefaultSectionSize(34)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.result_table, 1)

    def _mode(self) -> str:
        return "base" if self.base_mode.isChecked() else "derived"

    def _item_index(self) -> int:
        return max(0, self.item_combo.currentIndex())

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _show_error(self, title: str, error: object) -> None:
        QMessageBox.critical(self, title, str(error))

    def _run_worker(self, target: Callable[[], None]) -> None:
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _set_busy(self, action: str | None, status: str | None = None) -> None:
        self.busy_action = action
        if status is not None:
            self._set_status(status)
        self._sync_controls()

    def _connect(self) -> None:
        if self.busy_action or self.connected:
            return
        try:
            uid = int(self.uid_entry.text().strip())
            if uid <= 0:
                raise ValueError("米米号必须是正整数")
            password = self.password_entry.text()
            if not password:
                raise ValueError("请输入密码")
        except ValueError as exc:
            self._show_error("输入有误", exc)
            return
        server = self.server_combo.currentIndex()
        self._set_busy("connect", "正在连接服务器")

        def work() -> None:
            try:
                actual_server = self.backend.connect(uid, password, server)
                payload = (
                    actual_server,
                    self.backend.load_pets(),
                    self.backend.load_storage_pets(),
                    self.backend.load_item_quantities(),
                    self.backend.load_misc_item_quantities(),
                    self.backend.load_exp_tree_experience(),
                )
                self.events.put(("connected", payload))
            except Exception as exc:
                self.backend.close()
                self.events.put(("error", ("connect", exc)))

        self._run_worker(work)

    def _disconnect(self) -> None:
        if self.busy_action:
            return
        self.backend.close()
        self.connected = False
        self.bag_pets = []
        self.storage_pets = []
        self.item_quantities = {}
        self.misc_item_quantities = {}
        self.exp_tree_experience = None
        self._render_pet_list()
        self._render_storage_list()
        self._show_pet(None)
        self.connection_badge.setText("未连接")
        self.connection_badge.setStyleSheet("")
        self._set_status("已断开服务器")
        self._update_item_options()
        self._render_misc_quantities()
        self._render_exp_tree_experience()
        self._sync_controls()

    def _refresh(self) -> None:
        if not self.connected or self.busy_action:
            return
        self._set_busy("refresh", "正在刷新宠物和道具")

        def work() -> None:
            try:
                self.events.put(
                    (
                        "refreshed",
                        (
                            self.backend.load_pets(),
                            self.backend.load_storage_pets(),
                            self.backend.load_item_quantities(),
                            self.backend.load_misc_item_quantities(),
                            self.backend.load_exp_tree_experience(),
                        ),
                    )
                )
            except Exception as exc:
                self.events.put(("error", ("refresh", exc)))

        self._run_worker(work)

    def _render_pet_list(self, preferred_pet_id: int | None = None) -> None:
        current_id = self._pet_id_to_restore(preferred_pet_id)
        self.pet_count_label.setText(str(len(self.bag_pets)))
        self.pet_table.blockSignals(True)
        self.pet_table.clearSpans()
        self.pet_table.setRowCount(max(1, len(self.bag_pets)))
        if not self.bag_pets:
            empty = QTableWidgetItem(
                "连接后显示背包宠物" if not self.connected else "背包中暂无宠物"
            )
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setForeground(QColor(COLORS["muted"]))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.pet_table.setSpan(0, 0, 1, self.pet_table.columnCount())
            self.pet_table.setItem(0, 0, empty)
        for row, pet in enumerate(self.bag_pets):
            values = (
                pet["nick"],
                self.backend.pet_type_name(int(pet["type_id"])),
                pet["level"],
                pet["reincarnation_degree"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(
                    Qt.ItemDataRole.DisplayRole,
                    int(value) if column in (2, 3) else str(value),
                )
                item.setData(Qt.ItemDataRole.UserRole, int(pet["pet_id"]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.pet_table.setItem(row, column, item)
        if self.pet_sort_column is not None:
            self.pet_table.sortItems(self.pet_sort_column, self.pet_sort_order)
        selected_row = self._pet_table_row_for_id(current_id)
        if selected_row < 0 and self.bag_pets:
            selected_row = 0
        self.pet_table.blockSignals(False)
        if selected_row >= 0:
            self.pet_table.selectRow(selected_row)
            self._select_pet()
        else:
            self._show_pet(None)
        self._sync_controls()

    def _sort_pet_table(self, column: int) -> None:
        if column not in (0, 1, 2) or not self.bag_pets:
            return
        selected_pet = self._selected_bag_pet()
        selected_id = int(selected_pet["pet_id"]) if selected_pet is not None else None
        if self.pet_sort_column == column:
            self.pet_sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.pet_sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.pet_sort_column = column
            self.pet_sort_order = Qt.SortOrder.AscendingOrder
        labels = ["昵称", "种类", "等级", "转生"]
        labels[column] += (
            " ↑"
            if self.pet_sort_order == Qt.SortOrder.AscendingOrder
            else " ↓"
        )
        for index, label in enumerate(labels):
            self.pet_table.horizontalHeaderItem(index).setText(label)
        self.pet_table.sortItems(column, self.pet_sort_order)
        selected_row = self._pet_table_row_for_id(selected_id)
        if selected_row >= 0:
            self.pet_table.selectRow(selected_row)

    def _pet_table_row_for_id(self, pet_id: int | None) -> int:
        if pet_id is None:
            return -1
        for row in range(self.pet_table.rowCount()):
            item = self.pet_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == pet_id:
                return row
        return -1

    def _selected_bag_pet(self) -> dict[str, object] | None:
        selected_rows = self.pet_table.selectionModel().selectedRows()
        item = self.pet_table.item(selected_rows[0].row(), 0) if selected_rows else None
        if item is None:
            return None
        pet_id = item.data(Qt.ItemDataRole.UserRole)
        if pet_id is None:
            return None
        return next(
            (pet for pet in self.bag_pets if int(pet["pet_id"]) == int(pet_id)),
            None,
        )

    def _render_storage_list(self, preferred_pet_id: int | None = None) -> None:
        current_id = int(preferred_pet_id) if preferred_pet_id is not None else None
        if current_id is None:
            current_pet = self._selected_storage_pet()
            if current_pet is not None:
                current_id = int(current_pet["pet_id"])
        self.storage_count_label.setText(str(len(self.storage_pets)))
        self.storage_table.blockSignals(True)
        self.storage_table.clearSpans()
        self.storage_table.setRowCount(max(1, len(self.storage_pets)))
        if not self.storage_pets:
            empty = QTableWidgetItem(
                "连接后显示仓库宠物" if not self.connected else "仓库中暂无宠物"
            )
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setForeground(QColor(COLORS["muted"]))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.storage_table.setSpan(
                0, 0, 1, self.storage_table.columnCount()
            )
            self.storage_table.setItem(0, 0, empty)
        for row, pet in enumerate(self.storage_pets):
            values = (
                pet["nick"],
                self.backend.pet_type_name(int(pet["type_id"])),
                pet["level"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(
                    Qt.ItemDataRole.DisplayRole,
                    int(value) if column == 2 else str(value),
                )
                item.setData(Qt.ItemDataRole.UserRole, int(pet["pet_id"]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.storage_table.setItem(row, column, item)
        if self.storage_sort_column is not None:
            self.storage_table.sortItems(
                self.storage_sort_column, self.storage_sort_order
            )
        selected_row = self._storage_table_row_for_id(current_id)
        self.storage_table.blockSignals(False)
        if selected_row >= 0:
            self.storage_table.selectRow(selected_row)
        self._sync_controls()

    def _sort_storage_table(self, column: int) -> None:
        if column not in (0, 1, 2) or not self.storage_pets:
            return
        selected_pet = self._selected_storage_pet()
        selected_id = int(selected_pet["pet_id"]) if selected_pet is not None else None
        if self.storage_sort_column == column:
            self.storage_sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.storage_sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.storage_sort_column = column
            self.storage_sort_order = Qt.SortOrder.AscendingOrder
        labels = ["昵称", "种类", "等级"]
        labels[column] += (
            " ↑"
            if self.storage_sort_order == Qt.SortOrder.AscendingOrder
            else " ↓"
        )
        for index, label in enumerate(labels):
            self.storage_table.horizontalHeaderItem(index).setText(label)
        self.storage_table.blockSignals(True)
        self.storage_table.sortItems(column, self.storage_sort_order)
        selected_row = self._storage_table_row_for_id(selected_id)
        if selected_row >= 0:
            self.storage_table.selectRow(selected_row)
        self.storage_table.blockSignals(False)

    def _storage_table_row_for_id(self, pet_id: int | None) -> int:
        if pet_id is None:
            return -1
        for row in range(self.storage_table.rowCount()):
            item = self.storage_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == pet_id:
                return row
        return -1

    def _remember_pet_selection(self, pet: Mapping[str, object]) -> None:
        self.remembered_pet_selection = (int(self.backend.uid), int(pet["pet_id"]))

    def _pet_id_to_restore(self, preferred_pet_id: int | None) -> int | None:
        if preferred_pet_id is not None:
            return int(preferred_pet_id)
        if self.selected_pet is not None:
            return int(self.selected_pet["pet_id"])
        if self.remembered_pet_selection is None:
            return None
        uid, pet_id = self.remembered_pet_selection
        return pet_id if uid == int(self.backend.uid) else None

    def _select_pet(self) -> None:
        if self.busy_action == "wash":
            return
        pet = self._selected_bag_pet()
        if pet is None:
            return
        self.storage_table.blockSignals(True)
        self.storage_table.clearSelection()
        self.storage_table.blockSignals(False)
        self._show_pet(pet)
        self._sync_controls()

    def _select_storage_pet(self) -> None:
        if self.busy_action:
            return
        pet = self._selected_storage_pet()
        if pet is None:
            return
        self.pet_table.blockSignals(True)
        self.pet_table.clearSelection()
        self.pet_table.blockSignals(False)
        self.selected_pet = None
        self.selected_pet_location = "storage"
        self.pet_title_label.setText(str(pet["nick"]))
        self.pet_meta_label.setText(
            f"{self.backend.pet_type_name(int(pet['type_id']))}  |  等级 {pet['level']}  |  仓库宠物  |  ID {pet['pet_id']}  |  正在读取详细属性"
        )
        for value in self.metric_labels.values():
            value.setText("-")
        self._render_pet_skills(None, "正在读取技能")
        self._render_prediction(None)
        self._clear_simulator_species("正在读取仓库宠物种类与详细属性")
        self.storage_request_id += 1
        request_id = self.storage_request_id
        pet_id = int(pet["pet_id"])
        self._set_busy("storage_info", "正在读取仓库宠物详细属性")

        def work() -> None:
            try:
                self.events.put(("storage_info", (request_id, self.backend.load_pet_info(pet_id))))
            except Exception as exc:
                self.events.put(("error", ("storage_info", exc)))

        self._run_worker(work)

    def _show_pet(self, pet: Mapping[str, object] | None) -> None:
        self.selected_pet = dict(pet) if pet is not None else None
        self.selected_pet_location = "bag" if pet is not None else None
        if pet is None:
            self.pet_title_label.setText("请先选择宠物")
            self.pet_meta_label.setText("连接服务器后，从左侧宠物列表中选择")
            for value in self.metric_labels.values():
                value.setText("-")
            self._render_pet_skills(None)
            self._render_prediction(None)
            self._clear_simulator_species()
            return
        self._remember_pet_selection(pet)
        self._show_pet_detail(pet, "bag")

    def _show_pet_detail(self, pet: Mapping[str, object], location: str) -> None:
        self.selected_pet_location = location
        type_name = self.backend.pet_type_name(int(pet["type_id"]))
        location_text = pet["status"] if location == "bag" else "仓库宠物"
        self.pet_title_label.setText(str(pet["nick"]))
        self.pet_meta_label.setText(
            f"{type_name}  |  等级 {pet['level']}  |  {pet['reincarnation_degree']} 转  |  {location_text}  |  ID {pet['pet_id']}"
        )
        for key, value in self.metric_labels.items():
            value.setText(str(pet[key]))
        self._render_pet_skills(pet)
        self._render_prediction(pet)
        self._load_simulator_species(pet)

    def _render_pet_skills(
        self,
        pet: Mapping[str, object] | None,
        empty_text: str = "选择宠物后显示技能",
    ) -> None:
        rows = pet_skill_rows(pet, self.skill_names) if pet is not None else ()
        self.skill_count_label.setText(f"{len(rows)} 个" if pet is not None else "-")
        _clear_layout(self.skills_content_layout)
        if not rows:
            placeholder = self._muted("暂无技能" if pet is not None else empty_text)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.skills_content_layout.addWidget(placeholder, 0, 0, 1, 2)
            return
        for index, (name, level) in enumerate(rows):
            card = QFrame()
            card.setObjectName("skillCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(2)
            name_label = QLabel(name)
            name_label.setObjectName("skillName")
            level_label = QLabel(level)
            level_label.setObjectName("skillLevel")
            card_layout.addWidget(name_label)
            card_layout.addWidget(level_label)
            row, column = divmod(index, 2)
            self.skills_content_layout.addWidget(card, row, column)

    def _load_simulator_species(self, pet: Mapping[str, object]) -> None:
        try:
            type_name = self.backend.pet_type_name(int(pet["type_id"]))
            server = "active" if self.backend.server >= 11 else "normal"
            self.simulator_basis = find_pet_basis(type_name, server)
        except (KeyError, LookupError, OSError, TypeError, ValueError) as exc:
            self._clear_simulator_species(f"无法载入基数：{exc}")
            return
        basis = self.simulator_basis
        self.simulator_species_label.setText(f"{basis.name} · {'活动服' if server == 'active' else '普通服'} · 默认成长 {basis.growth:g}")
        for label, value in zip(self.simulator_original_labels, basis.base):
            label.setText(f"{value:g}")
        self._preview_simulator_target()

    def _clear_simulator_species(self, message: str = "请先选择宠物") -> None:
        self.simulator_basis = None
        self.simulator_species_label.setText(message)
        for label in (*self.simulator_original_labels, *self.simulator_target_labels):
            label.setText("-")
        self.simulator_status_label.setText(message)

    def _read_simulator_wave(self) -> tuple[float, ...]:
        try:
            return tuple(float(entry.text().strip()) for entry in self.simulator_wave_entries)
        except ValueError as exc:
            raise ValueError("五项波动必须是数字") from exc

    def _read_simulator_level(self) -> int:
        try:
            level = int(self.simulator_level_entry.text().strip())
        except ValueError as exc:
            raise ValueError("任意等级必须是 1 到 99 的整数") from exc
        if level not in range(1, 100):
            raise ValueError("任意等级必须是 1 到 99 的整数")
        return level

    def _read_simulator_allocations(self) -> tuple[tuple[int, ...], ...]:
        allocations = []
        limits = (98, self._read_simulator_level() - 1, 98)
        for entries, limit in zip(self.simulator_point_entries, limits):
            try:
                values = tuple(int(entry.text().strip()) for entry in entries)
            except ValueError as exc:
                raise ValueError("自由加点必须是非负整数") from exc
            if any(value < 0 for value in values) or sum(values) > limit:
                raise ValueError(f"自由加点必须非负且合计不超过 {limit}")
            allocations.append(values)
        return tuple(allocations)

    def _preview_simulator_target(self) -> None:
        if self.simulator_basis is None:
            return
        try:
            target = apply_basis_wave(self.simulator_basis.base, self._read_simulator_wave())
        except ValueError:
            for label in self.simulator_target_labels:
                label.setText("?")
            return
        for label, value in zip(self.simulator_target_labels, target):
            label.setText(f"{value:g}")

    def _update_simulator_allocation_header(self) -> None:
        try:
            level = self._read_simulator_level()
        except ValueError:
            self.simulator_allocation_headers[1].setText("0转?级\n最多?")
            return
        self.simulator_allocation_headers[1].setText(f"0转{level}级\n最多{level - 1}")

    def _fill_simulator_strength(self) -> None:
        try:
            level = self._read_simulator_level()
        except ValueError:
            level = 50
        limits = (98, level - 1, 98)
        for entries, limit in zip(self.simulator_point_entries, limits):
            for index, entry in enumerate(entries):
                entry.setText(str(limit if index == 1 else 0))

    def _reset_simulator_wave(self) -> None:
        for entry in self.simulator_wave_entries:
            entry.setText("0")
        self._preview_simulator_target()

    def _calculate_base_simulation(self) -> None:
        try:
            if self.simulator_basis is None:
                raise ValueError("请先选择有基数数据的宠物")
            target = apply_basis_wave(self.simulator_basis.base, self._read_simulator_wave())
            level = self._read_simulator_level()
            allocations = self._read_simulator_allocations()
            results = simulate_base(target, level, allocations)
        except (TypeError, ValueError) as exc:
            self.simulator_status_label.setText(f"无法计算：{exc}")
            return
        self.simulator_status_label.setText(f"目标成长 {sum(target):g} · 加点 {'/'.join(str(sum(v)) for v in allocations)}")
        titles = ("0转1级", "0转99级", f"0转{level}级", "10转99级")
        for (card, efforts, battle), title, result in zip(self.simulator_result_values, titles, results):
            card.setTitle(title)  # type: ignore[union-attr]
            for label, value in zip(efforts, result.efforts):
                label.setText(f"{value:,}")
            for label, value in zip(battle, result.battle):
                label.setText(f"{value:,}")

    def _render_targets(self) -> None:
        if not hasattr(self, "targets_layout"):
            return
        _clear_layout(self.targets_layout)
        self.target_group_entries = []
        self.target_group_checks = []
        fields = target_fields(self._item_index(), self._mode())
        hint = "；".join(f"{field.label}{field.comparison_label}" for field in fields)
        self.target_hint_label.setText(hint + "；0 表示忽略")
        for group_index in range(3):
            row = QHBoxLayout()
            check = QCheckBox(f"候选 {group_index + 1}")
            check.setChecked(group_index == 0)
            row.addWidget(check)
            entries = []
            for field in fields:
                row.addWidget(self._muted(field.label))
                entry = _line("0", 76)
                entries.append(entry)
                row.addWidget(entry)
            row.addStretch()
            self.targets_layout.addLayout(row)
            self.target_group_checks.append(check)
            self.target_group_entries.append(entries)
            check.toggled.connect(lambda checked, widgets=entries: [widget.setEnabled(checked) for widget in widgets])
            for entry in entries:
                entry.setEnabled(group_index == 0)
        self._sync_controls()

    def _fill_target_candidates(self) -> None:
        pet = self.selected_pet
        if pet is None or self.simulator_basis is None:
            return
        try:
            groups = automatic_target_groups(self.simulator_basis, pet, self._item_index(), self._mode())
        except (KeyError, TypeError, ValueError) as exc:
            self._show_error("无法一键填充", exc)
            return
        for check, entries, values in zip(self.target_group_checks, self.target_group_entries, groups):
            check.setChecked(True)
            for entry, value in zip(entries, values):
                entry.setText(str(value))

    def _start_wash(self) -> None:
        if not self.connected or self.busy_action or self.selected_pet is None:
            return
        try:
            slots: list[tuple[int, ...] | None] = []
            for check, entries in zip(self.target_group_checks, self.target_group_entries):
                if not check.isChecked():
                    slots.append(None)
                    continue
                try:
                    slots.append(tuple(int(entry.text().strip()) for entry in entries))
                except ValueError as exc:
                    raise ValueError("期望数值必须是非负整数") from exc
            config = WashConfig(
                pet_id=int(self.selected_pet["pet_id"]),
                item_index=self._item_index(),
                mode=self._mode(),
                targets=slots[0],
                alternative_targets=(slots[1], slots[2]),
                max_attempts=int(self.max_attempts_entry.text().strip()),
            )
            config.validate()
            if not is_pet_washable(self.selected_pet):
                raise ValueError("只有0转宠物可以洗点")
        except ValueError as exc:
            self._show_error("输入有误", exc)
            return
        self.stop_event.clear()
        self.result_table.setRowCount(0)
        self.result_summary_label.setText("正在洗点，请勿关闭程序")
        self.progress.setRange(0, config.max_attempts)
        self.progress.setValue(0)
        self.progress_text_label.setText(f"0 / {config.max_attempts}")
        self.notebook.setCurrentWidget(self.result_tab)
        self._set_busy("wash", "洗点任务已开始")

        def work() -> None:
            try:
                runner = RespecRunner(self.backend, self.backend.item_ids)
                result = runner.run(
                    config,
                    self.stop_event.is_set,
                    lambda progress: self.events.put(("progress", (config, progress))),
                )
                self.events.put(("finished", (config, result)))
            except Exception as exc:
                self.events.put(("error", ("wash", exc)))

        self._run_worker(work)

    def _stop_wash(self) -> None:
        if self.busy_action == "wash":
            self.stop_event.set()
            self._set_status("正在停止，将在本次请求完成后结束")

    def _selected_storage_pet(self) -> dict[str, object] | None:
        selected_rows = self.storage_table.selectionModel().selectedRows()
        item = (
            self.storage_table.item(selected_rows[0].row(), 0)
            if selected_rows
            else None
        )
        if item is None:
            return None
        pet_id = item.data(Qt.ItemDataRole.UserRole)
        if pet_id is None:
            return None
        return next(
            (
                pet
                for pet in self.storage_pets
                if int(pet["pet_id"]) == int(pet_id)
            ),
            None,
        )

    def _move_selected_pet(self, direction: str) -> None:
        if not self.connected or self.busy_action:
            return
        if direction == "to_storage":
            pet = self._selected_bag_pet()
            action_text = "放回仓库"
            mover = self.backend.move_pet_to_store
        elif direction == "to_bag":
            pet = self._selected_storage_pet()
            action_text = "取回背包"
            mover = self.backend.move_pet_to_bag
        else:
            raise ValueError(f"未知宠物移动方向：{direction}")
        if pet is None:
            return
        self._set_busy("move_pet", f"正在{action_text}")

        def work() -> None:
            try:
                mover(pet)
                self.events.put(
                    (
                        "pet_moved",
                        (
                            action_text,
                            self.backend.load_pets(),
                            self.backend.load_storage_pets(),
                            self.backend.load_item_quantities(),
                        ),
                    )
                )
            except Exception as exc:
                self.events.put(("error", ("move_pet", exc)))

        self._run_worker(work)

    def _change_selected_pet_name(self) -> None:
        if not self.connected or self.busy_action:
            return
        pet = self.selected_pet if self.selected_pet_location == "bag" else self._selected_storage_pet()
        if pet is None:
            return
        old_name = str(pet["nick"])
        entered, accepted = QInputDialog.getText(self, "修改宠物昵称", "请输入新昵称：", text=old_name)
        if not accepted:
            return
        try:
            new_name = validate_pet_nickname(entered)
        except ValueError as exc:
            self._show_error("昵称无效", exc)
            return
        if new_name == old_name:
            return
        pet_id = int(pet["pet_id"])
        location = self.selected_pet_location or "bag"
        self._set_busy("change_pet_name", f"正在将 {old_name} 改名为 {new_name}")

        def work() -> None:
            try:
                self.backend.change_pet_name(pet, new_name)
                bag_pets = self.backend.load_pets()
                storage_pets = self.backend.load_storage_pets()
                candidates = bag_pets if location == "bag" else storage_pets
                refreshed = next((item for item in candidates if int(item["pet_id"]) == pet_id), None)
                if refreshed is None or str(refreshed["nick"]) != new_name:
                    raise RuntimeError("服务端未接受宠物昵称修改")
                self.events.put(("pet_renamed", (pet_id, new_name, location, bag_pets, storage_pets)))
            except Exception as exc:
                self.events.put(("error", ("change_pet_name", exc)))

        self._run_worker(work)

    def _release_selected_pet(self) -> None:
        if not self.connected or self.busy_action:
            return
        pet = self._selected_storage_pet()
        if pet is None:
            return
        response = QMessageBox.warning(
            self,
            "确认放生宠物",
            f"确定要放生仓库宠物“{pet['nick']}”吗？\n\n等级：{pet['level']}\nID：{pet['pet_id']}\n\n放生后无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        pet_name = str(pet["nick"])
        self._set_busy("release_pet", f"正在放生 {pet_name}")

        def work() -> None:
            try:
                self.backend.release_pet(pet)
                self.events.put(
                    (
                        "pet_released",
                        (pet_name, self.backend.load_pets(), self.backend.load_storage_pets(), self.backend.load_item_quantities()),
                    )
                )
            except Exception as exc:
                self.events.put(("error", ("release_pet", exc)))

        self._run_worker(work)

    def _open_learn_skills_dialog(self) -> None:
        if not self.connected or self.busy_action or self.selected_pet is None:
            return
        pet = dict(self.selected_pet)
        current_levels = {
            int(skill["skill_id"]): int(skill["level"])
            for skill in pet.get("skills", ())
            if isinstance(skill, Mapping)
        }
        dialog = QDialog(self)
        dialog.setWindowTitle("学习技能")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{pet['nick']}  |  选择要学习的技能"))
        grid = QGridLayout()
        checks: dict[int, QCheckBox] = {}
        for index, (name, skill_id, target_level) in enumerate(PET_SKILL_OPTIONS):
            learned = current_levels.get(skill_id, 0) >= target_level
            check = QCheckBox(f"{name}  {target_level}级" + ("  已学习" if learned else ""))
            check.setEnabled(not learned)
            checks[skill_id] = check
            row, column = divmod(index, 2)
            grid.addWidget(check, row, column)
        layout.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("学习")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = tuple(skill_id for _name, skill_id, _level in PET_SKILL_OPTIONS if checks[skill_id].isChecked())
        if selected:
            self._learn_selected_pet_skills(pet, selected)

    def _learn_selected_pet_skills(self, pet: Mapping[str, object], skill_ids: tuple[int, ...]) -> None:
        pet_id = int(pet["pet_id"])
        names = [name for name, skill_id, _level in PET_SKILL_OPTIONS if skill_id in skill_ids]
        self._set_busy("learn_pet_skills", f"正在为 {pet['nick']} 学习 {len(skill_ids)} 个技能")

        def work() -> None:
            try:
                self.backend.learn_pet_skills(pet_id, skill_ids)
                bag_pets = self.backend.load_pets()
                self.events.put(("pet_skills_learned", (pet_id, tuple(names), bag_pets)))
            except Exception as exc:
                try:
                    bag_pets = self.backend.load_pets()
                except Exception:
                    bag_pets = None
                self.events.put(("pet_skills_failed", (pet_id, bag_pets, exc)))

        self._run_worker(work)

    def _prediction_cache_key(self, pet: Mapping[str, object]) -> tuple[str, int, int]:
        return (self.uid_entry.text().strip(), int(self.backend.server), int(pet["pet_id"]))

    def _render_prediction(self, pet: Mapping[str, object] | None) -> None:
        if pet is None:
            self.prediction_pet = None
            for entry in self.prediction_point_entries:
                entry.clear()
                entry.setEnabled(False)
            self.prediction_growth_entry.clear()
            self.prediction_growth_entry.setEnabled(False)
            self.prediction_calculate_button.setEnabled(False)
            self.prediction_intro_label.setText("选择宠物后，自动按默认成长和全力加点计算预测")
            self.prediction_base_label.setText("匹配到的 0 转基数：-")
            self.prediction_fit_label.clear()
            self._clear_prediction_candidates()
            return
        self.prediction_pet = dict(pet)
        cached = self.prediction_cache.get(self._prediction_cache_key(pet))
        if cached is not None:
            for entry, value in zip(self.prediction_point_entries, cached.point_values):
                entry.setText(value)
                entry.setEnabled(True)
            self.prediction_growth_entry.setText(cached.growth_value)
            self.prediction_growth_entry.setEnabled(True)
            self.prediction_calculate_button.setEnabled(True)
            self._display_prediction_cache(pet, cached)
            return
        points = (0, int(pet["attr_point_applied"]), 0, 0, 0)
        for entry, value in zip(self.prediction_point_entries, points):
            entry.setText(str(value))
            entry.setEnabled(True)
        self.prediction_calculate_button.setEnabled(True)
        self.prediction_growth_entry.setEnabled(True)
        self.prediction_growth_entry.setText(str(pet["grow_value"]) if int(pet["reincarnation_degree"]) == 0 else "")
        self.prediction_intro_label.setText(f"{pet['nick']} · 当前 {pet['reincarnation_degree']}转{pet['level']}级")
        self.prediction_fit_label.setText(f"当前已分配点数：{pet['attr_point_applied']}")
        self.prediction_base_label.setText("进入成长预测页后自动计算")
        self._clear_prediction_candidates()
        if self.notebook.currentWidget() is self.prediction_tab:
            self._calculate_prediction()

    def _on_workspace_tab_changed(self, _index: int) -> None:
        if self.notebook.currentWidget() is not self.prediction_tab or self.prediction_pet is None:
            return
        cached = self.prediction_cache.get(self._prediction_cache_key(self.prediction_pet))
        if cached is None:
            self._calculate_prediction()
        else:
            self._display_prediction_cache(self.prediction_pet, cached)

    def _prediction_basis_and_growth(self, pet: Mapping[str, object]) -> tuple[PetBasis, int, str]:
        type_name = self.backend.pet_type_name(int(pet["type_id"]))
        server = "active" if self.backend.server >= 11 else "normal"
        basis = find_pet_basis(type_name, server)
        if int(pet["reincarnation_degree"]) == 0:
            default_growth = int(pet["grow_value"])
            source = "宠物当前成长"
        else:
            default_growth = round_display(basis.growth)
            source = "基数库默认成长"
        text = self.prediction_growth_entry.text().strip()
        try:
            growth = int(text) if text else default_growth
        except ValueError as exc:
            raise ValueError("成长必须是整数") from exc
        if not text:
            self.prediction_growth_entry.setText(str(growth))
        return basis, growth, source if growth == default_growth else "手动成长"

    def _calculate_prediction(self) -> None:
        pet = self.prediction_pet
        if pet is None:
            return
        try:
            texts = [entry.text().strip() for entry in self.prediction_point_entries]
            if any(not text for text in texts):
                raise ValueError("请填写完整的五项自由加点")
            points = tuple(int(text) for text in texts)
            basis, growth, source = self._prediction_basis_and_growth(pet)
            prediction = predict_pet(pet, basis, points, growth)
            cached = PredictionCacheEntry(tuple(texts), self.prediction_growth_entry.text().strip(), growth, source, prediction, None)
        except (KeyError, LookupError, OSError, TypeError, ValueError) as exc:
            cached = PredictionCacheEntry(
                tuple(entry.text().strip() for entry in self.prediction_point_entries),
                self.prediction_growth_entry.text().strip(),
                None,
                "",
                None,
                str(exc),
            )
        self.prediction_cache[self._prediction_cache_key(pet)] = cached
        self._display_prediction_cache(pet, cached)

    def _calculate_unknown_point_prediction(self) -> None:
        pet = self.selected_pet
        if not self.connected or self.busy_action or pet is None:
            return
        try:
            if int(pet["attr_point_applied"]) <= 0:
                raise ValueError("当前宠物没有已分配的自由点，无需重置预测")
            basis, growth, source = self._prediction_basis_and_growth(pet)
        except (KeyError, LookupError, OSError, TypeError, ValueError) as exc:
            self._show_error("无法预测", exc)
            return
        original_pet = dict(pet)
        pet_id = int(pet["pet_id"])
        cache_key = self._prediction_cache_key(pet)
        self._set_busy("unknown_point_prediction", f"正在重置 {pet['nick']} 的自由点并预测，完成后会自动恢复")

        def work() -> None:
            try:
                result = predict_pet_with_unknown_points(
                    original_pet,
                    basis,
                    growth,
                    self.backend.reset_pet_attr_points,
                    self.backend.allocate_pet_attr_points,
                )
                cached = PredictionCacheEntry(
                    tuple(str(value) for value in result.source_points),
                    str(growth),
                    growth,
                    source,
                    result.prediction,
                    None,
                )
                self.events.put(("unknown_prediction_completed", (cache_key, pet_id, cached, dict(result.restored_pet))))
            except Exception as exc:
                self.events.put(("error", ("unknown_point_prediction", exc)))

        self._run_worker(work)

    def _clear_prediction_candidates(self) -> None:
        _clear_layout(self.prediction_content_layout)
        self.prediction_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def _display_prediction_cache(self, pet: Mapping[str, object], cached: PredictionCacheEntry) -> None:
        if cached.error is not None:
            self.prediction_intro_label.setText(f"暂时无法预测：{cached.error}")
            self.prediction_base_label.setText("匹配到的 0 转基数：-")
            self.prediction_fit_label.setText(f"当前已分配点数：{pet['attr_point_applied']}；点击重新计算可更新缓存")
            self._clear_prediction_candidates()
            return
        prediction = cached.prediction
        if prediction is None or cached.growth is None:
            return
        self.prediction_intro_label.setText(
            f"{pet['nick']} · 当前 {pet['reincarnation_degree']}转{pet['level']}级 · 成长 {cached.growth}（{cached.growth_source}）"
        )
        match_label = "容差候选" if prediction.used_tolerance else "十项完全一致候选"
        self.prediction_base_label.setText(f"{match_label}：{prediction.candidate_count} 套（已按网页权重排序）")
        self.prediction_fit_label.setText(f"{prediction.note}；结果已缓存，点击重新计算可更新")
        self._clear_prediction_candidates()
        scenario_names = ("0转1级", "0转99级", "10转99级")
        for index, candidate in enumerate(prediction.candidates, 1):
            section = QGroupBox(f"候选 {index}" + ("（容差）" if prediction.used_tolerance else ""))
            section_layout = QVBoxLayout(section)
            base_text = " / ".join(f"{value:g}（{wave:+g}）" for value, wave in zip(candidate.base, candidate.base_wave))
            section_layout.addWidget(self._muted(f"0转基数（体/力/耐/敏/智）：{base_text}    权重 {candidate.paths}"))
            section_layout.addWidget(self._muted("当前自由加点：" + " / ".join(str(value) for value in candidate.source_points)))
            cards = QHBoxLayout()
            for name, projected in zip(scenario_names, candidate.scenarios):
                card = QGroupBox(name)
                grid = QGridLayout(card)
                for row, (label, value) in enumerate(zip(("体力", "力量", "耐力", "敏捷", "智力"), projected.efforts)):
                    grid.addWidget(self._muted(label), row, 0)
                    grid.addWidget(QLabel(f"{value:,}"), row, 1)
                for row, (label, value) in enumerate(zip(("生命", "攻击", "防御", "速度", "魔力"), projected.battle), 5):
                    grid.addWidget(self._muted(label), row, 0)
                    grid.addWidget(QLabel(f"{value:,}"), row, 1)
                cards.addWidget(card)
            section_layout.addLayout(cards)
            self.prediction_content_layout.addWidget(section)

    @staticmethod
    def _read_misc_count(variable: Any, label: str) -> int:
        getter = getattr(variable, "text", None) or getattr(variable, "get", None)
        if getter is None:
            raise TypeError("输入控件不支持读取")
        raw = getter().strip()
        try:
            count = int(raw)
        except ValueError as exc:
            raise ValueError(f"{label}必须是正整数") from exc
        if count <= 0 or str(count) != raw:
            raise ValueError(f"{label}必须是正整数")
        return count

    def _misc_total(self, item_id: int) -> int:
        quantity = self.misc_item_quantities.get(item_id, {})
        return int(quantity.get("bag", 0)) + int(quantity.get("store", 0))

    def _render_misc_quantities(self) -> None:
        for item_id, label in (
            (GIANT_ROCK_FRAGMENT_ID, self.fragment_quantity_label),
            (EXPERIENCE_BOOK_ID, self.book_quantity_label),
        ):
            if not self.connected:
                label.setText("背包 - | 仓库 - | 合计 -")
                continue
            quantity = self.misc_item_quantities.get(item_id, {})
            bag = int(quantity.get("bag", 0))
            store = int(quantity.get("store", 0))
            label.setText(f"背包 {bag} | 仓库 {store} | 合计 {bag + store}")

    def _render_exp_tree_experience(self) -> None:
        text = "剩余经验 -" if not self.connected or self.exp_tree_experience is None else f"剩余经验 {self.exp_tree_experience}"
        self.exp_tree_quantity_label.setText(text)

    def _update_item_options(self) -> None:
        selected = self._item_index()
        self.item_combo.blockSignals(True)
        self.item_combo.clear()
        self.item_combo.addItems(
            [
                f"{name}  x {self.item_quantities.get(item_id, 0)}"
                for name, item_id in zip(ITEM_TYPES, self.backend.item_ids)
            ]
        )
        if self.item_combo.count():
            self.item_combo.setCurrentIndex(min(selected, self.item_combo.count() - 1))
        self.item_combo.blockSignals(False)

    def _exchange_big_pills(self) -> None:
        try:
            count = self._read_misc_count(self.big_pill_count_entry, "兑换数量")
            required = count * 2
            available = self._misc_total(GIANT_ROCK_FRAGMENT_ID)
            if required > available:
                raise ValueError(f"巨石碎片不足：兑换 {count} 个大丸子需要 {required} 个，当前共 {available} 个")
        except ValueError as exc:
            self._show_error("输入有误", exc)
            return
        self._run_misc_action(
            "exchange_big_pills",
            f"正在兑换 {count} 个大丸子",
            lambda: self.backend.exchange_big_pills(count),
            f"已兑换 {count} 个大丸子",
        )

    def _use_experience_books(self) -> None:
        try:
            count = self._read_misc_count(self.book_count_entry, "使用数量")
            available = self._misc_total(EXPERIENCE_BOOK_ID)
            if count > available:
                raise ValueError(f"经验书不足：准备使用 {count} 本，当前共 {available} 本")
        except ValueError as exc:
            self._show_error("输入有误", exc)
            return
        self._run_misc_action(
            "use_experience_books",
            f"正在使用 {count} 本经验书",
            lambda: self.backend.use_experience_books(count),
            f"已使用 {count} 本经验书",
        )

    def _run_misc_action(self, action: str, pending: str, operation: Callable[[], Any], completed: str) -> None:
        if not self.connected or self.busy_action:
            return
        self._set_busy(action, pending)

        def work() -> None:
            try:
                operation()
                self.events.put(
                    (
                        "misc_completed",
                        (
                            completed,
                            self.backend.load_item_quantities(),
                            self.backend.load_misc_item_quantities(),
                            self.backend.load_exp_tree_experience(),
                        ),
                    )
                )
            except Exception as exc:
                self.events.put(("error", (action, exc)))

        self._run_worker(work)

    def _allocate_pet_experience(self) -> None:
        if self.selected_pet is None:
            return
        try:
            experience = self._read_misc_count(self.pet_experience_entry, "分配经验")
        except ValueError as exc:
            self._show_error("输入有误", exc)
            return
        pet_id = int(self.selected_pet["pet_id"])
        name = str(self.selected_pet["nick"])
        self._run_exp_action(
            "allocate_pet_experience",
            f"正在向 {name} 分配 {experience} 经验（不转生）",
            lambda: self.backend.allocate_pet_experience(pet_id, experience),
            f"已向 {name} 分配 {experience} 经验（不转生）并自动加点",
            pet_id,
        )

    def _allocate_all_pet_experience(self) -> None:
        if self.selected_pet is None:
            return
        pet_id = int(self.selected_pet["pet_id"])
        name = str(self.selected_pet["nick"])
        self._run_exp_action(
            "allocate_all_pet_experience",
            f"正在向 {name} 分配经验树全部经验",
            lambda: self.backend.allocate_all_pet_experience(pet_id),
            f"已为 {name} 一键分配经验，达标时自动转生并加点",
            pet_id,
        )

    def _upgrade_selected_pet_to_zero_99(self) -> None:
        if self.selected_pet is None:
            return
        pet_id = int(self.selected_pet["pet_id"])
        name = str(self.selected_pet["nick"])
        self._run_exp_action(
            "upgrade_pet_zero_99",
            f"正在将 {name} 升级到0转99级",
            lambda: self.backend.upgrade_pet_to_zero_99(pet_id),
            f"已将 {name} 升级到0转99级并自动加点",
            pet_id,
        )

    def _upgrade_selected_pet_to_ten_99_with_books(self) -> None:
        if self.selected_pet is None:
            return
        pet_id = int(self.selected_pet["pet_id"])
        name = str(self.selected_pet["nick"])
        self._run_exp_action(
            "upgrade_pet_ten_99_with_books",
            f"正在按经验缺口使用经验书并将 {name} 升级到10转99级",
            lambda: self.backend.upgrade_pet_to_ten_99_with_books(pet_id),
            lambda count: f"已使用 {count} 本经验书并将 {name} 升级到10转99级" if count else f"经验树经验已足够，未使用经验书；已将 {name} 升级到10转99级",
            pet_id,
            refresh_misc=True,
        )

    def _run_exp_action(
        self,
        action: str,
        pending: str,
        operation: Callable[[], Any],
        completed: str | Callable[[Any], str],
        pet_id: int,
        refresh_misc: bool = False,
    ) -> None:
        if not self.connected or self.busy_action:
            return
        self._set_busy(action, pending)

        def work() -> None:
            try:
                result = operation()
                completion = completed(result) if callable(completed) else completed
                self.events.put(
                    (
                        "exp_completed",
                        (
                            completion,
                            pet_id,
                            self.backend.load_pets(),
                            self.backend.load_exp_tree_experience(),
                            self.backend.load_misc_item_quantities() if refresh_misc else None,
                        ),
                    )
                )
            except Exception as exc:
                self.events.put(("error", (action, exc)))

        self._run_worker(work)

    def _open_pet_eggs(self) -> None:
        index = self.pet_egg_combo.currentIndex()
        if index not in range(len(self.pet_egg_items)):
            self._show_error("输入有误", "请选择精灵蛋")
            return
        try:
            count = self._read_misc_count(self.pet_egg_count_entry, "开蛋数量")
        except ValueError as exc:
            self._show_error("输入有误", exc)
            return
        item_id, name = self.pet_egg_items[index]
        if not self.connected or self.busy_action:
            return
        self._set_busy("open_pet_eggs", f"正在开启 {count} 个{name}")

        def work() -> None:
            try:
                self.backend.open_pet_eggs(item_id, count)
                self.events.put(
                    (
                        "pet_eggs_opened",
                        (f"已开启 {count} 个{name}", self.backend.load_pets(), self.backend.load_exp_tree_experience()),
                    )
                )
            except Exception as exc:
                self.events.put(("error", ("open_pet_eggs", exc)))

        self._run_worker(work)

    def _teleport_to_selected_map(self) -> None:
        index = self.map_combo.currentIndex()
        if index not in range(len(self.map_locations)):
            self._show_error("输入有误", "请选择地图")
            return
        map_id, name, x, y = self.map_locations[index]
        if not self.connected or self.busy_action:
            return
        self._set_busy("teleport", f"正在传送到{name}")

        def work() -> None:
            try:
                self.backend.teleport_to_map(map_id, x, y)
                self.events.put(("teleport_completed", f"已发送前往{name}（{map_id}）的传送请求"))
            except Exception as exc:
                self.events.put(("error", ("teleport", exc)))

        self._run_worker(work)

    def _sync_controls(self) -> None:
        if not hasattr(self, "connect_button"):
            return
        idle = self.busy_action is None
        bag_selected = self.selected_pet is not None and self.selected_pet_location == "bag"
        storage_selected = self.selected_pet_location == "storage" and self.storage_table.currentRow() >= 0
        self.connect_button.setEnabled(idle and not self.connected)
        self.disconnect_button.setEnabled(idle and self.connected)
        self.refresh_button.setEnabled(idle and self.connected)
        pet_selection_enabled = self.busy_action != "wash"
        self.pet_table.setEnabled(pet_selection_enabled)
        self.storage_table.setEnabled(pet_selection_enabled)
        for widget in (self.uid_entry, self.password_entry, self.server_combo, self.show_password):
            widget.setEnabled(idle and not self.connected)
        self.change_pet_name_button.setEnabled(idle and self.connected and (bag_selected or storage_selected))
        self.to_storage_button.setEnabled(idle and self.connected and bag_selected)
        self.to_bag_button.setEnabled(idle and self.connected and storage_selected)
        self.release_pet_button.setEnabled(idle and self.connected and storage_selected)
        self.learn_skills_button.setEnabled(idle and self.connected and bag_selected)
        self.start_button.setEnabled(
            idle and self.connected and bag_selected and self.selected_pet is not None and is_pet_washable(self.selected_pet)
        )
        self.stop_button.setEnabled(self.busy_action == "wash")
        self.fill_targets_button.setEnabled(
            idle and self.connected and bag_selected and self.simulator_basis is not None and self._item_index() not in (0, 1)
        )
        self.exchange_big_pill_button.setEnabled(idle and self.connected and self._misc_total(GIANT_ROCK_FRAGMENT_ID) >= 2)
        self.use_book_button.setEnabled(idle and self.connected and self._misc_total(EXPERIENCE_BOOK_ID) >= 1)
        has_exp = idle and self.connected and bag_selected and bool(self.exp_tree_experience)
        self.pet_experience_entry.setEnabled(has_exp)
        self.allocate_experience_button.setEnabled(has_exp)
        selected = self.selected_pet
        done = bool(selected and int(selected["reincarnation_degree"]) == 10 and int(selected["level"]) >= 99)
        self.allocate_all_experience_button.setEnabled(has_exp and not done)
        self.upgrade_zero_99_button.setEnabled(
            has_exp and selected is not None and int(selected["reincarnation_degree"]) == 0 and int(selected["level"]) < 99
        )
        self.upgrade_ten_99_with_books_button.setEnabled(
            idle and self.connected and bag_selected and not done and (bool(self.exp_tree_experience) or self._misc_total(EXPERIENCE_BOOK_ID) > 0)
        )
        for widget in (self.pet_egg_combo, self.pet_egg_count_entry, self.open_pet_eggs_button, self.map_combo, self.teleport_button):
            widget.setEnabled(idle and self.connected)
        self.prediction_unknown_button.setEnabled(
            idle and self.connected and bag_selected and selected is not None and int(selected["attr_point_applied"]) > 0
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            return

    def _handle_event(self, event: str, payload: Any) -> None:
        if event == "connected":
            server, self.bag_pets, self.storage_pets, self.item_quantities, self.misc_item_quantities, self.exp_tree_experience = payload
            self.connected = True
            self._set_busy(None)
            self.connection_badge.setText(f"已连接 {server} 服")
            self.connection_badge.setStyleSheet(f"background:#dcefe9;color:{COLORS['success']};padding:7px 13px;border-radius:6px;font-weight:600")
            self._set_status(f"连接成功，背包 {len(self.bag_pets)} 只，仓库 {len(self.storage_pets)} 只")
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            self._render_misc_quantities()
            self._render_exp_tree_experience()
            return
        if event == "refreshed":
            self.bag_pets, self.storage_pets, self.item_quantities, self.misc_item_quantities, self.exp_tree_experience = payload
            self._set_busy(None, f"刷新完成，背包 {len(self.bag_pets)} 只，仓库 {len(self.storage_pets)} 只")
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            self._render_misc_quantities()
            self._render_exp_tree_experience()
            return
        if event == "misc_completed":
            text, self.item_quantities, self.misc_item_quantities, self.exp_tree_experience = payload
            self._set_busy(None, text)
            self._update_item_options()
            self._render_misc_quantities()
            self._render_exp_tree_experience()
            return
        if event == "exp_completed":
            text, pet_id, self.bag_pets, self.exp_tree_experience, misc = payload
            if misc is not None:
                self.misc_item_quantities = misc
            self._set_busy(None, text)
            self._render_pet_list(pet_id)
            self._render_exp_tree_experience()
            self._render_misc_quantities()
            return
        if event == "pet_eggs_opened":
            text, self.bag_pets, self.exp_tree_experience = payload
            self._set_busy(None, text)
            self._render_pet_list()
            self._render_exp_tree_experience()
            return
        if event == "teleport_completed":
            self._set_busy(None, payload)
            return
        if event == "storage_info":
            request_id, pet = payload
            if request_id != self.storage_request_id:
                return
            for index, listed in enumerate(self.storage_pets):
                if int(listed["pet_id"]) == int(pet["pet_id"]):
                    self.storage_pets[index] = pet
                    break
            self._set_busy(None, "仓库宠物详细属性读取完成")
            self._show_pet_detail(pet, "storage")
            return
        if event == "unknown_prediction_completed":
            cache_key, pet_id, cached, restored = payload
            self.prediction_cache[cache_key] = cached
            self.bag_pets = [restored if int(pet["pet_id"]) == pet_id else pet for pet in self.bag_pets]
            self._set_busy(None, f"未知加点预测完成，{restored['nick']} 的原自由加点已恢复")
            self._render_pet_list(pet_id)
            return
        if event == "pet_moved":
            action, self.bag_pets, self.storage_pets, self.item_quantities = payload
            self.selected_pet = None
            self.selected_pet_location = None
            self._set_busy(None, f"{action}完成")
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            return
        if event == "pet_released":
            name, self.bag_pets, self.storage_pets, self.item_quantities = payload
            self.selected_pet = None
            self.selected_pet_location = None
            self._set_busy(None, f"已放生 {name}")
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            return
        if event == "pet_renamed":
            pet_id, name, location, self.bag_pets, self.storage_pets = payload
            self.selected_pet = None
            self._set_busy(None, f"宠物昵称已修改为 {name}")
            self._render_pet_list(pet_id if location == "bag" else None)
            self._render_storage_list(pet_id if location == "storage" else None)
            if location == "storage":
                self._select_storage_pet()
            return
        if event == "pet_skills_learned":
            pet_id, names, self.bag_pets = payload
            self._set_busy(None, f"已学习技能：{'、'.join(names)}")
            self._render_pet_list(pet_id)
            return
        if event == "pet_skills_failed":
            pet_id, bag_pets, exc = payload
            if bag_pets is not None:
                self.bag_pets = bag_pets
            self._set_busy(None)
            if bag_pets is not None:
                self._render_pet_list(pet_id)
            self._set_status(f"学习技能失败：{exc}")
            self._show_error("学习技能失败", exc)
            return
        if event == "progress":
            self._handle_progress(*payload)
            return
        if event == "finished":
            config, result = payload
            self.bag_pets = [dict(result.final_pet) if int(pet["pet_id"]) == config.pet_id else pet for pet in self.bag_pets]
            self.item_quantities[self.backend.item_ids[config.item_index]] = result.remaining_items
            self._set_busy(None)
            self._render_pet_list(config.pet_id)
            self._update_item_options()
            self._restore_wash_rule(config)
            self._handle_result(config, result)
            return
        if event == "error":
            action, exc = payload
            self._set_busy(None)
            if action == "connect":
                self.connected = False
                self.connection_badge.setText("连接失败")
                self.connection_badge.setStyleSheet(f"background:#f4e3e3;color:{COLORS['danger']};padding:7px 13px;border-radius:6px;font-weight:600")
            self._set_status(f"操作失败：{exc}")
            self._show_error("操作失败", exc)

    def _handle_progress(self, config: WashConfig, progress: WashProgress) -> None:
        pet = progress.pet
        self.progress.setValue(progress.attempt)
        self.progress_text_label.setText(f"{progress.attempt} / {config.max_attempts}")
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        values = (
            progress.attempt,
            pet["grow_value"],
            " / ".join(str(pet[key]) for key, _ in BASE_FIELDS),
            " / ".join(str(pet[key]) for key, _ in DERIVED_FIELDS),
            progress.remaining_items,
            "已达标" if progress.reached else "未达标",
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if progress.reached:
                item.setForeground(QColor(COLORS["success"]))
            self.result_table.setItem(row, column, item)
        self.result_table.scrollToBottom()
        self._set_status(f"第 {progress.attempt} 次完成，{ITEM_TYPES[config.item_index]}剩余 {progress.remaining_items} 个")

    def _handle_result(self, config: WashConfig, result: WashResult) -> None:
        reason = RESULT_REASON.get(result.reason, result.reason)
        self.progress.setValue(result.attempts)
        self.progress_text_label.setText(f"{result.attempts} / {config.max_attempts}")
        changes = []
        for field in target_fields(config.item_index, config.mode):
            changes.append(f"{field.label} {int(result.initial_pet[field.key])} -> {int(result.final_pet[field.key])}")
        self.result_summary_label.setText(
            f"{reason}。共洗点 {result.attempts} 次，道具剩余 {result.remaining_items} 个。" + "  ".join(changes)
        )
        self._set_status(reason)

    def _restore_wash_rule(self, config: WashConfig) -> None:
        self.item_combo.setCurrentIndex(config.item_index)
        self.base_mode.setChecked(config.mode == "base")
        self.derived_mode.setChecked(config.mode == "derived")
        self._render_targets()
        for check, entries, targets in zip(self.target_group_checks, self.target_group_entries, config.target_slots):
            check.setChecked(targets is not None)
            if targets is not None:
                for entry, value in zip(entries, targets):
                    entry.setText(str(value))
        self.max_attempts_entry.setText(str(config.max_attempts))
        self.notebook.setCurrentWidget(self.result_tab)
        self._sync_controls()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.busy_action == "unknown_point_prediction":
            QMessageBox.warning(self, "正在恢复宠物加点", "未知加点预测尚未完成，必须等待原自由加点恢复后才能关闭。")
            event.ignore()
            return
        self.stop_event.set()
        self.backend.close()
        event.accept()


def run() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("摩尔勇士宠物洗点工具")
    app.setStyle("Fusion")
    window = PetRespecApp()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    run()
