from __future__ import annotations

import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from focal_ai_export.core import export_dataset


class ExportApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Focal AI Export")
        self.geometry("760x590")
        self.minsize(680, 540)
        self.configure(bg="#F5F7FB")
        self.input_path = tk.StringVar(value=str(Path.home() / "Pictures"))
        self.output_path = tk.StringVar(value=str(Path.home() / "Desktop"))
        self.session_gap = tk.StringVar(value="90")
        self.burst_gap = tk.StringVar(value="5")
        self.status = tk.StringVar(value="사진은 읽기 전용으로 스캔됩니다. 시작할 준비가 됐어요.")
        self.events = queue.Queue()
        self._configure_style()
        self._draw()
        self.after(150, self._poll)

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("Title.TLabel", background="#FFFFFF", foreground="#13233F", font=("Helvetica", 25, "bold"))
        style.configure("Subtitle.TLabel", background="#FFFFFF", foreground="#52627A", font=("Helvetica", 12))
        style.configure("Section.TLabel", background="#FFFFFF", foreground="#13233F", font=("Helvetica", 13, "bold"))
        style.configure("Hint.TLabel", background="#FFFFFF", foreground="#65758C", font=("Helvetica", 10))
        style.configure("Status.TLabel", background="#EAF2FF", foreground="#224A8D", font=("Helvetica", 11))
        style.configure("Accent.TButton", font=("Helvetica", 12, "bold"), padding=(18, 11), background="#246BFD", foreground="#FFFFFF", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#1758D6"), ("disabled", "#A8BFED")])
        style.configure("Browse.TButton", padding=(11, 7), background="#EEF3FC", foreground="#24436D", borderwidth=0)
        style.map("Browse.TButton", background=[("active", "#DCE8FA")])

    def _draw(self):
        outer = ttk.Frame(self, padding=(30, 26), style="Card.TFrame")
        outer.pack(fill="both", expand=True, padx=22, pady=20)
        ttk.Label(outer, text="Focal AI Export", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="사진은 그대로 두고, EXIF 초점거리 데이터를 AI 분석용 파일로 정리합니다.", style="Subtitle.TLabel", wraplength=670).pack(anchor="w", pady=(5, 22))

        guide = ttk.Frame(outer, style="Card.TFrame")
        guide.pack(fill="x", pady=(0, 22))
        ttk.Label(guide, text="사용 방법", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            guide,
            text="1. 사진 폴더 선택   →   2. 결과 저장 위치 선택   →   3. AI 데이터 내보내기",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        for label, variable, chooser in (("사진 폴더", self.input_path, self._choose_input), ("결과 저장 위치", self.output_path, self._choose_output)):
            ttk.Label(outer, text=label, style="Section.TLabel").pack(anchor="w")
            row = ttk.Frame(outer, style="Card.TFrame")
            row.pack(fill="x", pady=(5, 17))
            entry = ttk.Entry(row, textvariable=variable, font=("Helvetica", 11))
            entry.pack(side="left", fill="x", expand=True, ipady=6)
            ttk.Button(row, text="폴더 선택", command=chooser, style="Browse.TButton").pack(side="left", padx=(10, 0))

        options = ttk.Frame(outer, style="Card.TFrame")
        options.pack(fill="x", pady=(0, 14))
        ttk.Label(options, text="세션 분리(분)", style="Section.TLabel").pack(side="left")
        ttk.Entry(options, textvariable=self.session_gap, width=6, font=("Helvetica", 11)).pack(side="left", padx=(7, 22), ipady=4)
        ttk.Label(options, text="Burst 간격(초)", style="Section.TLabel").pack(side="left")
        ttk.Entry(options, textvariable=self.burst_gap, width=6, font=("Helvetica", 11)).pack(side="left", padx=(7, 0), ipady=4)

        self.button = ttk.Button(outer, text="AI 데이터 내보내기", command=self._start, style="Accent.TButton")
        self.button.pack(anchor="w", pady=(1, 16))
        ttk.Label(outer, textvariable=self.status, style="Status.TLabel", wraplength=660, padding=(14, 12)).pack(fill="x")
        ttk.Label(outer, text="원본 사진 · EXIF · 위치 정보는 수정하거나 복사하지 않습니다.", style="Hint.TLabel").pack(anchor="w", pady=(13, 0))

    def _choose_input(self):
        value = filedialog.askdirectory(initialdir=self.input_path.get())
        if value: self.input_path.set(value)

    def _choose_output(self):
        value = filedialog.askdirectory(initialdir=self.output_path.get())
        if value: self.output_path.set(value)

    def _start(self):
        source, destination = Path(self.input_path.get()), Path(self.output_path.get())
        if not source.is_dir() or not destination.is_dir():
            messagebox.showerror("폴더 오류", "유효한 사진 폴더와 결과 저장 위치를 선택하세요."); return
        try:
            session_gap, burst_gap = int(self.session_gap.get()), int(self.burst_gap.get())
            if session_gap < 1 or burst_gap < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("설정 오류", "세션 간격은 1분 이상, Burst 간격은 0초 이상 정수여야 합니다.")
            return
        self.button.configure(state="disabled"); self.status.set("EXIF를 읽는 중…")
        threading.Thread(target=self._work, args=(source, destination, session_gap, burst_gap), daemon=True).start()

    def _work(self, source: Path, destination: Path, session_gap: int, burst_gap: int):
        try:
            result = export_dataset(
                source,
                destination / f"FocalAIExport_{datetime.now():%Y%m%d_%H%M%S}",
                session_gap,
                burst_gap,
            )
            self.events.put(("done", result))
        except Exception as error:
            self.events.put(("error", str(error)))

    def _poll(self):
        try:
            kind, value = self.events.get_nowait()
            self.button.configure(state="normal")
            if kind == "done":
                self.status.set(f"완료: {value.name} 폴더에 결과를 저장했습니다.")
                if messagebox.askyesno("내보내기 완료", f"AI 데이터셋을 만들었습니다.\n\n{value}\n\n결과 폴더를 열까요?"):
                    self._open_result(value)
            else:
                self.status.set("내보내기 실패"); messagebox.showerror("내보내기 실패", value)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _open_result(self, path: Path):
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])


if __name__ == "__main__":
    ExportApp().mainloop()
