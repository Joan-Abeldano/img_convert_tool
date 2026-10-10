from __future__ import annotations

import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import to_png
import to_portrait
import to_square

JOBS = {
    "square": ("Convert to 1:1", "square"),
    "portrait": ("Convert to portrait", "portrait"),
    "png": ("Convert to PNG", "png"),
}


def images_in(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in to_square.SUPPORTED
    )


def png_target(out: Path, path: Path, taken: set[Path]) -> Path:
    dst = out / f"{path.stem}.png"
    if path.suffix.lower() == ".png":
        return dst
    index = 2
    while dst in taken:
        dst = out / f"{path.stem}_{index}.png"
        index += 1
    taken.add(dst)
    return dst


def convert(folder: Path, mode: str, report) -> tuple[int, int]:
    if not folder.is_dir():
        report(f"Not a folder: {folder}")
        return 0, 0
    out = folder / JOBS[mode][1]
    files = images_in(folder)
    if not files:
        report(f"No supported images in {folder}")
        return 0, 0

    if mode == "portrait":
        args = to_portrait.Options()
    else:
        args = to_square.Options()
    out.mkdir(parents=True, exist_ok=True)
    report(f"{len(files)} image(s) -> {out}")

    written = failed = 0
    if mode == "png":
        taken = {out / f"{path.stem}.png" for path in files if path.suffix.lower() == ".png"}
    else:
        taken = {out / path.name for path in files if path.suffix.lower() in to_square.ALPHA_FORMATS}
    for path in files:
        try:
            if mode == "png":
                dst = png_target(out, path, taken)
                note = to_png.convert_to_png(path, dst, args)
                done = True
            else:
                if mode == "portrait":
                    dst = to_portrait.portrait_target(out, path, args, taken)
                    note = to_portrait.portrait_file(path, dst, args)
                else:
                    dst = to_square.square_target(out, path, args, taken)
                    note = to_square.square_file(path, dst, args)
                if note.startswith("already") and not dst.exists():
                    shutil.copy2(path, dst)
                done = dst.exists()
            report(f"  {'=' if note.startswith('already') else '+'} {path.name}: {note}")
            if done:
                written += 1
        except Exception as error:
            failed += 1
            report(f"  ! {path.name}: {type(error).__name__}: {error}")

    report(f"\nDone: {written} written, {failed} failed, in {out}")
    return written, failed


def self_test(folder: Path, report_path: Path) -> int:
    lines: list[str] = []
    failed = 0
    for mode in ("square", "portrait", "png"):
        lines.append(f"== {JOBS[mode][0]} ==")
        written, errors = convert(folder, mode, lines.append)
        failed += errors
        lines.append(f"written={written} failed={errors}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.folder = tk.StringVar(value=str(Path.cwd()))
        self.messages: queue.Queue[str] = queue.Queue()
        self.running = False

        root.title("logos_tool")

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.folder, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(frame, text="Choose...", command=self.choose).grid(row=0, column=2)

        self.buttons = {}
        for column, mode in enumerate(("square", "portrait", "png")):
            button = ttk.Button(frame, text=JOBS[mode][0], command=lambda m=mode: self.start(m))
            button.grid(row=1, column=column, sticky="ew", padx=(0, 6) if column < 2 else 0, pady=(12, 0))
            self.buttons[mode] = button

        self.log = tk.Text(frame, height=12, width=52, wrap="word", state="disabled")
        self.log.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        frame.rowconfigure(2, weight=1)
        self.append("Choose a folder, then press a button.")

    def append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose(self) -> None:
        picked = filedialog.askdirectory(initialdir=self.folder.get(), title="Folder with images")
        if picked:
            self.folder.set(picked)
            self.append(f"\n{picked}")

    def start(self, mode: str) -> None:
        if self.running:
            return
        folder = Path(self.folder.get()).expanduser()
        if not folder.is_dir():
            messagebox.showerror(JOBS[mode][0], f"Not a folder:\n{folder}")
            return

        self.running = True
        for button in self.buttons.values():
            button.configure(state="disabled")
        self.append(f"\n== {JOBS[mode][0]} ==")
        threading.Thread(target=self.work, args=(folder, mode), daemon=True).start()
        self.root.after(80, self.drain)

    def work(self, folder: Path, mode: str) -> None:
        try:
            convert(folder, mode, self.messages.put)
        except Exception as error:
            self.messages.put(f"  ! {type(error).__name__}: {error}")
        finally:
            self.messages.put(None)

    def drain(self) -> None:
        finished = False
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if message is None:
                finished = True
            else:
                self.append(message)

        if finished:
            self.running = False
            for button in self.buttons.values():
                button.configure(state="normal")
        else:
            self.root.after(80, self.drain)


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--self-test":
        return self_test(Path(sys.argv[2]),
                         Path(sys.argv[3] if len(sys.argv) > 3 else "self-test.txt"))

    window = tk.Tk()
    App(window)
    window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
