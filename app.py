"""Desktop UI for capturing and stitching overlapping screenshots."""

from __future__ import annotations

import atexit
import base64
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import cv2
    import numpy as np
except ModuleNotFoundError as exc:
    raise SystemExit(
        "缺少图片依赖。请在 VS Code 中选择项目的 .venv，"
        "或执行：python -m pip install -r requirements.txt"
    ) from exc

from global_hotkey import GlobalHotkey, default_hotkey, hotkey_choices
from screen_capture import (
    ScreenRect,
    active_displays,
    capture_filename,
    capture_region_to_file,
    configure_persistent_overlay,
    configure_process_dpi_awareness,
    frame_pieces,
    place_windows_window,
    show_native_overlay,
)
from stitcher import MosaicResult, StitchResult, read_image, stitch_images, stitch_mosaic, write_image
from update_checker import ReleaseInfo, check_latest_release, is_newer_version


configure_process_dpi_awareness()

APP_VERSION = "1.1.0"
DEVELOPER_NAME = "KwoYeung"


class StitchApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("截图自动拼接")
        self.geometry("1180x800")
        self.minsize(900, 640)

        self.paths: list[str] = []
        self.session_cache_dir = Path(tempfile.mkdtemp(prefix="screenshot_stitch_session_"))
        self.captured_paths: set[str] = set()
        self.capture_serial = 0
        atexit.register(self._cleanup_session_files)

        self.result: StitchResult | MosaicResult | None = None
        self.result_preview_photo: tk.PhotoImage | None = None
        self.result_preview_scale = 1.0
        self.result_preview_min_scale = 0.08
        self.result_preview_max_scale = 4.0
        self.result_zoom_text = tk.StringVar(value="滚轮缩放")
        self.input_preview_photo: tk.PhotoImage | None = None
        self.thumbnail_photos: list[tk.PhotoImage] = []
        self.thumbnail_meta: dict[str, tuple[int, int]] = {}

        self.capture_region: tuple[int, int, int, int] | None = None
        self.selectors: list[tk.Toplevel] = []
        self.capture_frame_windows: list[tk.Toplevel] = []
        self.capture_frame_native: list[int | None] = []
        self.capture_controller: tk.Toplevel | None = None
        self.capture_controller_native: int | None = None
        self.capture_controller_button: ttk.Button | None = None
        self.capture_controller_menu_button: ttk.Button | None = None
        self.capture_controller_canvas: tk.Canvas | None = None
        self.capture_controller_text_item: int | None = None
        self.controller_reset_job: str | None = None
        self.capture_in_progress = False

        self.selection_start: tuple[int, int] | None = None
        self.selection_local_start: tuple[int, int] | None = None
        self.selection_canvas: tk.Canvas | None = None
        self.selection_rectangle: int | None = None

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.auto_sort = tk.BooleanVar(value=True)
        self.stitch_mode = tk.StringVar(value="自由平移画布")
        self.mosaic_strategy = tk.StringVar(value="自动容错")
        self.mosaic_strategy_hint = tk.StringVar()
        self.version_status = tk.StringVar(value=f"v{APP_VERSION}")
        self.update_release_url: str | None = None
        self.region_status = tk.StringVar(value="固定区域：尚未设置")
        self.status = tk.StringVar(value="设置固定区域或添加已有图片")
        self.preview_info = tk.StringVar(value="选择左侧缩略图可查看单张大图")
        self.hotkey_choice = tk.StringVar(value=default_hotkey())
        self.hotkey_status = tk.StringVar(value="")
        self.controller_text = tk.StringVar(value="📷 截图")
        self.mode_hint = tk.StringVar(value="")

        self._build_ui()
        self._mode_changed()
        self.global_hotkey = GlobalHotkey(self, lambda: self.events.put(("hotkey", None)))
        self._register_hotkey()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_macos_reopen()
        self.after(80, self._poll_events)
        self.after(1200, self._start_update_check)

    def _configure_macos_reopen(self) -> None:
        """Restore the main Tk window when the macOS Dock icon is clicked."""
        if sys.platform != "darwin":
            return
        try:
            self.createcommand("tk::mac::ReopenApplication", self._reopen_from_dock)
        except tk.TclError:
            pass

    def _reopen_from_dock(self) -> None:
        if self.capture_in_progress:
            self.after(250, self._reopen_from_dock)
            return
        if self.selectors:
            self._selection_cancel()
        self._restore_main_window()

    # ---------- Main pages ----------

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Accent.TButton", font=("TkDefaultFont", 11, "bold"), padding=(14, 8))
        style.configure("Image.Treeview", rowheight=84)
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)
        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="截图自动拼接", font=("TkDefaultFont", 19, "bold")).pack(side="left")
        ttk.Button(header, text="关于", command=self._show_about).pack(side="right")

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.capture_page = ttk.Frame(self.notebook, padding=10)
        self.result_page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.capture_page, text="① 截图管理")
        self.notebook.add(self.result_page, text="② 拼接结果")
        self._build_capture_page()
        self._build_result_page()
        footer = ttk.Frame(root)
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.version_label = tk.Label(
            footer,
            textvariable=self.version_status,
            foreground="#6e6e73",
            cursor="arrow",
            borderwidth=0,
            padx=0,
            pady=0,
        )
        self.version_label.pack(side="left")
        self.version_label.bind("<Button-1>", self._open_update_release)
        ttk.Separator(footer, orient="vertical").pack(side="left", fill="y", padx=9)
        ttk.Label(footer, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(footer, text=f"© 2026 {DEVELOPER_NAME}", foreground="#6e6e73").pack(side="right", padx=(12, 0))

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于截图自动拼接",
            f"截图自动拼接  v{APP_VERSION}\n\n"
            f"Developed by {DEVELOPER_NAME}\n"
            "Copyright © 2026 KwoYeung\n\n"
            "开源图片拼接工具",
        )

    def _start_update_check(self) -> None:
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _check_for_update(self) -> None:
        release = check_latest_release()
        if release is not None and is_newer_version(release.version, APP_VERSION):
            self.events.put(("update_available", release))

    def _show_update_available(self, release: ReleaseInfo) -> None:
        self.update_release_url = release.url
        self.version_status.set(f"v{APP_VERSION}  ·  发现新版本 v{release.version}")
        self.version_label.configure(foreground="#b05a00", cursor="hand2")

    def _open_update_release(self, _event: tk.Event | None = None) -> None:
        if self.update_release_url:
            webbrowser.open(self.update_release_url)

    def _build_capture_page(self) -> None:
        page = self.capture_page
        page.rowconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)
        left = ttk.Frame(page, width=455)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(5, weight=1)

        source_row = ttk.Frame(left)
        source_row.grid(row=0, column=0, sticky="ew")
        ttk.Button(source_row, text="添加已有图片", command=self._add_images).pack(side="left", fill="x", expand=True)
        capture_supported = sys.platform in {"darwin", "win32"}
        self.capture_button = ttk.Button(
            source_row,
            text="设置固定截图区域",
            command=self._capture_or_select_region,
            state="normal" if capture_supported else "disabled",
        )
        self.capture_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        region_row = ttk.Frame(left)
        region_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(region_row, textvariable=self.region_status, foreground="#555555").pack(side="left", fill="x", expand=True)
        self.reset_region_button = ttk.Button(region_row, text="重设区域", command=self._select_fixed_region, state="disabled")
        self.reset_region_button.pack(side="right")
        self.clear_region_button = ttk.Button(region_row, text="取消区域", command=self._clear_fixed_region, state="disabled")
        self.clear_region_button.pack(side="right", padx=(0, 6))

        hotkey_row = ttk.Frame(left)
        hotkey_row.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(hotkey_row, text="截图快捷键").pack(side="left")
        hotkey_box = ttk.Combobox(
            hotkey_row,
            textvariable=self.hotkey_choice,
            values=hotkey_choices(),
            state="readonly",
            width=18,
        )
        hotkey_box.pack(side="right")
        hotkey_box.bind("<<ComboboxSelected>>", lambda _event: self._register_hotkey())
        self.hotkey_status_label = ttk.Label(left, textvariable=self.hotkey_status, foreground="#b42318")
        self.hotkey_status_label.grid(
            row=3, column=0, sticky="w", pady=(2, 8)
        )

        ttk.Label(left, text="截图列表（支持多选）", font=("TkDefaultFont", 11, "bold")).grid(
            row=4, column=0, sticky="w"
        )
        tree_frame = ttk.Frame(left)
        tree_frame.grid(row=5, column=0, sticky="nsew", pady=(5, 6))
        self.image_tree = ttk.Treeview(
            tree_frame,
            columns=("name", "size"),
            show="tree headings",
            selectmode="extended",
            style="Image.Treeview",
        )
        self.image_tree.heading("#0", text="缩略图")
        self.image_tree.heading("name", text="名称")
        self.image_tree.heading("size", text="尺寸")
        self.image_tree.column("#0", width=124, minwidth=124, stretch=False)
        self.image_tree.column("name", width=205, minwidth=120)
        self.image_tree.column("size", width=88, minwidth=70, stretch=False, anchor="center")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.image_tree.yview)
        self.image_tree.configure(yscrollcommand=tree_scroll.set)
        self.image_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.image_tree.bind("<<TreeviewSelect>>", self._show_selected_preview)

        edit_row = ttk.Frame(left)
        edit_row.grid(row=6, column=0, sticky="ew")
        ttk.Button(edit_row, text="移除选中", command=self._remove).pack(side="left", fill="x", expand=True)
        ttk.Button(edit_row, text="全部清理", command=self._remove_all).pack(side="left", fill="x", expand=True, padx=(6, 0))

        options = ttk.LabelFrame(left, text="拼接设置", padding=8)
        options.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        mode_row = ttk.Frame(options)
        mode_row.pack(fill="x")
        ttk.Label(mode_row, text="模式").pack(side="left")
        mode_box = ttk.Combobox(
            mode_row,
            textvariable=self.stitch_mode,
            values=("自由平移画布", "纵向长图"),
            state="readonly",
            width=16,
        )
        mode_box.pack(side="right")
        mode_box.bind("<<ComboboxSelected>>", self._mode_changed)
        ttk.Label(
            options,
            textvariable=self.mode_hint,
            foreground="#9a5a00",
            wraplength=405,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(7, 0))
        self.mosaic_strategy_row = ttk.Frame(options)
        ttk.Label(self.mosaic_strategy_row, text="自由平移策略").pack(side="left")
        mosaic_strategy_box = ttk.Combobox(
            self.mosaic_strategy_row,
            textvariable=self.mosaic_strategy,
            values=("自动容错", "严格顺序"),
            state="readonly",
            width=12,
        )
        mosaic_strategy_box.pack(side="right")
        mosaic_strategy_box.bind("<<ComboboxSelected>>", self._strategy_changed)
        self.mosaic_strategy_hint_label = ttk.Label(
            options,
            textvariable=self.mosaic_strategy_hint,
            foreground="#666666",
            wraplength=405,
            justify="left",
        )
        self.auto_sort_check = ttk.Checkbutton(
            options,
            text="根据内容自动排序",
            variable=self.auto_sort,
            command=self._mode_changed,
        )
        self.auto_sort_check.pack(anchor="w", pady=(7, 0))
        self.order_row = ttk.Frame(options)
        ttk.Label(self.order_row, text="手动调整纵向顺序").pack(side="left")
        ttk.Button(self.order_row, text="上移", command=lambda: self._move(-1)).pack(side="right")
        ttk.Button(self.order_row, text="下移", command=lambda: self._move(1)).pack(side="right", padx=5)

        self.run_button = ttk.Button(left, text="开始二维拼接", style="Accent.TButton", command=self._start)
        self.run_button.grid(row=8, column=0, sticky="ew", pady=(10, 0))

        preview = ttk.LabelFrame(page, text="单张截图预览", padding=8)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        self.input_preview_canvas = tk.Canvas(preview, background="#262626", highlightthickness=0)
        self.input_preview_canvas.grid(row=0, column=0, sticky="nsew")
        ttk.Label(preview, textvariable=self.preview_info, anchor="center").grid(row=1, column=0, sticky="ew", pady=(7, 0))

    def _build_result_page(self) -> None:
        page = self.result_page
        page.rowconfigure(0, weight=1)
        page.columnconfigure(0, weight=1)
        preview = ttk.LabelFrame(page, text="拼接预览", padding=8)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(preview, background="#252525", highlightthickness=0)
        self.canvas.bind("<MouseWheel>", self._zoom_result_preview)
        self.canvas.bind("<Button-4>", lambda event: self._zoom_result_preview(event, 1))
        self.canvas.bind("<Button-5>", lambda event: self._zoom_result_preview(event, -1))
        vertical = ttk.Scrollbar(preview, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(preview, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        ttk.Label(preview, textvariable=self.result_zoom_text, anchor="center").grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        side = ttk.Frame(page, width=330)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_propagate(False)
        ttk.Label(side, text="匹配记录", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        self.report = tk.Text(side, wrap="word", state="disabled", background="#f3f3f3")
        self.report.pack(fill="both", expand=True, pady=(6, 10))
        self.save_button = ttk.Button(side, text="保存拼接结果…", style="Accent.TButton", command=self._save, state="disabled")
        self.save_button.pack(fill="x")
        ttk.Label(
            side,
            text=(
                "预览发现缺漏？\n"
                "可返回“截图管理”补充、移除或替换图片。\n"
                "然后直接重新拼接，无需清空列表。\n"
                "出现未对齐时，请核实画布／内容缩放比例。"
            ),
            foreground="#9a5a00",
            justify="left",
        ).pack(fill="x", pady=(0, 8))
        ttk.Button(side, text="返回截图管理", command=lambda: self.notebook.select(self.capture_page)).pack(fill="x", pady=(6, 0))
        ttk.Button(side, text="清理本轮内容", command=self._remove_all).pack(fill="x", pady=(6, 0))

    # ---------- Thumbnail list ----------

    def _make_thumbnail(self, path: str) -> tuple[tk.PhotoImage | None, tuple[int, int] | None]:
        try:
            image = read_image(path)
        except Exception:
            return None, None
        height, width = image.shape[:2]
        scale = min(1.0, 112 / width, 72 / height)
        shown = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        tile = np.full((76, 116, 3), 238, dtype=np.uint8)
        top, left = (76 - shown.shape[0]) // 2, (116 - shown.shape[1]) // 2
        tile[top : top + shown.shape[0], left : left + shown.shape[1]] = shown
        ok, encoded = cv2.imencode(".png", tile)
        if not ok:
            return None, (width, height)
        return tk.PhotoImage(data=base64.b64encode(encoded).decode("ascii")), (width, height)

    def _refresh_list(self, select_index: int | None = None) -> None:
        self.image_tree.delete(*self.image_tree.get_children())
        self.thumbnail_photos.clear()
        for index, path in enumerate(self.paths):
            photo, size = self._make_thumbnail(path)
            if photo is not None:
                self.thumbnail_photos.append(photo)
            if size is not None:
                self.thumbnail_meta[path] = size
            width, height = size or (0, 0)
            self.image_tree.insert(
                "",
                "end",
                iid=str(index),
                image=photo or "",
                values=(f"{index + 1}. {Path(path).name}", f"{width}×{height}" if width else "读取失败"),
            )
        if select_index is not None and 0 <= select_index < len(self.paths):
            item = str(select_index)
            self.image_tree.selection_set(item)
            self.image_tree.focus(item)
            self.image_tree.see(item)
            self._show_selected_preview()
        elif not self.paths:
            self.input_preview_photo = None
            self.input_preview_canvas.delete("all")
            self.preview_info.set("当前没有图片")

    def _selected_indices(self) -> list[int]:
        return sorted((int(item) for item in self.image_tree.selection()), reverse=True)

    def _show_selected_preview(self, _event: tk.Event | None = None) -> None:
        selected = self._selected_indices()
        if not selected:
            return
        index = selected[-1]
        if not (0 <= index < len(self.paths)):
            return
        path = self.paths[index]
        try:
            image = read_image(path)
        except Exception as exc:
            self.preview_info.set(str(exc))
            return
        canvas_width = max(320, self.input_preview_canvas.winfo_width() - 24)
        canvas_height = max(300, self.input_preview_canvas.winfo_height() - 24)
        scale = min(1.0, canvas_width / image.shape[1], canvas_height / image.shape[0])
        shown = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".png", shown)
        if not ok:
            return
        self.input_preview_photo = tk.PhotoImage(data=base64.b64encode(encoded).decode("ascii"))
        self.input_preview_canvas.delete("all")
        self.input_preview_canvas.create_image(
            max(0, self.input_preview_canvas.winfo_width() // 2),
            max(0, self.input_preview_canvas.winfo_height() // 2),
            image=self.input_preview_photo,
            anchor="center",
        )
        source = "临时截图" if path in self.captured_paths else "导入图片"
        self.preview_info.set(f"第 {index + 1} 张 · {source} · {image.shape[1]} × {image.shape[0]} · {Path(path).name}")

    # ---------- Input management ----------

    def _add_images(self) -> None:
        chosen = filedialog.askopenfilenames(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("所有文件", "*.*")]
        )
        for path in chosen:
            if path not in self.paths:
                self.paths.append(path)
        self._refresh_list(len(self.paths) - 1 if self.paths else None)
        self.status.set(f"当前共有 {len(self.paths)} 张图片")

    def _remove(self) -> None:
        selected = self._selected_indices()
        if not selected:
            return
        next_index = min(selected[-1], max(0, len(self.paths) - len(selected) - 1))
        for index in selected:
            if 0 <= index < len(self.paths):
                path = self.paths.pop(index)
                self._delete_cached_capture(path)
                self.thumbnail_meta.pop(path, None)
        self._refresh_list(next_index if self.paths else None)
        self._set_controller_default_text()
        self.status.set(f"已移除 {len(selected)} 张，剩余 {len(self.paths)} 张")

    def _remove_all(self) -> None:
        if not self.paths and self.result is None:
            return
        cached = sum(path in self.captured_paths for path in self.paths)
        imported = len(self.paths) - cached
        if not messagebox.askyesno(
            "全部清理",
            f"将清空图片列表和当前拼接结果。\n\n"
            f"{cached} 张临时截图会从缓存删除；"
            f"{imported} 张导入原图只从列表移除，不会删除原文件。\n\n是否继续？",
        ):
            return
        for path in self.paths.copy():
            self._delete_cached_capture(path)
        self.paths.clear()
        self.thumbnail_meta.clear()
        self._refresh_list()
        self._clear_result()
        self._set_controller_default_text()
        self.notebook.select(self.capture_page)
        self.status.set("已清理截图缓存、图片列表和拼接结果")

    def _clear_result(self) -> None:
        """Release the in-memory stitched image and reset its preview."""
        self.result = None
        self.result_preview_photo = None
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 0, 0))
        self.report.configure(state="normal")
        self.report.delete("1.0", tk.END)
        self.report.configure(state="disabled")
        self.save_button.configure(state="disabled")

    def _move(self, step: int) -> None:
        selected = self._selected_indices()
        if len(selected) != 1:
            return
        old, new = selected[0], selected[0] + step
        if 0 <= new < len(self.paths):
            self.paths[old], self.paths[new] = self.paths[new], self.paths[old]
            self._refresh_list(new)

    def _delete_cached_capture(self, path_text: str) -> bool:
        if path_text not in self.captured_paths:
            return False
        path = Path(path_text)
        if path.parent != self.session_cache_dir:
            self.captured_paths.discard(path_text)
            return False
        path.unlink(missing_ok=True)
        self.captured_paths.discard(path_text)
        return True

    def _cleanup_capture_cache(self, remove_from_list: bool = True) -> int:
        cached = set(self.captured_paths)
        removed = sum(1 for path in cached if self._delete_cached_capture(path))
        if remove_from_list:
            self.paths = [path for path in self.paths if path not in cached]
            self._refresh_list()
        self._set_controller_default_text()
        return removed

    def _cleanup_session_files(self) -> None:
        if not self.session_cache_dir.exists():
            return
        for path in self.session_cache_dir.glob("*.png"):
            path.unlink(missing_ok=True)
        try:
            self.session_cache_dir.rmdir()
        except OSError:
            pass

    # ---------- Capture region and overlays ----------

    def _capture_or_select_region(self) -> None:
        self._select_fixed_region() if self.capture_region is None else self._capture_fixed_region()

    def _clear_fixed_region(self) -> None:
        """Remove the persistent frame without touching captured images."""
        self.capture_region = None
        self._destroy_capture_controller()
        self._destroy_capture_frame()
        self.capture_button.configure(text="设置固定截图区域")
        self.reset_region_button.configure(state="disabled")
        self.clear_region_button.configure(state="disabled")
        self.region_status.set("固定区域：尚未设置")
        self.status.set("已取消固定截图区域；已截取的图片仍保留在列表中")

    def _display_rects(self) -> list[ScreenRect]:
        return active_displays() or [ScreenRect(0, 0, self.winfo_screenwidth(), self.winfo_screenheight())]

    def _place_window(self, window: tk.Toplevel, x: int, y: int, width: int, height: int) -> None:
        window.update_idletasks()
        virtual_right = window.winfo_vrootx() + window.winfo_vrootwidth()
        virtual_bottom = window.winfo_vrooty() + window.winfo_vrootheight()
        x_token = f"+{x}" if x >= 0 else f"-{max(0, virtual_right - (x + width))}"
        y_token = f"+{y}" if y >= 0 else f"-{max(0, virtual_bottom - (y + height))}"
        window.geometry(f"{max(1, width)}x{max(1, height)}{x_token}{y_token}")
        if sys.platform == "win32":
            window.update_idletasks()
            place_windows_window(window.winfo_id(), (x, y, width, height))

    def _select_fixed_region(self) -> None:
        if sys.platform not in {"darwin", "win32"}:
            messagebox.showinfo("暂不支持", "当前内置截图支持 macOS 和 Windows。")
            return
        self.status.set("请拖动选择固定截图区域；按 Esc 可取消")
        self.withdraw()
        self._hide_capture_frame()
        self._hide_capture_controller()
        for display in self._display_rects():
            overlay = tk.Toplevel(self)
            self.selectors.append(overlay)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.attributes("-alpha", 0.32)
            self._place_window(overlay, display.x, display.y, display.width, display.height)
            canvas = tk.Canvas(overlay, background="black", cursor="crosshair", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.create_text(
                display.width // 2,
                42,
                text="拖动设置固定取景框    可在任意显示器选择    Esc 取消",
                fill="white",
                font=("TkDefaultFont", 18, "bold"),
            )
            canvas.bind("<ButtonPress-1>", lambda event, item=canvas: self._selection_begin(item, event))
            canvas.bind("<B1-Motion>", lambda event, item=canvas: self._selection_move(item, event))
            canvas.bind("<ButtonRelease-1>", self._selection_finish)
            overlay.bind("<Escape>", lambda _event: self._selection_cancel())
        if self.selectors:
            self.selectors[-1].focus_force()

    def _selection_begin(self, canvas: tk.Canvas, event: tk.Event) -> None:
        if self.selection_rectangle is not None and self.selection_canvas is not None:
            self.selection_canvas.delete(self.selection_rectangle)
        self.selection_start = (event.x_root, event.y_root)
        self.selection_local_start = (event.x, event.y)
        self.selection_canvas = canvas
        self.selection_rectangle = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#2da8ff", width=4, fill="white", stipple="gray25"
        )

    def _selection_move(self, canvas: tk.Canvas, event: tk.Event) -> None:
        if self.selection_local_start is None or self.selection_rectangle is None or canvas is not self.selection_canvas:
            return
        start_x, start_y = self.selection_local_start
        canvas.coords(self.selection_rectangle, start_x, start_y, event.x, event.y)

    def _selection_finish(self, event: tk.Event) -> None:
        if self.selection_start is None:
            return
        start_x, start_y = self.selection_start
        x, y = min(start_x, event.x_root), min(start_y, event.y_root)
        width, height = abs(event.x_root - start_x), abs(event.y_root - start_y)
        if width < 100 or height < 100:
            self.status.set("固定区域至少需要 100 × 100；请重新设置")
            self._close_selector()
            self._show_capture_frame()
            self._show_capture_controller()
            return
        self.capture_region = (x, y, width, height)
        self.region_status.set(f"固定区域：{width} × {height}，位置 ({x}, {y})")
        self.capture_button.configure(text="按固定区域截图")
        self.reset_region_button.configure(state="normal")
        self.clear_region_button.configure(state="normal")
        self._close_selector()
        self._create_capture_frame()
        self._create_capture_controller()
        self.status.set(f"固定取景框已显示；使用 {self.hotkey_choice.get()} 可直接截图")

    def _selection_cancel(self) -> None:
        self.status.set("已取消设置固定区域")
        self._close_selector()
        self._show_capture_frame()
        self._show_capture_controller()

    def _close_selector(self) -> None:
        for selector in self.selectors:
            selector.destroy()
        self.selectors.clear()
        self.selection_start = self.selection_local_start = None
        self.selection_canvas = None
        self.selection_rectangle = None
        self.deiconify()
        self.lift()
        self.focus_force()

    def _create_capture_frame(self) -> None:
        self._destroy_capture_frame()
        if self.capture_region is None:
            return
        for piece in frame_pieces(self.capture_region, thickness=2):
            border = tk.Toplevel(self)
            border.withdraw()
            native_title = f"capture-border-{uuid.uuid4().hex}"
            border.title(native_title)
            border.overrideredirect(True)
            border.attributes("-topmost", True)
            border.configure(background="#00a8ff")
            if sys.platform == "darwin":
                try:
                    border.tk.call(
                        "::tk::unsupported::MacWindowStyle", "style", border._w, "help", "noActivates doesNotHide"
                    )
                except tk.TclError:
                    pass
            self._place_window(border, piece.x, piece.y, piece.width, piece.height)
            border.deiconify()
            border.update_idletasks()
            self.capture_frame_windows.append(border)
            self.capture_frame_native.append(configure_persistent_overlay(native_title))

    def _hide_capture_frame(self) -> None:
        for window in self.capture_frame_windows:
            window.withdraw()

    def _show_capture_frame(self) -> None:
        for index, window in enumerate(self.capture_frame_windows):
            window.deiconify()
            window.update_idletasks()
            native = self.capture_frame_native[index] if index < len(self.capture_frame_native) else None
            if native is not None:
                show_native_overlay(native)
            else:
                window.attributes("-topmost", True)
                window.lift()

    def _destroy_capture_frame(self) -> None:
        for window in self.capture_frame_windows:
            window.destroy()
        self.capture_frame_windows.clear()
        self.capture_frame_native.clear()

    def _controller_position(self, width: int, height: int) -> tuple[int, int]:
        if self.capture_region is None:
            return 40, 80
        x, y, region_width, _ = self.capture_region
        return x + max(8, (region_width - width) // 2), y + 12

    def _create_capture_controller(self) -> None:
        self._destroy_capture_controller()
        if self.capture_region is None:
            return
        width, height = 370, 32
        left, top = self._controller_position(width, height)
        controller = tk.Toplevel(self)
        controller.withdraw()
        native_title = f"capture-controller-{uuid.uuid4().hex}"
        controller.title(native_title)
        controller.overrideredirect(True)
        controller.attributes("-topmost", True)
        transparent = "#ff00ff"
        controller_background = controller.cget("background")
        if sys.platform == "win32":
            try:
                controller.attributes("-transparentcolor", transparent)
                controller_background = transparent
            except tk.TclError:
                pass
        elif sys.platform == "darwin":
            try:
                controller.attributes("-transparent", True)
                controller.configure(background="systemTransparent")
                controller_background = "systemTransparent"
            except tk.TclError:
                pass
        controller.configure(background=controller_background)
        canvas = tk.Canvas(
            controller,
            width=width,
            height=height,
            background=controller_background,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        self._rounded_rectangle(
            canvas, 1, 1, 238, 30, 8,
            fill="#ffffff", outline="#c7c7cc", tags=("capture_action", "capture_bg"),
        )
        text_item = canvas.create_text(
            120, 15, text=self.controller_text.get(), fill="#1d1d1f",
            font=("TkDefaultFont", 11), tags=("capture_action",),
        )
        self._rounded_rectangle(
            canvas, 246, 1, 369, 30, 8,
            fill="#ffffff", outline="#c7c7cc", tags=("menu_action", "menu_bg"),
        )
        canvas.create_text(
            307, 15, text="🏠 主菜单", fill="#1d1d1f",
            font=("TkDefaultFont", 11), tags=("menu_action",),
        )
        canvas.tag_bind("capture_action", "<ButtonPress-1>", self._controller_first_press)
        canvas.tag_bind("menu_action", "<ButtonPress-1>", self._controller_menu_first_press)
        canvas.tag_bind("capture_action", "<Enter>", lambda _event: canvas.itemconfigure("capture_bg", fill="#e9e9ed"))
        canvas.tag_bind("capture_action", "<Leave>", lambda _event: canvas.itemconfigure("capture_bg", fill="#ffffff"))
        canvas.tag_bind("menu_action", "<Enter>", lambda _event: canvas.itemconfigure("menu_bg", fill="#e9e9ed"))
        canvas.tag_bind("menu_action", "<Leave>", lambda _event: canvas.itemconfigure("menu_bg", fill="#ffffff"))
        self._place_window(controller, left, top, width, height)
        controller.deiconify()
        controller.update_idletasks()
        self.capture_controller = controller
        self.capture_controller_canvas = canvas
        self.capture_controller_text_item = text_item
        self.capture_controller_native = configure_persistent_overlay(native_title, ignore_mouse=False)
        self._set_controller_default_text()

    @staticmethod
    def _rounded_rectangle(
        canvas: tk.Canvas,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        **kwargs: object,
    ) -> int:
        points = (
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        )
        return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _controller_first_press(self, _event: tk.Event) -> str:
        self.after_idle(self._capture_fixed_region)
        return "break"

    def _controller_menu_first_press(self, _event: tk.Event) -> str:
        self.after_idle(self._restore_main_window)
        return "break"

    def _hide_capture_controller(self) -> None:
        if self.capture_controller is not None:
            self.capture_controller.withdraw()

    def _show_capture_controller(self) -> None:
        if self.capture_controller is None:
            return
        self.capture_controller.deiconify()
        self.capture_controller.update_idletasks()
        if self.capture_controller_native is not None:
            show_native_overlay(self.capture_controller_native)
        else:
            self.capture_controller.attributes("-topmost", True)
            self.capture_controller.lift()

    def _destroy_capture_controller(self) -> None:
        if self.controller_reset_job is not None:
            self.after_cancel(self.controller_reset_job)
            self.controller_reset_job = None
        if self.capture_controller is not None:
            self.capture_controller.destroy()
        self.capture_controller = None
        self.capture_controller_button = None
        self.capture_controller_menu_button = None
        self.capture_controller_canvas = None
        self.capture_controller_text_item = None
        self.capture_controller_native = None

    def _restore_main_window(self) -> None:
        try:
            self.state("normal")
        except tk.TclError:
            self.deiconify()
        self.deiconify()
        self.update_idletasks()
        self.notebook.select(self.capture_page)
        self.lift()
        self.focus_force()

    # ---------- Hotkey and capture ----------

    def _register_hotkey(self) -> None:
        if not hasattr(self, "global_hotkey"):
            return
        success, detail = self.global_hotkey.register(self.hotkey_choice.get())
        if success:
            self.hotkey_status.set("")
            self.hotkey_status_label.grid_remove()
        else:
            self.hotkey_status.set(detail)
            self.hotkey_status_label.grid()
            self.status.set(detail)
        self._set_controller_default_text()

    def _hotkey_capture(self) -> None:
        if self.capture_region is None:
            self._restore_main_window()
            self.notebook.select(self.capture_page)
            self.status.set("请先设置固定截图区域")
            return
        self._capture_fixed_region()

    def _set_controller_default_text(self) -> None:
        count = len(self.captured_paths)
        shortcut = self.hotkey_choice.get()
        self._update_controller_text(f"📷 截图  {count} 张  ·  {shortcut}" if count else f"📷 截图  ·  {shortcut}")

    def _update_controller_text(self, text: str) -> None:
        self.controller_text.set(text)
        if self.capture_controller_canvas is not None and self.capture_controller_text_item is not None:
            self.capture_controller_canvas.itemconfigure(self.capture_controller_text_item, text=text)

    def _notify_capture_success(self, position: int) -> None:
        if self.controller_reset_job is not None:
            self.after_cancel(self.controller_reset_job)
        self._update_controller_text(f"✓  第 {position} 张截图成功")
        self.controller_reset_job = self.after(1800, self._reset_controller_notification)

    def _reset_controller_notification(self) -> None:
        self.controller_reset_job = None
        self._set_controller_default_text()

    def _next_capture_path(self) -> Path:
        while True:
            self.capture_serial += 1
            path = self.session_cache_dir / capture_filename(datetime.now(), self.capture_serial)
            if not path.exists():
                return path

    def _capture_fixed_region(self) -> None:
        if self.capture_in_progress:
            return
        if self.capture_region is None:
            self._select_fixed_region()
            return
        self.capture_in_progress = True
        path = self._next_capture_path()
        self.status.set("正在按固定区域截图…")
        root_state = self.state()
        root_was_visible = root_state not in {"withdrawn", "iconic"}
        if root_was_visible:
            self.withdraw()
        self._hide_capture_frame()
        self._hide_capture_controller()
        self.update()
        time.sleep(0.36 if sys.platform == "darwin" else 0.20)
        error: Exception | None = None
        succeeded = False
        try:
            succeeded = capture_region_to_file(path, self.capture_region)
        except Exception as exc:
            error = exc
        finally:
            if root_was_visible:
                self.deiconify()
                self.lower()
            self._show_capture_frame()
            self._show_capture_controller()
            self.capture_in_progress = False

        if succeeded and path.exists() and path.stat().st_size > 0:
            path_text = str(path)
            self.paths.append(path_text)
            self.captured_paths.add(path_text)
            self._refresh_list(len(self.paths) - 1)
            captured = read_image(path)
            self.region_status.set(f"固定区域：输出 {captured.shape[1]} × {captured.shape[0]} 像素")
            position = len(self.paths)
            self.status.set(f"第 {position} 张截图成功；继续移动底图后可再次截图")
            self._notify_capture_success(position)
        else:
            path.unlink(missing_ok=True)
            message = str(error) if error else "系统没有生成截图文件"
            self.status.set(f"截图失败：{message}")
            self._update_controller_text("✕  截图失败，请重试")
            if self.controller_reset_job is not None:
                self.after_cancel(self.controller_reset_job)
            self.controller_reset_job = self.after(1800, self._reset_controller_notification)

    # ---------- Stitching and result page ----------

    def _mode_changed(self, _event: tk.Event | None = None) -> None:
        is_vertical = self.stitch_mode.get() == "纵向长图"
        if is_vertical:
            self.mode_hint.set("提示：纵向截图建议保持相同宽度和内容缩放比。")
            self.mosaic_strategy_row.pack_forget()
            self.mosaic_strategy_hint_label.pack_forget()
            self.auto_sort_check.pack(anchor="w", pady=(7, 0))
            if self.auto_sort.get():
                self.order_row.pack_forget()
            else:
                self.order_row.pack(fill="x", pady=(7, 0))
        else:
            self.mode_hint.set("提示：图片宽高可以不同，但画布／内容缩放比建议保持一致。")
            self.mosaic_strategy_row.pack(fill="x", pady=(7, 0))
            self._strategy_changed()
            self.mosaic_strategy_hint_label.pack(anchor="w", fill="x", pady=(5, 0))
            self.auto_sort_check.pack_forget()
            self.order_row.pack_forget()
        self.run_button.configure(text="开始纵向拼接" if is_vertical else "开始二维拼接")

    def _strategy_changed(self, _event: tk.Event | None = None) -> None:
        if self.mosaic_strategy.get() == "严格顺序":
            self.mosaic_strategy_hint.set(
                "严格顺序：按列表顺序逐张拼接，相邻图片一旦匹配失败就停止，适合逐张排查。"
            )
        else:
            self.mosaic_strategy_hint.set(
                "自动容错（推荐）：按内容寻找能连接的主要图片组，不依赖导入顺序，并跳过少量异常图。"
            )

    def _start(self) -> None:
        if len(self.paths) < 2:
            messagebox.showinfo("需要更多图片", "请至少选择两张截图。")
            return
        self.run_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.status.set("正在读取图片…")
        threading.Thread(
            target=self._worker,
            args=(
                self.paths.copy(),
                self.auto_sort.get(),
                self.stitch_mode.get(),
                self.mosaic_strategy.get(),
            ),
            daemon=True,
        ).start()

    def _worker(self, paths: list[str], auto_sort: bool, mode: str, mosaic_strategy: str) -> None:
        try:
            images = [read_image(path) for path in paths]
            progress = lambda message: self.events.put(("status", message))
            result = (
                stitch_mosaic(images, progress=progress, strict_order=mosaic_strategy == "严格顺序")
                if mode == "自由平移画布"
                else stitch_images(images, auto_sort, progress=progress)
            )
            self.events.put(("done", result))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(payload))
                elif kind == "hotkey":
                    self._hotkey_capture()
                elif kind == "update_available":
                    self._show_update_available(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self.run_button.configure(state="normal")
                    self.status.set("拼接失败")
                    messagebox.showerror("拼接失败", str(payload))
                elif kind == "done":
                    self._show_result(payload)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _show_result(self, result: StitchResult | MosaicResult) -> None:
        self.result = result
        self.run_button.configure(state="normal")
        self.save_button.configure(state="normal")
        self.status.set(f"完成：{result.image.shape[1]} × {result.image.shape[0]} 像素；{len(result.warnings)} 个警告")
        if isinstance(result, MosaicResult):
            placed_positions = [
                f"{i + 1}=({position[0]}, {position[1]})"
                for i, position in enumerate(result.positions)
                if position is not None
            ]
            lines = ["二维位置：" + "，".join(placed_positions)]
            for match in result.matches:
                state = f"位移 ({match.offset_x}, {match.offset_y})" if match.succeeded else "匹配失败，完整保留"
                lines.append(f"{match.first + 1} → {match.second + 1}：{match.confidence:.0%}，{state}")
        else:
            lines = ["排序：" + " → ".join(str(i + 1) for i in result.order)]
            for match in result.matches:
                state = f"裁去前 {match.cut_y}px" if match.succeeded else "匹配失败，完整保留"
                lines.append(f"{match.first + 1} → {match.second + 1}：{match.confidence:.0%}，{state}")
        lines.extend("⚠ " + warning for warning in result.warnings)
        self.report.configure(state="normal")
        self.report.delete("1.0", tk.END)
        self.report.insert("1.0", "\n".join(lines))
        self.report.configure(state="disabled")

        self.notebook.select(self.result_page)
        self.update_idletasks()
        max_width = max(360, self.canvas.winfo_width() - 28)
        scale = min(1.0, max_width / result.image.shape[1])
        pixels = max(1, result.image.shape[0] * result.image.shape[1])
        self.result_preview_scale = scale
        self.result_preview_min_scale = max(0.01, min(0.08, scale * 0.5))
        self.result_preview_max_scale = max(scale, min(4.0, (45_000_000 / pixels) ** 0.5))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._render_result_preview(scale)

    def _render_result_preview(self, scale: float) -> None:
        if self.result is None:
            return
        source = self.result.image
        width = max(1, round(source.shape[1] * scale))
        height = max(1, round(source.shape[0] * scale))
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        shown = cv2.resize(source, (width, height), interpolation=interpolation)
        ok, encoded = cv2.imencode(".png", shown)
        if ok:
            self.result_preview_photo = tk.PhotoImage(data=base64.b64encode(encoded).decode("ascii"))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self.result_preview_photo, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, shown.shape[1], shown.shape[0]))
            self.result_preview_scale = scale
            self.result_zoom_text.set(f"滚轮缩放：{scale:.0%}  ·  拖动滚动条查看画布")

    def _zoom_result_preview(self, event: tk.Event, direction: int | None = None) -> str:
        if self.result is None:
            return "break"
        if direction is None:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        old_scale = self.result_preview_scale
        factor = 1.15 if direction > 0 else 1 / 1.15
        new_scale = float(
            np.clip(old_scale * factor, self.result_preview_min_scale, self.result_preview_max_scale)
        )
        if abs(new_scale - old_scale) < 1e-6:
            return "break"

        # Keep the source point under the pointer stationary while zooming.
        source_x = self.canvas.canvasx(event.x) / old_scale
        source_y = self.canvas.canvasy(event.y) / old_scale
        self._render_result_preview(new_scale)
        self.canvas.update_idletasks()
        shown_width = max(1, round(self.result.image.shape[1] * new_scale))
        shown_height = max(1, round(self.result.image.shape[0] * new_scale))
        target_x = source_x * new_scale - event.x
        target_y = source_y * new_scale - event.y
        self.canvas.xview_moveto(max(0.0, target_x / shown_width))
        self.canvas.yview_moveto(max(0.0, target_y / shown_height))
        return "break"

    def _save(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("WebP", "*.webp")],
        )
        if not path:
            return
        try:
            write_image(path, self.result.image)
            removed = self._cleanup_capture_cache(remove_from_list=True)
            self.status.set(f"已保存到：{path}；已清理 {removed} 张临时截图缓存")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _on_close(self) -> None:
        self.global_hotkey.unregister()
        self._cleanup_capture_cache(remove_from_list=False)
        self._cleanup_session_files()
        self._destroy_capture_controller()
        self._destroy_capture_frame()
        self.destroy()


if __name__ == "__main__":
    StitchApp().mainloop()
