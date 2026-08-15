from __future__ import annotations

import queue
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
        self.geometry("700x360")
        self.input_path = tk.StringVar(value=str(Path.home() / "Pictures"))
        self.output_path = tk.StringVar(value=str(Path.home() / "Desktop"))
        self.status = tk.StringVar(value="사진은 읽기 전용으로 스캔됩니다.")
        self.events = queue.Queue()
        self._draw()
        self.after(150, self._poll)

    def _draw(self):
        outer = ttk.Frame(self, padding=24); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Focal AI Export", font=("Helvetica", 22, "bold")).pack(anchor="w")
        ttk.Label(outer, text="EXIF 초점거리 데이터를 JSONL·CSV로 내보냅니다. 사진과 EXIF는 수정하지 않습니다.", wraplength=640).pack(anchor="w", pady=(5, 18))
        for label, variable, chooser in (("사진 폴더", self.input_path, self._choose_input), ("결과 저장 위치", self.output_path, self._choose_output)):
            ttk.Label(outer, text=label).pack(anchor="w")
            row = ttk.Frame(outer); row.pack(fill="x", pady=(3, 12))
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="선택", command=chooser).pack(side="left", padx=(8, 0))
        self.button = ttk.Button(outer, text="AI 데이터 내보내기", command=self._start); self.button.pack(anchor="w", pady=12)
        ttk.Label(outer, textvariable=self.status, wraplength=640).pack(anchor="w")

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
        self.button.configure(state="disabled"); self.status.set("EXIF를 읽는 중…")
        threading.Thread(target=self._work, args=(source, destination), daemon=True).start()

    def _work(self, source: Path, destination: Path):
        try:
            result = export_dataset(source, destination / f"FocalAIExport_{datetime.now():%Y%m%d_%H%M%S}")
            self.events.put(("done", result))
        except Exception as error:
            self.events.put(("error", str(error)))

    def _poll(self):
        try:
            kind, value = self.events.get_nowait()
            self.button.configure(state="normal")
            if kind == "done":
                self.status.set(f"완료: {value}"); messagebox.showinfo("완료", f"AI 데이터셋을 만들었습니다.\n{value}")
            else:
                self.status.set("내보내기 실패"); messagebox.showerror("내보내기 실패", value)
        except queue.Empty:
            pass
        self.after(150, self._poll)


if __name__ == "__main__":
    ExportApp().mainloop()
