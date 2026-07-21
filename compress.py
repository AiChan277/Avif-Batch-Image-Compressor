import os
import re
import shutil
import zipfile
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageOps, ImageFile

# Optional plugins for wider format support
try:
    import pillow_avif  # registers AVIF support
except Exception:
    pass

try:
    import pillow_heif  # registers HEIF/HEIC support
    pillow_heif.register_heif_opener()
except Exception:
    pass

ImageFile.LOAD_TRUNCATED_IMAGES = True


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip().strip(".")
    return name or "folder"


def unique_folder_label(folder_path: str, used: set[str]) -> str:
    base = sanitize_filename(os.path.basename(os.path.normpath(folder_path)) or "folder")
    candidate = base
    i = 2
    while candidate.lower() in used:
        candidate = f"{base}_{i}"
        i += 1
    used.add(candidate.lower())
    return candidate


def iter_files(folder: str, recursive: bool = True):
    if recursive:
        for root, _, files in os.walk(folder):
            for file in files:
                yield os.path.join(root, file)
    else:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file():
                    yield entry.path


def make_avif_name(src_path: str, root_folder: str, root_label: str):
    rel = os.path.relpath(src_path, root_folder)
    rel_parent = os.path.dirname(rel)
    stem = Path(rel).stem
    out_rel = os.path.join(root_label, rel_parent, f"{stem}.avif")
    return out_rel


def convert_one_image(src_path: str, dst_path: str, quality: int = 80, lossless: bool = False):
    with Image.open(src_path) as im:
        im = ImageOps.exif_transpose(im)

        # For multipage / animated files, convert only first frame
        try:
            if getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) > 1:
                im.seek(0)
        except Exception:
            pass

        # Normalize mode for AVIF save
        has_alpha = (
            im.mode in ("RGBA", "LA")
            or ("transparency" in im.info)
        )

        if im.mode == "P":
            im = im.convert("RGBA" if has_alpha else "RGB")
        elif im.mode in ("CMYK", "YCbCr", "LAB", "HSV", "I;16", "I", "F"):
            im = im.convert("RGBA" if has_alpha else "RGB")
        elif im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA" if has_alpha else "RGB")

        os.makedirs(os.path.dirname(dst_path), exist_ok=True)

        save_kwargs = {
            "format": "AVIF",
            "quality": quality,
        }

        if lossless:
            save_kwargs["lossless"] = True

        if "icc_profile" in im.info:
            save_kwargs["icc_profile"] = im.info["icc_profile"]
        if "exif" in im.info:
            save_kwargs["exif"] = im.info["exif"]

        im.save(dst_path, **save_kwargs)


class AVIFBatchGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Batch JPG/Images to AVIF → ZIP")
        self.geometry("980x680")
        self.minsize(900, 620)

        self.folder_list = []
        self.output_zip = tk.StringVar()
        self.quality = tk.IntVar(value=80)
        self.lossless = tk.BooleanVar(value=False)
        self.recursive = tk.BooleanVar(value=True)
        self.overwrite_zip = tk.BooleanVar(value=True)

        self.progress_value = tk.IntVar(value=0)
        self.progress_total = tk.IntVar(value=1)
        self.status_text = tk.StringVar(value="Siap.")

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 8}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        left = ttk.Frame(top)
        left.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="Folder sumber").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=10, selectmode=tk.EXTENDED)
        self.listbox.pack(fill="both", expand=True, pady=(5, 8))

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x")

        ttk.Button(btn_row, text="Tambah folder", command=self.add_folder).pack(side="left")
        ttk.Button(btn_row, text="Hapus terpilih", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Bersihkan", command=self.clear_folders).pack(side="left")

        right = ttk.Frame(top)
        right.pack(side="right", fill="y", padx=(14, 0))

        ttk.Label(right, text="Output ZIP").pack(anchor="w")
        out_row = ttk.Frame(right)
        out_row.pack(fill="x", pady=(5, 10))
        ttk.Entry(out_row, textvariable=self.output_zip, width=40).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="Pilih", command=self.choose_output_zip).pack(side="left", padx=6)

        opts = ttk.LabelFrame(right, text="Opsi")
        opts.pack(fill="x", pady=8)

        ttk.Checkbutton(opts, text="Recursive (scan subfolder)", variable=self.recursive).pack(anchor="w", padx=10, pady=(8, 2))
        ttk.Checkbutton(opts, text="Lossless AVIF", variable=self.lossless).pack(anchor="w", padx=10, pady=2)
        ttk.Checkbutton(opts, text="Overwrite ZIP jika ada", variable=self.overwrite_zip).pack(anchor="w", padx=10, pady=(2, 8))

        qframe = ttk.Frame(opts)
        qframe.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(qframe, text="Quality").pack(side="left")
        ttk.Scale(qframe, from_=0, to=100, orient="horizontal", command=self._sync_quality).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(qframe, textvariable=self.quality, width=4).pack(side="left")

        action = ttk.Frame(right)
        action.pack(fill="x", pady=(8, 0))
        self.start_btn = ttk.Button(action, text="Convert → ZIP", command=self.start_batch)
        self.start_btn.pack(fill="x")

        progress_box = ttk.LabelFrame(self, text="Progress")
        progress_box.pack(fill="x", **pad)

        self.progress = ttk.Progressbar(progress_box, mode="determinate", maximum=1, variable=self.progress_value)
        self.progress.pack(fill="x", padx=10, pady=(10, 4))

        status_row = ttk.Frame(progress_box)
        status_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(status_row, textvariable=self.status_text).pack(side="left")

        log_box = ttk.LabelFrame(self, text="Log")
        log_box.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(log_box, wrap="word", height=18)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_box, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Label(
            self,
            text="Mendukung format yang bisa dibaca Pillow + plugin opsional (AVIF / HEIF / HEIC).",
            foreground="#666",
        )
        footer.pack(anchor="w", padx=12, pady=(0, 8))

    def _sync_quality(self, value):
        self.quality.set(int(float(value)))

    def add_folder(self):
        folder = filedialog.askdirectory(title="Pilih folder gambar")
        if folder:
            if folder not in self.folder_list:
                self.folder_list.append(folder)
                self.listbox.insert(tk.END, folder)

    def remove_selected(self):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        for idx in reversed(selected):
            folder = self.listbox.get(idx)
            self.folder_list.remove(folder)
            self.listbox.delete(idx)

    def clear_folders(self):
        self.folder_list.clear()
        self.listbox.delete(0, tk.END)

    def choose_output_zip(self):
        path = filedialog.asksaveasfilename(
            title="Simpan ZIP hasil",
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
            initialfile="avif_output.zip",
        )
        if path:
            self.output_zip.set(path)

    def log(self, msg: str):
        self.after(0, self._append_log, msg)

    def _append_log(self, msg: str):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def set_status(self, msg: str):
        self.after(0, self.status_text.set, msg)

    def set_progress(self, current: int, total: int):
        def _apply():
            self.progress_total.set(max(total, 1))
            self.progress["maximum"] = max(total, 1)
            self.progress_value.set(current)
        self.after(0, _apply)

    def start_batch(self):
        if not self.folder_list:
            messagebox.showwarning("Belum ada folder", "Tambahkan minimal satu folder dulu.")
            return
        out_zip = self.output_zip.get().strip()
        if not out_zip:
            messagebox.showwarning("Output belum dipilih", "Pilih lokasi ZIP output dulu.")
            return

        if os.path.exists(out_zip) and not self.overwrite_zip.get():
            messagebox.showerror("ZIP sudah ada", "ZIP output sudah ada. Aktifkan overwrite atau pilih nama lain.")
            return

        self.start_btn.config(state="disabled")
        self.log_text.delete("1.0", tk.END)
        self.set_status("Memindai file...")
        self.log("Mulai proses.")
        self.log(f"Output: {out_zip}")
        self.log(f"Folder: {len(self.folder_list)}")

        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def _worker(self):
        temp_dir = tempfile.mkdtemp(prefix="avif_batch_")
        try:
            used_labels = set()
            jobs = []

            # Collect files first
            for root_folder in self.folder_list:
                root_label = unique_folder_label(root_folder, used_labels)
                for src in iter_files(root_folder, recursive=self.recursive.get()):
                    jobs.append((root_folder, root_label, src))

            total = len(jobs)
            self.set_progress(0, total)

            if total == 0:
                self.log("Tidak ada file ditemukan.")
                self.set_status("Selesai: tidak ada file.")
                return

            converted = 0
            skipped = 0
            failed = 0

            for i, (root_folder, root_label, src) in enumerate(jobs, start=1):
                self.set_progress(i - 1, total)
                rel_out = make_avif_name(src, root_folder, root_label)
                dst_path = os.path.join(temp_dir, rel_out)

                try:
                    convert_one_image(
                        src_path=src,
                        dst_path=dst_path,
                        quality=self.quality.get(),
                        lossless=self.lossless.get(),
                    )
                    converted += 1
                    self.log(f"[OK] {src} -> {rel_out}")
                except Exception as e:
                    # Non-image files will land here too
                    if "cannot identify image file" in str(e).lower():
                        skipped += 1
                        self.log(f"[SKIP] {src}")
                    else:
                        failed += 1
                        self.log(f"[ERR] {src} :: {e}")

                self.set_status(f"Memproses {i}/{total} | OK {converted} | Skip {skipped} | Err {failed}")
                self.set_progress(i, total)

            out_zip = self.output_zip.get().strip()
            if os.path.exists(out_zip) and self.overwrite_zip.get():
                os.remove(out_zip)

            self.set_status("Membuat ZIP...")
            self.log("Membuat arsip ZIP...")

            os.makedirs(os.path.dirname(out_zip) or ".", exist_ok=True)
            with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for root, _, files in os.walk(temp_dir):
                    for file in files:
                        full = os.path.join(root, file)
                        arc = os.path.relpath(full, temp_dir)
                        zf.write(full, arcname=arc)

            self.log(f"ZIP selesai: {out_zip}")
            self.log(f"Ringkasan: OK {converted}, Skip {skipped}, Error {failed}")
            self.set_status(f"Selesai. OK {converted} | Skip {skipped} | Err {failed}")
            messagebox.showinfo("Selesai", f"Konversi selesai.\n\nOK: {converted}\nSkip: {skipped}\nError: {failed}\n\nZIP:\n{out_zip}")

        except Exception as e:
            self.log(f"[FATAL] {e}")
            self.set_status("Gagal.")
            messagebox.showerror("Gagal", str(e))
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            finally:
                self.after(0, lambda: self.start_btn.config(state="normal"))


if __name__ == "__main__":
    app = AVIFBatchGUI()
    app.mainloop()