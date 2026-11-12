"""Tkinter interface for the Mole Warriors pet respec workflow."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Mapping

from pet_respec import (
    BASE_FIELDS,
    DERIVED_FIELDS,
    ITEM_TYPES,
    RespecRunner,
    WashConfig,
    WashProgress,
    WashResult,
    target_fields,
)
from pet_prediction import (
    PetBasis,
    PetPrediction,
    apply_basis_wave,
    find_pet_basis,
    predict_pet,
    round_display,
    simulate_base,
)
from protocol_backend import ProtocolBackend


COLORS = {
    "background": "#f3f6f7",
    "surface": "#ffffff",
    "surface_alt": "#edf3f4",
    "text": "#172b2f",
    "muted": "#5c7074",
    "line": "#d5e0e2",
    "accent": "#087f78",
    "accent_dark": "#05645f",
    "success": "#17735c",
    "warning": "#9a5a12",
    "danger": "#a23b3b",
}


RESULT_REASON = {
    "target": "已达到期望值",
    "limit": "已达到次数上限",
    "inventory": "道具数量不足",
    "stopped": "已由用户停止",
}


@dataclass(frozen=True)
class PredictionCacheEntry:
    point_values: tuple[str, ...]
    growth_value: str
    growth: int | None
    growth_source: str
    prediction: PetPrediction | None
    error: str | None


@dataclass(frozen=True)
class SimulatorResultView:
    card: ttk.Frame
    title: tk.StringVar
    efforts: tuple[tk.StringVar, ...]
    battle: tuple[tk.StringVar, ...]


class PetRespecApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.backend = ProtocolBackend()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.busy_action: str | None = None
        self.connected = False
        self.bag_pets: list[dict[str, object]] = []
        self.storage_pets: list[dict[str, object]] = []
        self.pet_by_row: dict[str, dict[str, object]] = {}
        self.storage_pet_by_row: dict[str, dict[str, object]] = {}
        self.selected_pet: dict[str, object] | None = None
        self.storage_request_id = 0
        self.item_quantities: dict[int, int] = {}
        self.target_entries: list[tk.StringVar] = []
        self.rendered_target_rule: tuple[int, str] | None = None
        self.simulator_basis: PetBasis | None = None
        self.prediction_pet: dict[str, object] | None = None
        self.prediction_cache: dict[
            tuple[str, int, int], PredictionCacheEntry
        ] = {}

        self._configure_root()
        self._configure_styles()
        self._build_ui()
        self._render_targets()
        self._sync_controls()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_root(self) -> None:
        self.root.title("摩尔勇士宠物洗点工具")
        self.root.geometry("1180x780")
        self.root.minsize(980, 740)
        self.root.configure(background=COLORS["background"])

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("App.TFrame", background=COLORS["background"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("Alt.TFrame", background=COLORS["surface_alt"])
        style.configure(
            "Title.TLabel",
            background=COLORS["background"],
            foreground=COLORS["text"],
            font=("Segoe UI", 20, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=COLORS["background"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "CreditTitle.TLabel",
            background=COLORS["background"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "CreditValue.TLabel",
            background=COLORS["background"],
            foreground=COLORS["accent_dark"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "Section.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "Body.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "AltSection.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "AltBody.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "CompactValue.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "MetricLabel.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "MetricValue.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "PredictionValue.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Accent.TButton",
            font=("Microsoft YaHei UI", 9, "bold"),
            foreground="#ffffff",
            background=COLORS["accent"],
            padding=(14, 7),
        )
        style.map(
            "Accent.TButton",
            background=[("active", COLORS["accent_dark"]), ("disabled", "#9cb7b5")],
            foreground=[("disabled", "#f4f7f7")],
        )
        style.configure("Tool.TButton", padding=(10, 6))
        style.configure("Danger.TButton", padding=(12, 7), foreground=COLORS["danger"])
        style.configure(
            "Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            rowheight=30,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            font=("Microsoft YaHei UI", 9, "bold"),
            foreground=COLORS["muted"],
        )
        style.map(
            "Treeview",
            background=[("selected", "#d9eeeb")],
            foreground=[("selected", COLORS["text"])],
        )
        style.configure("TNotebook", background=COLORS["surface"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.configure("TProgressbar", background=COLORS["accent"])

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame", padding=(18, 14, 18, 14))
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell, style="App.TFrame")
        header.pack(fill="x", pady=(0, 12))
        title_box = ttk.Frame(header, style="App.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="宠物洗点", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="选择宠物与洗点目标，实时查看每次结果",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        self.connection_badge = tk.Label(
            header,
            text="未连接",
            bg="#e7edef",
            fg=COLORS["muted"],
            padx=12,
            pady=6,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.connection_badge.pack(side="right")
        credit_box = ttk.Frame(header, style="App.TFrame")
        credit_box.pack(side="right", padx=(0, 18))
        ttk.Label(credit_box, text="鸣谢", style="CreditTitle.TLabel").pack(
            anchor="e"
        )
        ttk.Label(
            credit_box,
            text="感谢艾德华大佬支持",
            style="CreditValue.TLabel",
        ).pack(anchor="e", pady=(1, 0))

        self._build_connection(shell)

        panes = ttk.Panedwindow(shell, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(12, 0))
        left = ttk.Frame(panes, style="Surface.TFrame", padding=14, width=330)
        right = ttk.Frame(panes, style="Surface.TFrame", padding=14)
        panes.add(left, weight=1)
        panes.add(right, weight=3)
        self._build_pet_list(left)
        self._build_workspace(right)

        footer = ttk.Frame(shell, style="App.TFrame")
        footer.pack(fill="x", pady=(10, 0))
        self.status_var = tk.StringVar(value="请输入账号并连接服务器")
        ttk.Label(footer, textvariable=self.status_var, style="Subtitle.TLabel").pack(
            side="left"
        )
        ttk.Label(
            footer,
            text="密码仅用于本次登录，不会保存",
            style="Subtitle.TLabel",
        ).pack(side="right")

    def _build_connection(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Surface.TFrame", padding=(14, 12))
        panel.pack(fill="x")
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(3, weight=1)

        ttk.Label(panel, text="米米号", style="Body.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.uid_var = tk.StringVar()
        self.uid_entry = ttk.Entry(panel, textvariable=self.uid_var, width=18)
        self.uid_entry.grid(row=0, column=1, sticky="ew", padx=(0, 14))

        ttk.Label(panel, text="密码", style="Body.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(
            panel, textvariable=self.password_var, show="*", width=22
        )
        self.password_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        self.show_password_var = tk.BooleanVar(value=False)
        self.show_password = ttk.Checkbutton(
            panel,
            text="显示",
            variable=self.show_password_var,
            command=self._toggle_password,
        )
        self.show_password.grid(row=0, column=4, padx=(0, 14))

        ttk.Label(panel, text="服务器", style="Body.TLabel").grid(
            row=0, column=5, sticky="w", padx=(0, 8)
        )
        server_values = ["随机活动服 (11-20)"] + [f"{value} 服" for value in range(1, 21)]
        self.server_combo = ttk.Combobox(
            panel, values=server_values, state="readonly", width=18
        )
        self.server_combo.current(0)
        self.server_combo.grid(row=0, column=6, padx=(0, 14))

        self.connect_button = ttk.Button(
            panel, text="连接", style="Accent.TButton", command=self._connect
        )
        self.connect_button.grid(row=0, column=7)
        self.disconnect_button = ttk.Button(
            panel, text="断开", style="Tool.TButton", command=self._disconnect
        )
        self.disconnect_button.grid(row=0, column=8, padx=(8, 0))
        self.uid_entry.bind("<Return>", lambda _event: self._connect())
        self.password_entry.bind("<Return>", lambda _event: self._connect())

    def _build_pet_list(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(4, weight=1)

        heading = ttk.Frame(parent, style="Surface.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(heading, text="背包宠物", style="Section.TLabel").pack(side="left")
        self.refresh_button = ttk.Button(
            heading, text="刷新", style="Tool.TButton", command=self._refresh
        )
        self.refresh_button.pack(side="right")

        bag_container = ttk.Frame(parent, style="Surface.TFrame")
        bag_container.grid(row=1, column=0, sticky="nsew")
        bag_container.columnconfigure(0, weight=1)
        bag_container.rowconfigure(0, weight=1)
        self.pet_tree = ttk.Treeview(
            bag_container,
            columns=("type", "level", "rebirth"),
            show="tree headings",
            selectmode="browse",
        )
        self.pet_tree.heading("#0", text="昵称", anchor="center")
        self.pet_tree.heading("type", text="种类", anchor="center")
        self.pet_tree.heading("level", text="等级", anchor="center")
        self.pet_tree.heading("rebirth", text="转生", anchor="center")
        self.pet_tree.column("#0", width=110, minwidth=80, anchor="center")
        self.pet_tree.column("type", width=105, minwidth=80, anchor="center")
        self.pet_tree.column("level", width=50, minwidth=45, anchor="center")
        self.pet_tree.column("rebirth", width=50, minwidth=45, anchor="center")
        bag_scrollbar = ttk.Scrollbar(
            bag_container, orient="vertical", command=self.pet_tree.yview
        )
        self.pet_tree.configure(yscrollcommand=bag_scrollbar.set)
        self.pet_tree.grid(row=0, column=0, sticky="nsew")
        bag_scrollbar.grid(row=0, column=1, sticky="ns")
        self.pet_tree.bind("<<TreeviewSelect>>", self._select_pet)

        self.pet_list_note = tk.Label(
            bag_container,
            text="连接后显示宠物背包",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.pet_list_note.place(relx=0.5, rely=0.5, anchor="center")

        ttk.Separator(parent).grid(row=2, column=0, sticky="ew", pady=10)
        storage_heading = ttk.Frame(parent, style="Surface.TFrame")
        storage_heading.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(storage_heading, text="仓库宠物", style="Section.TLabel").pack(
            side="left"
        )

        storage_container = ttk.Frame(parent, style="Surface.TFrame")
        storage_container.grid(row=4, column=0, sticky="nsew")
        storage_container.columnconfigure(0, weight=1)
        storage_container.rowconfigure(0, weight=1)
        self.storage_tree = ttk.Treeview(
            storage_container,
            columns=("type", "level"),
            show="tree headings",
            selectmode="browse",
        )
        for column, label, width in (
            ("#0", "昵称", 110),
            ("type", "种类", 105),
            ("level", "等级", 50),
        ):
            self.storage_tree.heading(column, text=label, anchor="center")
            self.storage_tree.column(
                column,
                width=width,
                minwidth=45 if column == "level" else 80,
                anchor="center",
            )
        storage_scrollbar = ttk.Scrollbar(
            storage_container, orient="vertical", command=self.storage_tree.yview
        )
        self.storage_tree.configure(yscrollcommand=storage_scrollbar.set)
        self.storage_tree.grid(row=0, column=0, sticky="nsew")
        storage_scrollbar.grid(row=0, column=1, sticky="ns")
        self.storage_tree.bind("<<TreeviewSelect>>", self._select_storage_pet)
        self.storage_list_note = tk.Label(
            storage_container,
            text="连接后显示宠物仓库",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.storage_list_note.place(relx=0.5, rely=0.5, anchor="center")

    def _build_workspace(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        operation_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(2, 10))
        prediction_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(2, 10))
        simulator_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(2, 10))
        result_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(2, 10))
        notebook.add(operation_tab, text="洗点设置")
        notebook.add(prediction_tab, text="成长预测")
        notebook.add(simulator_tab, text="基数与洗宠模拟")
        notebook.add(result_tab, text="洗点结果")
        self.notebook = notebook
        self.prediction_tab = prediction_tab
        self.result_tab = result_tab
        self._build_operation_tab(operation_tab)
        self._build_prediction_tab(prediction_tab)
        self._build_simulator_tab(simulator_tab)
        self._build_result_tab(result_tab)
        notebook.bind("<<NotebookTabChanged>>", self._on_workspace_tab_changed)

    def _build_prediction_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="等级与转生预测", style="Section.TLabel").pack(
            anchor="w"
        )
        self.prediction_intro_var = tk.StringVar(
            value="选择宠物后，自动按默认成长和全力加点计算预测"
        )
        ttk.Label(
            parent, textvariable=self.prediction_intro_var, style="Muted.TLabel"
        ).pack(anchor="w", pady=(4, 12))

        base_box = ttk.Frame(parent, style="Alt.TFrame", padding=(12, 10))
        base_box.pack(fill="x", pady=(0, 12))
        points_row = ttk.Frame(base_box, style="Alt.TFrame")
        points_row.pack(fill="x", pady=(0, 7))
        ttk.Label(points_row, text="当前自由加点", style="AltBody.TLabel").pack(
            side="left", padx=(0, 8)
        )
        self.prediction_point_vars: list[tk.StringVar] = []
        self.prediction_point_entries: list[ttk.Entry] = []
        for label in ("体", "力", "耐", "敏", "智"):
            ttk.Label(points_row, text=label, style="Muted.TLabel").pack(side="left")
            variable = tk.StringVar(value="")
            entry = ttk.Entry(
                points_row,
                textvariable=variable,
                width=5,
                state="disabled",
            )
            entry.pack(side="left", padx=(3, 7))
            self.prediction_point_vars.append(variable)
            self.prediction_point_entries.append(entry)
        ttk.Label(points_row, text="成长", style="Muted.TLabel").pack(
            side="left", padx=(4, 0)
        )
        self.prediction_growth_var = tk.StringVar(value="")
        self.prediction_growth_entry = ttk.Entry(
            points_row,
            textvariable=self.prediction_growth_var,
            width=7,
            state="disabled",
        )
        self.prediction_growth_entry.pack(side="left", padx=(3, 7))
        self.prediction_calculate_button = ttk.Button(
            points_row,
            text="重新计算",
            style="Tool.TButton",
            command=self._calculate_prediction,
            state="disabled",
        )
        self.prediction_calculate_button.pack(side="left", padx=(3, 0))
        self.prediction_base_var = tk.StringVar(value="十项完全一致的 0 转基数：-")
        ttk.Label(
            base_box, textvariable=self.prediction_base_var, style="AltBody.TLabel"
        ).pack(anchor="w")
        self.prediction_fit_var = tk.StringVar(value="")
        ttk.Label(
            base_box, textvariable=self.prediction_fit_var, style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 0))

        results = ttk.Frame(parent, style="Surface.TFrame")
        results.pack(fill="both", expand=True)
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.prediction_canvas = tk.Canvas(
            results,
            background=COLORS["surface"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.prediction_scrollbar = ttk.Scrollbar(
            results, orient="vertical", command=self.prediction_canvas.yview
        )
        self.prediction_canvas.configure(yscrollcommand=self.prediction_scrollbar.set)
        self.prediction_canvas.grid(row=0, column=0, sticky="nsew")
        self.prediction_scrollbar.grid(row=0, column=1, sticky="ns")
        self.prediction_scrollbar.grid_remove()
        self.prediction_content = ttk.Frame(
            self.prediction_canvas, style="Surface.TFrame"
        )
        self.prediction_canvas_window = self.prediction_canvas.create_window(
            (0, 0), window=self.prediction_content, anchor="nw"
        )
        self.prediction_content.bind(
            "<Configure>",
            self._on_prediction_content_configure,
        )
        self.prediction_canvas.bind(
            "<Configure>",
            self._on_prediction_canvas_configure,
        )
        self.prediction_canvas.bind(
            "<MouseWheel>",
            lambda event: self.prediction_canvas.yview_scroll(
                int(-event.delta / 120), "units"
            ),
        )

        ttk.Label(
            parent,
            text="预测口径与 V14 规则一致：使用手动填写的五项自由加点，从任意等级和转生次数搜索基数，当前五项努力值与五项战斗值必须全部完全一致。",
            style="Muted.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(12, 0))

    def _build_simulator_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="基数与洗宠模拟", style="Section.TLabel").pack(
            anchor="w"
        )
        self.simulator_species_var = tk.StringVar(
            value="选择宠物后，从本地基数库载入对应服务器的原始基数"
        )
        ttk.Label(
            parent, textvariable=self.simulator_species_var, style="Muted.TLabel"
        ).pack(anchor="w", pady=(4, 10))

        editors = ttk.Frame(parent, style="Surface.TFrame")
        editors.pack(fill="x")
        editors.columnconfigure(0, weight=1, uniform="simulator_editor")
        editors.columnconfigure(1, weight=1, uniform="simulator_editor")

        basis_box = ttk.Frame(editors, style="Alt.TFrame", padding=(10, 8))
        basis_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        allocation_box = ttk.Frame(editors, style="Alt.TFrame", padding=(10, 8))
        allocation_box.grid(row=0, column=1, sticky="nsew")

        for column, text in enumerate(("属性", "原始基数", "输入波动", "目标基数")):
            basis_box.columnconfigure(column, weight=1)
            ttk.Label(basis_box, text=text, style="MetricLabel.TLabel").grid(
                row=0, column=column, sticky="w", padx=3
            )

        labels = ("体力", "力量", "耐力", "敏捷", "智力")
        self.simulator_original_vars: list[tk.StringVar] = []
        self.simulator_wave_vars: list[tk.StringVar] = []
        self.simulator_target_vars: list[tk.StringVar] = []
        for row, label in enumerate(labels, start=1):
            ttk.Label(basis_box, text=label, style="MetricLabel.TLabel").grid(
                row=row, column=0, sticky="w", padx=3, pady=2
            )
            original = tk.StringVar(value="-")
            ttk.Label(
                basis_box, textvariable=original, style="AltBody.TLabel"
            ).grid(row=row, column=1, sticky="w", padx=3)
            wave = tk.StringVar(value="0")
            ttk.Entry(
                basis_box,
                textvariable=wave,
                width=8,
            ).grid(row=row, column=2, sticky="ew", padx=3)
            target = tk.StringVar(value="-")
            ttk.Label(
                basis_box, textvariable=target, style="AltBody.TLabel"
            ).grid(row=row, column=3, sticky="w", padx=3)
            self.simulator_original_vars.append(original)
            self.simulator_wave_vars.append(wave)
            self.simulator_target_vars.append(target)

        self.simulator_allocation_header_vars = (
            tk.StringVar(value="0转99级\n最多98"),
            tk.StringVar(value="0转50级\n最多49"),
            tk.StringVar(value="10转99级\n最多98"),
        )
        ttk.Label(allocation_box, text="属性", style="MetricLabel.TLabel").grid(
            row=0, column=0, sticky="w", padx=3
        )
        for column, header in enumerate(self.simulator_allocation_header_vars, start=1):
            allocation_box.columnconfigure(column, weight=1)
            ttk.Label(
                allocation_box,
                textvariable=header,
                style="MetricLabel.TLabel",
                justify="center",
            ).grid(row=0, column=column, padx=3)

        self.simulator_point_vars: list[list[tk.StringVar]] = [[], [], []]
        for row, label in enumerate(labels, start=1):
            ttk.Label(allocation_box, text=label, style="MetricLabel.TLabel").grid(
                row=row, column=0, sticky="w", padx=3, pady=2
            )
            for scenario_index in range(3):
                value = tk.StringVar(value="0")
                ttk.Entry(
                    allocation_box,
                    textvariable=value,
                    width=6,
                ).grid(row=row, column=scenario_index + 1, sticky="ew", padx=3)
                self.simulator_point_vars[scenario_index].append(value)

        for variable in self.simulator_wave_vars:
            variable.trace_add("write", lambda *_args: self._preview_simulator_target())

        controls = ttk.Frame(parent, style="Surface.TFrame")
        controls.pack(fill="x", pady=(8, 8))
        ttk.Label(controls, text="0转任意等级", style="Body.TLabel").pack(
            side="left"
        )
        self.simulator_level_var = tk.StringVar(value="50")
        ttk.Entry(
            controls,
            textvariable=self.simulator_level_var,
            width=7,
        ).pack(side="left", padx=(8, 14))
        self.simulator_level_var.trace_add(
            "write", lambda *_args: self._update_simulator_allocation_header()
        )
        ttk.Button(
            controls,
            text="计算模拟",
            style="Accent.TButton",
            command=self._calculate_base_simulation,
        ).pack(side="left")
        ttk.Button(
            controls,
            text="一键全加力量",
            style="Tool.TButton",
            command=self._fill_simulator_strength,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            controls,
            text="清空波动",
            style="Tool.TButton",
            command=self._reset_simulator_wave,
        ).pack(side="left", padx=(8, 0))
        self.simulator_status_var = tk.StringVar(value="请先选择宠物")
        ttk.Label(
            controls, textvariable=self.simulator_status_var, style="Muted.TLabel"
        ).pack(side="right")

        result_area = ttk.Frame(parent, style="Surface.TFrame")
        result_area.pack(fill="both", expand=True)
        result_area.columnconfigure(0, weight=1)
        result_area.rowconfigure(0, weight=1)
        self.simulator_canvas = tk.Canvas(
            result_area,
            background=COLORS["surface"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.simulator_scrollbar = ttk.Scrollbar(
            result_area, orient="vertical", command=self.simulator_canvas.yview
        )
        self.simulator_canvas.configure(
            yscrollcommand=self.simulator_scrollbar.set
        )
        self.simulator_canvas.grid(row=0, column=0, sticky="nsew")
        self.simulator_scrollbar.grid(row=0, column=1, sticky="ns")
        self.simulator_scrollbar.grid_remove()
        result_grid = ttk.Frame(self.simulator_canvas, style="Surface.TFrame")
        self.simulator_result_content = result_grid
        self.simulator_canvas_window = self.simulator_canvas.create_window(
            (0, 0), window=result_grid, anchor="nw"
        )
        result_grid.bind("<Configure>", self._on_simulator_content_configure)
        self.simulator_canvas.bind(
            "<Configure>", self._on_simulator_canvas_configure
        )
        self.simulator_canvas.bind(
            "<MouseWheel>",
            lambda event: self.simulator_canvas.yview_scroll(
                int(-event.delta / 120), "units"
            ),
        )
        result_grid.rowconfigure(0, weight=1)
        self.simulator_results: list[SimulatorResultView] = []
        effort_labels = ("体力", "力量", "耐力", "敏捷", "智力")
        battle_labels = ("生命", "攻击", "防御", "速度", "魔力")
        for index, title in enumerate(
            ("0转1级", "0转99级", "0转50级", "10转99级")
        ):
            result_grid.columnconfigure(
                index, weight=1, uniform="simulator_result"
            )
            card = ttk.Frame(result_grid, style="Alt.TFrame", padding=(10, 6))
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 5, 0),
            )
            title_var = tk.StringVar(value=title)
            ttk.Label(
                card, textvariable=title_var, style="AltSection.TLabel"
            ).pack(anchor="w")
            ttk.Label(
                card, text="五项努力值", style="MetricLabel.TLabel"
            ).pack(anchor="w", pady=(4, 1))
            effort_vars = tuple(tk.StringVar(value="-") for _ in effort_labels)
            for label, variable in zip(effort_labels, effort_vars):
                self._prediction_metric_variable_row(card, label, variable)
            ttk.Separator(card).pack(fill="x", pady=3)
            ttk.Label(
                card, text="战斗面板", style="MetricLabel.TLabel"
            ).pack(anchor="w", pady=(0, 1))
            battle_vars = tuple(tk.StringVar(value="-") for _ in battle_labels)
            for label, variable in zip(battle_labels, battle_vars):
                self._prediction_metric_variable_row(card, label, variable)
            self.simulator_results.append(
                SimulatorResultView(card, title_var, effort_vars, battle_vars)
            )

        for variables in self.simulator_point_vars:
            for variable in variables:
                variable.trace_add(
                    "write",
                    lambda *_args: self._invalidate_simulator_results(
                        "自由加点已修改，请重新计算"
                    ),
                )

        ttk.Label(
            parent,
            text="努力值顺序：体 / 力 / 耐 / 敏 / 智；战斗值顺序：生命 / 攻击 / 防御 / 速度 / 魔力。目标基数 = max(0, 原始基数 + 波动)。",
            style="Muted.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(8, 0))

        self._fill_simulator_strength()

    def _calculate_base_simulation(self) -> None:
        try:
            if self.simulator_basis is None:
                raise ValueError("请先选择有基数数据的宠物")
            wave = self._read_simulator_wave()
            target = apply_basis_wave(self.simulator_basis.base, wave)
            level = self._read_simulator_level()
            allocations = self._read_simulator_allocations()
            results = simulate_base(target, level, allocations)
        except (TypeError, ValueError) as exc:
            self._clear_simulator_results()
            self.simulator_status_var.set(f"无法计算：{exc}")
            return

        used = tuple(sum(points) for points in allocations)
        self.simulator_status_var.set(f"目标成长 {sum(target):g} · 加点 {used[0]}/{used[1]}/{used[2]}")
        titles = ("0转1级", "0转99级", f"0转{level}级", "10转99级")
        for view, title, result in zip(self.simulator_results, titles, results):
            view.title.set(title)
            for variable, value in zip(view.efforts, result.efforts):
                variable.set(f"{value:,}")
            for variable, value in zip(view.battle, result.battle):
                variable.set(f"{value:,}")

    def _load_simulator_species(self, pet: Mapping[str, object]) -> None:
        type_name = self.backend.pet_type_name(int(pet["type_id"]))
        server = "active" if self.backend.server >= 11 else "normal"
        try:
            basis = find_pet_basis(type_name, server)
        except (LookupError, OSError, TypeError, ValueError) as exc:
            self._clear_simulator_species(str(exc))
            return
        self.simulator_basis = basis
        server_label = "活动服" if server == "active" else "普通服"
        self.simulator_species_var.set(
            f"{basis.name} · {server_label} · 原始成长 {basis.growth:g}"
        )
        for variable, value in zip(self.simulator_original_vars, basis.base):
            variable.set(f"{value:g}")
        self._reset_simulator_wave()

    def _clear_simulator_species(self, message: str = "请先选择宠物") -> None:
        self.simulator_basis = None
        self.simulator_species_var.set(message)
        self.simulator_status_var.set(message)
        for variable in self.simulator_original_vars + self.simulator_target_vars:
            variable.set("-")
        self._clear_simulator_results()

    def _preview_simulator_target(self) -> None:
        self._clear_simulator_results()
        if self.simulator_basis is None:
            return
        try:
            target = apply_basis_wave(
                self.simulator_basis.base, self._read_simulator_wave()
            )
        except ValueError:
            for variable in self.simulator_target_vars:
                variable.set("-")
            return
        for variable, value in zip(self.simulator_target_vars, target):
            variable.set(f"{value:g}")
        self.simulator_status_var.set(f"目标成长 {sum(target):g} · 请重新计算")

    def _read_simulator_wave(self) -> tuple[float, ...]:
        raw = [variable.get().strip() for variable in self.simulator_wave_vars]
        if any(not value for value in raw):
            raise ValueError("请填写完整的五项波动")
        try:
            return tuple(float(value) for value in raw)
        except ValueError as exc:
            raise ValueError("五项波动必须是数字") from exc

    def _read_simulator_level(self) -> int:
        raw = self.simulator_level_var.get().strip()
        if not raw.isdigit():
            raise ValueError("任意等级必须是 1 到 99 的整数")
        level = int(raw)
        if level not in range(1, 100):
            raise ValueError("任意等级必须在 1 到 99 之间")
        return level

    def _read_simulator_allocations(self) -> tuple[tuple[int, ...], ...]:
        allocations = []
        for variables in self.simulator_point_vars:
            raw = [variable.get().strip() for variable in variables]
            if any(not value.isdigit() for value in raw):
                raise ValueError("自由加点必须是非负整数")
            allocations.append(tuple(int(value) for value in raw))
        return tuple(allocations)

    def _update_simulator_allocation_header(self) -> None:
        try:
            level = self._read_simulator_level()
        except ValueError:
            level = 0
        budget = max(0, level - 1)
        self.simulator_allocation_header_vars[1].set(
            f"0转{level or '?'}级\n最多{budget}"
        )
        self._invalidate_simulator_results("任意等级已修改，请重新计算")

    def _fill_simulator_strength(self) -> None:
        try:
            level = self._read_simulator_level()
        except ValueError:
            level = 50
        for variables, budget in zip(
            self.simulator_point_vars, (98, level - 1, 98)
        ):
            for index, variable in enumerate(variables):
                variable.set(str(budget if index == 1 else 0))

    def _reset_simulator_wave(self) -> None:
        for variable in self.simulator_wave_vars:
            variable.set("0")
        self._preview_simulator_target()
        if self.simulator_basis is not None:
            self._calculate_base_simulation()

    def _invalidate_simulator_results(self, message: str) -> None:
        if not hasattr(self, "simulator_results"):
            return
        self._clear_simulator_results()
        if self.simulator_basis is not None:
            self.simulator_status_var.set(message)

    def _clear_simulator_results(self) -> None:
        for view in self.simulator_results:
            for variable in view.efforts + view.battle:
                variable.set("-")

    def _on_simulator_content_configure(self, _event: tk.Event) -> None:
        self.simulator_canvas.configure(
            scrollregion=self.simulator_canvas.bbox("all")
        )
        self.root.after_idle(self._update_simulator_scrollbar)

    def _on_simulator_canvas_configure(self, event: tk.Event) -> None:
        self.simulator_canvas.itemconfigure(
            self.simulator_canvas_window, width=event.width
        )
        self.root.after_idle(self._update_simulator_scrollbar)

    def _update_simulator_scrollbar(self) -> None:
        if not self.simulator_canvas.winfo_exists():
            return
        self.simulator_canvas.update_idletasks()
        content_height = self.simulator_result_content.winfo_reqheight()
        canvas_height = self.simulator_canvas.winfo_height()
        if canvas_height > 1 and content_height > canvas_height:
            self.simulator_scrollbar.grid()
        else:
            self.simulator_scrollbar.grid_remove()
            self.simulator_canvas.yview_moveto(0)
        self.simulator_canvas.configure(
            scrollregion=self.simulator_canvas.bbox("all")
        )

    def _build_operation_tab(self, parent: ttk.Frame) -> None:
        self.pet_title_var = tk.StringVar(value="请先选择宠物")
        self.pet_meta_var = tk.StringVar(value="连接服务器后，从左侧宠物列表中选择")
        detail_header = ttk.Frame(parent, style="Surface.TFrame")
        detail_header.pack(fill="x", pady=(0, 10))
        detail_text = ttk.Frame(detail_header, style="Surface.TFrame")
        detail_text.pack(side="left", fill="x", expand=True)
        ttk.Label(
            detail_text, textvariable=self.pet_title_var, style="Section.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            detail_text, textvariable=self.pet_meta_var, style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 0))

        detail_actions = ttk.Frame(detail_header, style="Surface.TFrame")
        detail_actions.pack(side="right", padx=(12, 0))
        self.to_storage_button = ttk.Button(
            detail_actions,
            text="放回仓库",
            style="Tool.TButton",
            command=lambda: self._move_selected_pet("to_storage"),
        )
        self.to_storage_button.pack(side="left")
        self.to_bag_button = ttk.Button(
            detail_actions,
            text="取回背包",
            style="Tool.TButton",
            command=lambda: self._move_selected_pet("to_bag"),
        )
        self.to_bag_button.pack(side="left", padx=(8, 0))

        self.metrics_frame = ttk.Frame(parent, style="Surface.TFrame")
        self.metrics_frame.pack(fill="x")
        self.metric_values: dict[str, tk.StringVar] = {}
        metric_defs = (
            ("grow_value", "成长", 0, 0, 5),
            ("physique", "体力", 1, 0, 1),
            ("strength", "力量", 1, 1, 1),
            ("endurance", "耐力", 1, 2, 1),
            ("quick", "敏捷", 1, 3, 1),
            ("intelligence", "智力", 1, 4, 1),
            ("hp_max", "生命", 2, 0, 1),
            ("attack", "攻击", 2, 1, 1),
            ("defense", "防御", 2, 2, 1),
            ("speed", "速度", 2, 3, 1),
            ("mp_max", "魔力", 2, 4, 1),
        )
        for column in range(5):
            self.metrics_frame.columnconfigure(column, weight=1, uniform="pet_metric")
        for key, label, row, column, columnspan in metric_defs:
            box = ttk.Frame(self.metrics_frame, style="Alt.TFrame", padding=(10, 7))
            box.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky="nsew",
                padx=(0 if column == 0 else 4, 0),
                pady=(0 if row == 0 else 4, 0),
            )
            value_var = tk.StringVar(value="-")
            self.metric_values[key] = value_var
            ttk.Label(box, text=label, style="MetricLabel.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=value_var, style="MetricValue.TLabel").pack(
                anchor="w"
            )

        ttk.Separator(parent).pack(fill="x", pady=14)

        config = ttk.Frame(parent, style="Surface.TFrame")
        config.pack(fill="x")
        config.columnconfigure(0, weight=1)
        config.columnconfigure(1, weight=1)
        config.columnconfigure(2, weight=1)

        item_box = ttk.Frame(config, style="Surface.TFrame")
        item_box.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(item_box, text="洗点道具", style="Body.TLabel").pack(anchor="w")
        self.item_combo = ttk.Combobox(item_box, state="readonly")
        self.item_combo.pack(fill="x", pady=(5, 0))
        self.item_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_targets())

        mode_box = ttk.Frame(config, style="Surface.TFrame")
        mode_box.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Label(mode_box, text="洗点方式", style="Body.TLabel").pack(anchor="w")
        self.mode_var = tk.StringVar(value="base")
        mode_buttons = ttk.Frame(mode_box, style="Surface.TFrame")
        mode_buttons.pack(fill="x", pady=(4, 0))
        self.base_mode = ttk.Radiobutton(
            mode_buttons,
            text="基础五项",
            value="base",
            variable=self.mode_var,
            command=self._render_targets,
        )
        self.derived_mode = ttk.Radiobutton(
            mode_buttons,
            text="战斗面板",
            value="derived",
            variable=self.mode_var,
            command=self._render_targets,
        )
        self.base_mode.pack(side="left")
        self.derived_mode.pack(side="left", padx=(12, 0))

        count_box = ttk.Frame(config, style="Surface.TFrame")
        count_box.grid(row=0, column=2, sticky="ew")
        ttk.Label(count_box, text="最多洗点次数", style="Body.TLabel").pack(anchor="w")
        self.max_attempts_var = tk.StringVar(value="100")
        self.max_attempts_entry = ttk.Entry(count_box, textvariable=self.max_attempts_var)
        self.max_attempts_entry.pack(fill="x", pady=(5, 0))

        target_header = ttk.Frame(parent, style="Surface.TFrame")
        target_header.pack(fill="x", pady=(16, 7))
        ttk.Label(target_header, text="期望数值", style="Section.TLabel").pack(side="left")
        self.target_hint_var = tk.StringVar()
        ttk.Label(
            target_header, textvariable=self.target_hint_var, style="Muted.TLabel"
        ).pack(side="right")
        self.targets_frame = ttk.Frame(parent, style="Surface.TFrame")
        self.targets_frame.pack(fill="x")

        action_row = ttk.Frame(parent, style="Surface.TFrame")
        action_row.pack(fill="x", pady=(16, 0))
        self.start_button = ttk.Button(
            action_row, text="开始洗点", style="Accent.TButton", command=self._start_wash
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            action_row, text="停止", style="Danger.TButton", command=self._stop_wash
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(
            action_row, variable=self.progress_var, mode="determinate", maximum=100
        )
        self.progress.pack(side="left", fill="x", expand=True, padx=(16, 10))
        self.progress_text_var = tk.StringVar(value="尚未开始")
        ttk.Label(
            action_row, textvariable=self.progress_text_var, style="Muted.TLabel"
        ).pack(side="right")

    def _build_result_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="本次洗点结果", style="Section.TLabel").pack(anchor="w")
        self.result_summary_var = tk.StringVar(value="开始洗点后，这里会展示结果与每次属性变化")
        ttk.Label(
            parent,
            textvariable=self.result_summary_var,
            style="Muted.TLabel",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        columns = ("growth", "base", "derived", "remaining", "state")
        self.result_tree = ttk.Treeview(parent, columns=columns, show="tree headings")
        self.result_tree.heading("#0", text="次数")
        self.result_tree.heading("growth", text="成长")
        self.result_tree.heading("base", text="基础五项")
        self.result_tree.heading("derived", text="战斗面板")
        self.result_tree.heading("remaining", text="道具剩余")
        self.result_tree.heading("state", text="判断")
        self.result_tree.column("#0", width=58, minwidth=50, anchor="center")
        self.result_tree.column("growth", width=68, minwidth=60, anchor="center")
        self.result_tree.column("base", width=230, minwidth=180, anchor="w")
        self.result_tree.column("derived", width=260, minwidth=200, anchor="w")
        self.result_tree.column("remaining", width=80, minwidth=70, anchor="center")
        self.result_tree.column("state", width=90, minwidth=80, anchor="center")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        self.result_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.result_tree.tag_configure("reached", foreground=COLORS["success"])

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def _connect(self) -> None:
        if self.busy_action or self.connected:
            return
        try:
            uid = int(self.uid_var.get().strip())
            server_index = self.server_combo.current()
            server = 0 if server_index <= 0 else server_index
            password = self.password_var.get()
            if uid <= 0:
                raise ValueError
            if not password:
                raise RuntimeError("请输入密码")
        except ValueError:
            messagebox.showerror("输入有误", "米米号必须是正整数", parent=self.root)
            return
        except RuntimeError as exc:
            messagebox.showerror("输入有误", str(exc), parent=self.root)
            return

        self._set_busy("connect", "正在连接服务器")

        def work() -> None:
            try:
                actual_server = self.backend.connect(uid, password, server)
                bag_pets = self.backend.load_pets()
                storage_pets = self.backend.load_storage_pets()
                quantities = self.backend.load_item_quantities()
                self.events.put(
                    ("connected", (actual_server, bag_pets, storage_pets, quantities))
                )
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
        self._render_pet_list()
        self._render_storage_list()
        self._show_pet(None)
        self.connection_badge.configure(
            text="未连接", bg="#e7edef", fg=COLORS["muted"]
        )
        self.status_var.set("已断开服务器")
        self._update_item_options()
        self._sync_controls()

    def _refresh(self) -> None:
        if not self.connected or self.busy_action:
            return
        self._set_busy("refresh", "正在刷新宠物和道具")

        def work() -> None:
            try:
                bag_pets = self.backend.load_pets()
                storage_pets = self.backend.load_storage_pets()
                quantities = self.backend.load_item_quantities()
                self.events.put(("refreshed", (bag_pets, storage_pets, quantities)))
            except Exception as exc:
                self.events.put(("error", ("refresh", exc)))

        self._run_worker(work)

    def _move_selected_pet(self, direction: str) -> None:
        if not self.connected or self.busy_action:
            return
        if direction == "to_storage":
            selection = self.pet_tree.selection()
            pet = self.pet_by_row.get(selection[0]) if selection else None
            action_text = "放回仓库"
            mover = self.backend.move_pet_to_store
        elif direction == "to_bag":
            selection = self.storage_tree.selection()
            pet = self.storage_pet_by_row.get(selection[0]) if selection else None
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
                bag_pets = self.backend.load_pets()
                storage_pets = self.backend.load_storage_pets()
                quantities = self.backend.load_item_quantities()
                self.events.put(
                    ("pet_moved", (action_text, bag_pets, storage_pets, quantities))
                )
            except Exception as exc:
                self.events.put(("error", ("move_pet", exc)))

        self._run_worker(work)

    def _select_pet(self, _event: tk.Event[Any] | None = None) -> None:
        selection = self.pet_tree.selection()
        pet = self.pet_by_row.get(selection[0]) if selection else None
        if pet is None:
            return
        storage_selection = self.storage_tree.selection()
        if pet is not None and storage_selection:
            self.storage_tree.selection_remove(*storage_selection)
        self._show_pet(pet)
        self._sync_controls()

    def _select_storage_pet(self, _event: tk.Event[Any] | None = None) -> None:
        if self.busy_action is not None:
            return
        selection = self.storage_tree.selection()
        pet = self.storage_pet_by_row.get(selection[0]) if selection else None
        if pet is None:
            return
        bag_selection = self.pet_tree.selection()
        if bag_selection:
            self.pet_tree.selection_remove(*bag_selection)
        self.selected_pet = None
        type_name = self.backend.pet_type_name(int(pet["type_id"]))
        self.pet_title_var.set(str(pet["nick"]))
        self.pet_meta_var.set(
            f"{type_name}  |  等级 {pet['level']}  |  仓库宠物  |  "
            f"ID {pet['pet_id']}  |  正在读取详细属性"
        )
        for value in self.metric_values.values():
            value.set("-")
        self._render_prediction(None)
        self._clear_simulator_species("正在读取仓库宠物种类与详细属性")
        self.status_var.set("正在读取仓库宠物详细属性")
        self.storage_request_id += 1
        request_id = self.storage_request_id
        pet_id = int(pet["pet_id"])
        self._set_busy("storage_info", "正在读取仓库宠物详细属性")

        def work() -> None:
            try:
                detail = self.backend.load_pet_info(pet_id)
                self.events.put(("storage_info", (request_id, detail)))
            except Exception as exc:
                self.events.put(("error", ("storage_info", exc)))

        self._run_worker(work)

    def _show_pet(self, pet: Mapping[str, object] | None) -> None:
        self.selected_pet = dict(pet) if pet is not None else None
        if pet is None:
            self.pet_title_var.set("请先选择宠物")
            self.pet_meta_var.set("连接服务器后，从左侧宠物列表中选择")
            for value in self.metric_values.values():
                value.set("-")
            self._render_prediction(None)
            self._clear_simulator_species()
            return
        type_name = self.backend.pet_type_name(int(pet["type_id"]))
        self.pet_title_var.set(str(pet["nick"]))
        self.pet_meta_var.set(
            f"{type_name}  |  等级 {pet['level']}  |  {pet['reincarnation_degree']} 转  |  "
            f"{pet['status']}  |  ID {pet['pet_id']}"
        )
        for key, value in self.metric_values.items():
            value.set(str(pet[key]))
        self._render_prediction(pet)
        self._load_simulator_species(pet)

    def _show_storage_detail(self, pet: Mapping[str, object]) -> None:
        type_name = self.backend.pet_type_name(int(pet["type_id"]))
        self.pet_title_var.set(str(pet["nick"]))
        self.pet_meta_var.set(
            f"{type_name}  |  等级 {pet['level']}  |  {pet['reincarnation_degree']} 转  |  "
            f"仓库宠物  |  ID {pet['pet_id']}  |  需移入背包后洗点"
        )
        for key, value in self.metric_values.items():
            value.set(str(pet[key]))
        self._render_prediction(pet)
        self._load_simulator_species(pet)

    def _prediction_cache_key(
        self, pet: Mapping[str, object]
    ) -> tuple[str, int, int]:
        return (
            self.uid_var.get().strip(),
            int(self.backend.server),
            int(pet["pet_id"]),
        )

    def _render_prediction(self, pet: Mapping[str, object] | None) -> None:
        if pet is None:
            self.prediction_pet = None
            for variable, entry in zip(
                self.prediction_point_vars, self.prediction_point_entries
            ):
                variable.set("")
                entry.configure(state="disabled")
            self.prediction_calculate_button.configure(state="disabled")
            self.prediction_growth_var.set("")
            self.prediction_growth_entry.configure(state="disabled")
            self.prediction_intro_var.set(
                "选择宠物后，自动按默认成长和全力加点计算预测"
            )
            self.prediction_base_var.set("十项完全一致的 0 转基数：-")
            self.prediction_fit_var.set("")
            self._clear_prediction_candidates()
            return
        self.prediction_pet = dict(pet)
        cached = self.prediction_cache.get(self._prediction_cache_key(pet))
        if cached is not None:
            for variable, entry, value in zip(
                self.prediction_point_vars,
                self.prediction_point_entries,
                cached.point_values,
            ):
                variable.set(value)
                entry.configure(state="normal")
            self.prediction_growth_var.set(cached.growth_value)
            self.prediction_growth_entry.configure(state="normal")
            self.prediction_calculate_button.configure(state="normal")
            self._display_prediction_cache(pet, cached)
            return
        default_points = (0, int(pet["attr_point_applied"]), 0, 0, 0)
        for variable, entry, value in zip(
            self.prediction_point_vars,
            self.prediction_point_entries,
            default_points,
        ):
            variable.set(str(value))
            entry.configure(state="normal")
        self.prediction_calculate_button.configure(state="normal")
        if int(pet["reincarnation_degree"]) == 0:
            self.prediction_growth_var.set(str(pet["grow_value"]))
            default_growth_text = f"宠物当前成长 {pet['grow_value']}"
        else:
            self.prediction_growth_var.set("")
            default_growth_text = "本地基数库成长"
        self.prediction_growth_entry.configure(state="normal")
        self.prediction_intro_var.set(
            f"{pet['nick']} · 当前 {pet['reincarnation_degree']}转{pet['level']}级"
        )
        self.prediction_fit_var.set(
            f"当前已分配点数：{pet['attr_point_applied']}；成长默认使用{default_growth_text}"
        )
        self._clear_prediction_candidates()
        if self.notebook.select() == str(self.prediction_tab):
            self.prediction_base_var.set("正在按默认全力加点自动计算")
            self._calculate_prediction()
        else:
            self.prediction_base_var.set("进入成长预测页后自动计算")

    def _on_workspace_tab_changed(self, _event: tk.Event | None = None) -> None:
        if self.notebook.select() != str(self.prediction_tab):
            return
        pet = self.prediction_pet
        if pet is None:
            return
        cached = self.prediction_cache.get(self._prediction_cache_key(pet))
        if cached is None:
            self._calculate_prediction()
        else:
            for variable, value in zip(
                self.prediction_point_vars, cached.point_values
            ):
                variable.set(value)
            self.prediction_growth_var.set(cached.growth_value)
            self._display_prediction_cache(pet, cached)

    def _calculate_prediction(self, _event: tk.Event | None = None) -> None:
        pet = self.prediction_pet
        if pet is None:
            return
        cache_key = self._prediction_cache_key(pet)
        try:
            point_texts = [
                variable.get().strip() for variable in self.prediction_point_vars
            ]
            if all(not value for value in point_texts):
                points = (0, int(pet["attr_point_applied"]), 0, 0, 0)
                for variable, value in zip(self.prediction_point_vars, points):
                    variable.set(str(value))
            elif any(not value for value in point_texts):
                raise ValueError("请填写完整的五项自由加点")
            else:
                try:
                    points = tuple(int(value) for value in point_texts)
                except ValueError as exc:
                    raise ValueError("五项自由加点必须是整数") from exc
            type_name = self.backend.pet_type_name(int(pet["type_id"]))
            server = "active" if self.backend.server >= 11 else "normal"
            basis = find_pet_basis(type_name, server)
            if int(pet["reincarnation_degree"]) == 0:
                default_growth = int(pet["grow_value"])
                default_growth_source = "宠物当前成长"
            else:
                default_growth = round_display(basis.growth)
                default_growth_source = "基数库默认成长"
            growth_text = self.prediction_growth_var.get().strip()
            if growth_text:
                try:
                    growth = int(growth_text)
                except ValueError as exc:
                    raise ValueError("成长必须是整数") from exc
                growth_source = (
                    default_growth_source
                    if growth == default_growth
                    else "手动成长"
                )
            else:
                growth = default_growth
                self.prediction_growth_var.set(str(growth))
                growth_source = default_growth_source
            prediction = predict_pet(pet, basis, points, growth)
        except (KeyError, LookupError, OSError, TypeError, ValueError) as exc:
            cached = PredictionCacheEntry(
                point_values=tuple(
                    variable.get().strip() for variable in self.prediction_point_vars
                ),
                growth_value=self.prediction_growth_var.get().strip(),
                growth=None,
                growth_source="",
                prediction=None,
                error=str(exc),
            )
            self.prediction_cache[cache_key] = cached
            self._display_prediction_cache(pet, cached)
            return

        cached = PredictionCacheEntry(
            point_values=tuple(
                variable.get().strip() for variable in self.prediction_point_vars
            ),
            growth_value=self.prediction_growth_var.get().strip(),
            growth=growth,
            growth_source=growth_source,
            prediction=prediction,
            error=None,
        )
        self.prediction_cache[cache_key] = cached
        self._display_prediction_cache(pet, cached)

    def _display_prediction_cache(
        self,
        pet: Mapping[str, object],
        cached: PredictionCacheEntry,
    ) -> None:
        if cached.error is not None:
            self.prediction_intro_var.set(f"暂时无法预测：{cached.error}")
            self.prediction_base_var.set("十项完全一致的 0 转基数：-")
            self.prediction_fit_var.set(
                f"当前已分配点数：{pet['attr_point_applied']}；点击重新计算可更新缓存"
            )
            self._clear_prediction_candidates()
            return

        prediction = cached.prediction
        growth = cached.growth
        if prediction is None or growth is None:
            raise RuntimeError("成长预测缓存状态不完整")
        self.prediction_intro_var.set(
            f"{pet['nick']} · 当前 {pet['reincarnation_degree']}转{pet['level']}级 · "
            f"成长 {growth}（{cached.growth_source}）"
        )
        self.prediction_base_var.set(
            f"十项完全一致候选：{prediction.candidate_count} 套（已按网页权重排序）"
        )
        self.prediction_fit_var.set(
            f"{prediction.note}；结果已缓存，点击重新计算可更新"
        )
        self._clear_prediction_candidates()
        scenario_names = ("0转1级", "0转99级", "10转99级")
        effort_labels = ("体力", "力量", "耐力", "敏捷", "智力")
        battle_labels = ("生命", "攻击", "防御", "速度", "魔力")
        for candidate_index, candidate in enumerate(prediction.candidates, start=1):
            base_text = " / ".join(f"{value:g}" for value in candidate.base)
            points_text = " / ".join(str(value) for value in candidate.source_points)
            section = ttk.Frame(self.prediction_content, style="Surface.TFrame")
            section.pack(fill="x", pady=(0, 8))
            header = ttk.Frame(section, style="Surface.TFrame")
            header.pack(fill="x", pady=(0, 4))
            ttk.Label(
                header,
                text=f"候选 {candidate_index}",
                style="Section.TLabel",
            ).pack(side="left")
            ttk.Label(
                header,
                text=f"权重 {candidate.paths}",
                style="Muted.TLabel",
            ).pack(side="right")
            ttk.Label(
                section,
                text=(
                    f"0转基数：{base_text}    "
                    f"当前自由加点（体/力/耐/敏/智）：{points_text}"
                ),
                style="Muted.TLabel",
            ).pack(anchor="w", pady=(0, 4))
            cards = ttk.Frame(section, style="Surface.TFrame")
            cards.pack(fill="x")
            for column, (name, projected) in enumerate(
                zip(scenario_names, candidate.scenarios)
            ):
                cards.columnconfigure(column, weight=1, uniform="prediction_card")
                card = ttk.Frame(cards, style="Alt.TFrame", padding=(10, 6))
                card.grid(
                    row=0,
                    column=column,
                    sticky="nsew",
                    padx=(0 if column == 0 else 5, 0),
                )
                ttk.Label(card, text=name, style="AltSection.TLabel").pack(anchor="w")
                ttk.Label(card, text="五项努力值", style="MetricLabel.TLabel").pack(
                    anchor="w", pady=(4, 1)
                )
                for label, value in zip(effort_labels, projected.efforts):
                    self._prediction_metric_row(card, label, value)
                ttk.Separator(card).pack(fill="x", pady=3)
                ttk.Label(card, text="战斗面板", style="MetricLabel.TLabel").pack(
                    anchor="w", pady=(0, 1)
                )
                for label, value in zip(battle_labels, projected.battle):
                    self._prediction_metric_row(card, label, value)
            ttk.Separator(section).pack(fill="x", pady=(6, 0))
        self.prediction_canvas.update_idletasks()
        self.prediction_canvas.configure(scrollregion=self.prediction_canvas.bbox("all"))
        self._update_prediction_scrollbar()

    def _clear_prediction_candidates(self) -> None:
        for child in self.prediction_content.winfo_children():
            child.destroy()
        self.prediction_scrollbar.grid_remove()
        self.prediction_canvas.yview_moveto(0)

    def _on_prediction_content_configure(self, _event: tk.Event) -> None:
        self.prediction_canvas.configure(scrollregion=self.prediction_canvas.bbox("all"))
        self.root.after_idle(self._update_prediction_scrollbar)

    def _on_prediction_canvas_configure(self, event: tk.Event) -> None:
        self.prediction_canvas.itemconfigure(
            self.prediction_canvas_window, width=event.width
        )
        self.root.after_idle(self._update_prediction_scrollbar)

    def _update_prediction_scrollbar(self) -> None:
        if not self.prediction_canvas.winfo_exists():
            return
        self.prediction_canvas.update_idletasks()
        content_height = self.prediction_content.winfo_reqheight()
        canvas_height = self.prediction_canvas.winfo_height()
        if canvas_height > 1 and content_height > canvas_height:
            self.prediction_scrollbar.grid()
        else:
            self.prediction_scrollbar.grid_remove()
            self.prediction_canvas.yview_moveto(0)
        self.prediction_canvas.configure(scrollregion=self.prediction_canvas.bbox("all"))

    @staticmethod
    def _prediction_metric_row(parent: ttk.Frame, label: str, value: int) -> None:
        row = ttk.Frame(parent, style="Alt.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text=label, style="MetricLabel.TLabel").pack(side="left")
        ttk.Label(
            row, text=f"{value:,}", style="PredictionValue.TLabel"
        ).pack(side="right")

    @staticmethod
    def _prediction_metric_variable_row(
        parent: ttk.Frame,
        label: str,
        value: tk.StringVar,
    ) -> None:
        row = ttk.Frame(parent, style="Alt.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text=label, style="MetricLabel.TLabel").pack(side="left")
        ttk.Label(
            row, textvariable=value, style="PredictionValue.TLabel"
        ).pack(side="right")

    def _render_pet_list(self, preferred_pet_id: int | None = None) -> None:
        current_id = preferred_pet_id
        if current_id is None and self.selected_pet is not None:
            current_id = int(self.selected_pet["pet_id"])
        self.pet_tree.delete(*self.pet_tree.get_children())
        self.pet_by_row.clear()
        selected_row = None
        for pet in self.bag_pets:
            type_name = self.backend.pet_type_name(int(pet["type_id"]))
            row = self.pet_tree.insert(
                "",
                "end",
                text=str(pet["nick"]),
                values=(type_name, pet["level"], pet["reincarnation_degree"]),
            )
            self.pet_by_row[row] = pet
            if current_id == int(pet["pet_id"]):
                selected_row = row
        if self.bag_pets:
            self.pet_list_note.place_forget()
            selected_row = selected_row or self.pet_tree.get_children()[0]
            self.pet_tree.selection_set(selected_row)
            self.pet_tree.focus(selected_row)
            self.pet_tree.see(selected_row)
            self._select_pet()
        else:
            self.pet_list_note.configure(
                text="宠物背包为空" if self.connected else "连接后显示宠物背包"
            )
            self.pet_list_note.place(relx=0.5, rely=0.48, anchor="center")
            self._show_pet(None)
        self._sync_controls()

    def _render_storage_list(self) -> None:
        self.storage_tree.delete(*self.storage_tree.get_children())
        self.storage_pet_by_row.clear()
        for pet in self.storage_pets:
            type_name = self.backend.pet_type_name(int(pet["type_id"]))
            row = self.storage_tree.insert(
                "",
                "end",
                text=str(pet["nick"]),
                values=(type_name, pet["level"]),
            )
            self.storage_pet_by_row[row] = pet
        if self.storage_pets:
            self.storage_list_note.place_forget()
        else:
            self.storage_list_note.configure(
                text="宠物仓库为空" if self.connected else "连接后显示宠物仓库"
            )
            self.storage_list_note.place(relx=0.5, rely=0.5, anchor="center")
        self._sync_controls()

    def _item_index(self) -> int:
        index = self.item_combo.current()
        return index if index >= 0 else 0

    def _update_item_options(self) -> None:
        selected = self._item_index()
        item_ids = self.backend.item_ids
        values = [
            f"{name}  x {self.item_quantities.get(item_id, 0)}"
            for name, item_id in zip(ITEM_TYPES, item_ids)
        ]
        self.item_combo.configure(values=values)
        if values:
            self.item_combo.current(min(selected, len(values) - 1))
        self._render_targets()

    def _render_targets(self) -> None:
        previous_rule = self.rendered_target_rule
        previous_values = [variable.get() for variable in self.target_entries]
        for child in self.targets_frame.winfo_children():
            child.destroy()
        self.target_entries.clear()
        item_index = self._item_index()
        growth_only = item_index in (0, 1)
        mode_state = "disabled" if growth_only else "normal"
        self.base_mode.configure(state=mode_state)
        self.derived_mode.configure(state=mode_state)
        if growth_only:
            self.mode_var.set("base")

        fields = target_fields(item_index, self.mode_var.get())
        current_rule = (item_index, self.mode_var.get())
        keep_values = previous_rule == current_rule and len(previous_values) == len(fields)
        for index, field in enumerate(fields):
            box = ttk.Frame(self.targets_frame, style="Surface.TFrame")
            box.grid(
                row=index // 3,
                column=index % 3,
                sticky="ew",
                padx=(0 if index % 3 == 0 else 10, 0),
                pady=(0 if index < 3 else 10, 0),
            )
            self.targets_frame.columnconfigure(index % 3, weight=1)
            ttk.Label(
                box,
                text=f"{field.label} ({field.comparison_label})",
                style="Body.TLabel",
            ).pack(anchor="w")
            variable = tk.StringVar(
                value=previous_values[index] if keep_values else "0"
            )
            self.target_entries.append(variable)
            ttk.Entry(box, textvariable=variable).pack(fill="x", pady=(5, 0))
        self.rendered_target_rule = current_rule
        self.target_hint_var.set("0 表示不判断")

    def _start_wash(self) -> None:
        if not self.connected or self.busy_action or self.selected_pet is None:
            return
        try:
            targets = tuple(int(variable.get().strip()) for variable in self.target_entries)
            config = WashConfig(
                pet_id=int(self.selected_pet["pet_id"]),
                item_index=self._item_index(),
                mode=self.mode_var.get(),
                targets=targets,
                max_attempts=int(self.max_attempts_var.get().strip()),
            )
            config.validate()
        except ValueError as exc:
            messagebox.showerror("设置有误", str(exc), parent=self.root)
            return

        for row in self.result_tree.get_children():
            self.result_tree.delete(row)
        self.result_summary_var.set("正在洗点，结果会在每次请求后更新")
        self.progress.configure(maximum=config.max_attempts)
        self.progress_var.set(0)
        self.progress_text_var.set(f"0 / {config.max_attempts}")
        self.stop_event.clear()
        self._set_busy("wash", "正在洗点")
        runner = RespecRunner(self.backend, self.backend.item_ids)

        def on_progress(progress: WashProgress) -> None:
            self.events.put(("progress", (config, progress)))

        def work() -> None:
            try:
                result = runner.run(config, self.stop_event.is_set, on_progress)
                self.events.put(("finished", (config, result)))
            except Exception as exc:
                self.events.put(("error", ("wash", exc)))

        self._run_worker(work)

    def _stop_wash(self) -> None:
        if self.busy_action != "wash":
            return
        self.stop_event.set()
        self.status_var.set("正在停止，等待当前网络请求完成")

    def _run_worker(self, target: Any) -> None:
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _set_busy(self, action: str | None, status: str | None = None) -> None:
        self.busy_action = action
        if status:
            self.status_var.set(status)
        self._sync_controls()

    def _sync_controls(self) -> None:
        idle = self.busy_action is None
        self.connect_button.configure(state="normal" if idle and not self.connected else "disabled")
        self.disconnect_button.configure(state="normal" if idle and self.connected else "disabled")
        self.refresh_button.configure(state="normal" if idle and self.connected else "disabled")
        self.to_storage_button.configure(
            state="normal"
            if idle and self.connected and bool(self.pet_tree.selection())
            else "disabled"
        )
        self.to_bag_button.configure(
            state="normal"
            if idle and self.connected and bool(self.storage_tree.selection())
            else "disabled"
        )
        self.start_button.configure(
            state="normal"
            if idle and self.connected and self.selected_pet is not None
            else "disabled"
        )
        self.stop_button.configure(state="normal" if self.busy_action == "wash" else "disabled")
        login_state = "normal" if idle and not self.connected else "disabled"
        self.uid_entry.configure(state=login_state)
        self.password_entry.configure(state=login_state)
        self.server_combo.configure(state="readonly" if login_state == "normal" else "disabled")
        self.show_password.configure(state=login_state)

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: str, payload: Any) -> None:
        if event == "connected":
            server, bag_pets, storage_pets, quantities = payload
            self.connected = True
            self.bag_pets = bag_pets
            self.storage_pets = storage_pets
            self.item_quantities = quantities
            self._set_busy(None)
            self.connection_badge.configure(
                text=f"已连接 {server} 服", bg="#dcefe9", fg=COLORS["success"]
            )
            self.status_var.set(
                f"连接成功，背包 {len(bag_pets)} 只，仓库 {len(storage_pets)} 只"
            )
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            return
        if event == "refreshed":
            bag_pets, storage_pets, quantities = payload
            self.bag_pets = bag_pets
            self.storage_pets = storage_pets
            self.item_quantities = quantities
            self._set_busy(
                None,
                f"刷新完成，背包 {len(bag_pets)} 只，仓库 {len(storage_pets)} 只",
            )
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            return
        if event == "storage_info":
            request_id, pet = payload
            if request_id != self.storage_request_id:
                return
            for index, listed_pet in enumerate(self.storage_pets):
                if int(listed_pet["pet_id"]) == int(pet["pet_id"]):
                    self.storage_pets[index] = pet
                    break
            self._set_busy(None, "仓库宠物详细属性读取完成")
            self._show_storage_detail(pet)
            return
        if event == "pet_moved":
            action_text, bag_pets, storage_pets, quantities = payload
            self.bag_pets = bag_pets
            self.storage_pets = storage_pets
            self.item_quantities = quantities
            self.selected_pet = None
            self._set_busy(None, f"{action_text}完成")
            self._render_pet_list()
            self._render_storage_list()
            self._update_item_options()
            return
        if event == "progress":
            config, progress = payload
            self._handle_progress(config, progress)
            return
        if event == "finished":
            config, result = payload
            self.bag_pets = [
                dict(result.final_pet) if int(pet["pet_id"]) == config.pet_id else pet
                for pet in self.bag_pets
            ]
            item_id = self.backend.item_ids[config.item_index]
            self.item_quantities[item_id] = result.remaining_items
            self._set_busy(None)
            self._render_pet_list(preferred_pet_id=config.pet_id)
            self._update_item_options()
            self._restore_wash_rule(config)
            self._handle_result(config, result)
            return
        if event == "error":
            action, exc = payload
            self._set_busy(None)
            if action == "connect":
                self.connected = False
                self.connection_badge.configure(
                    text="连接失败", bg="#f4e3e3", fg=COLORS["danger"]
                )
            self.status_var.set(f"操作失败：{exc}")
            messagebox.showerror("操作失败", str(exc), parent=self.root)

    def _handle_progress(self, config: WashConfig, progress: WashProgress) -> None:
        pet = progress.pet
        self.progress_var.set(progress.attempt)
        self.progress_text_var.set(f"{progress.attempt} / {config.max_attempts}")
        state = "已达标" if progress.reached else "未达标"
        base_text = " / ".join(str(pet[key]) for key, _label in BASE_FIELDS)
        derived_text = " / ".join(str(pet[key]) for key, _label in DERIVED_FIELDS)
        row = self.result_tree.insert(
            "",
            "end",
            text=str(progress.attempt),
            values=(
                pet["grow_value"],
                base_text,
                derived_text,
                progress.remaining_items,
                state,
            ),
            tags=("reached",) if progress.reached else (),
        )
        self.result_tree.see(row)
        self.status_var.set(
            f"第 {progress.attempt} 次完成，{ITEM_TYPES[config.item_index]}剩余 "
            f"{progress.remaining_items} 个"
        )

    def _handle_result(self, config: WashConfig, result: WashResult) -> None:
        reason_text = RESULT_REASON.get(result.reason, result.reason)
        before = result.initial_pet
        after = result.final_pet
        self.progress_var.set(result.attempts)
        self.progress_text_var.set(f"{result.attempts} / {config.max_attempts}")
        changes = []
        for field in target_fields(config.item_index, config.mode):
            old_value = int(before[field.key])
            new_value = int(after[field.key])
            changes.append(f"{field.label} {old_value} -> {new_value}")
        self.result_summary_var.set(
            f"{reason_text}。共洗点 {result.attempts} 次，道具剩余 {result.remaining_items} 个。"
            + "  ".join(changes)
        )
        self.status_var.set(reason_text)

    def _restore_wash_rule(self, config: WashConfig) -> None:
        self.item_combo.current(config.item_index)
        self.mode_var.set(config.mode)
        self._render_targets()
        for variable, value in zip(self.target_entries, config.targets):
            variable.set(str(value))
        self.max_attempts_var.set(str(config.max_attempts))
        self.notebook.select(self.result_tab)

    def _on_close(self) -> None:
        self.stop_event.set()
        self.backend.close()
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    PetRespecApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
