import os
import csv
import hashlib
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict
from datetime import datetime

CHUNK = 8 * 1024 * 1024


def get_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(CHUNK)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def safe_destination(path):
    if not os.path.exists(path):
        return path

    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    n = 1

    while True:
        new_path = os.path.join(
            folder, f"{stem}_duplicate_{n}{ext}"
        )
        if not os.path.exists(new_path):
            return new_path
        n += 1


class Deduplicator:

    def __init__(self, root):
        self.root = root
        root.title("Exact File Deduplicator v3")
        root.geometry("700x500")

        self.folder = ""
        self.groups = []

        ttk.Label(
            root,
            text="EXACT FILE DEDUPLICATOR",
            font=("Arial", 17, "bold")
        ).pack(pady=15)

        ttk.Button(
            root,
            text="Select Folder",
            command=self.select_folder
        ).pack(pady=5)

        self.folder_label = ttk.Label(root, text="No folder selected")
        self.folder_label.pack(pady=5)

        self.scan_button = ttk.Button(
            root,
            text="SCAN FOR DUPLICATES",
            command=self.scan
        )
        self.scan_button.pack(pady=10)

        self.progress = ttk.Progressbar(
            root, length=500, mode="determinate"
        )
        self.progress.pack(pady=10)

        self.status = ttk.Label(root, text="Ready")
        self.status.pack(pady=5)

        self.output = tk.Text(root, height=13, width=80)
        self.output.pack(padx=10, pady=10)

        self.move_button = ttk.Button(
            root,
            text="MOVE DUPLICATES",
            command=self.move_duplicates,
            state="disabled"
        )
        self.move_button.pack(pady=10)

    def log(self, text):
        self.root.after(
            0,
            lambda: (
                self.output.insert("end", text + "\n"),
                self.output.see("end")
            )
        )

    def select_folder(self):
        folder = filedialog.askdirectory()

        if folder:
            self.folder = os.path.abspath(folder)
            self.folder_label.config(text=self.folder)
            self.groups = []
            self.move_button.config(state="disabled")
            self.status.config(text="Folder selected")

    def scan(self):
        if not self.folder:
            messagebox.showwarning("Warning", "Select a folder first")
            return

        self.scan_button.config(state="disabled")
        self.move_button.config(state="disabled")
        self.output.delete("1.0", "end")

        threading.Thread(
            target=self.scan_worker,
            daemon=True
        ).start()

    def scan_worker(self):

        # First pass: group files by size.
        size_groups = defaultdict(list)
        scanned = 0

        duplicates_folder = os.path.join(
            self.folder, "Duplicates"
        )

        self.log("Scanning folders...")

        for current, dirs, files in os.walk(
            self.folder, topdown=True, followlinks=False
        ):

            dirs[:] = sorted(
                [
                    d for d in dirs
                    if not os.path.islink(os.path.join(current, d))
                    and os.path.abspath(os.path.join(current, d))
                    != duplicates_folder
                ],
                key=lambda x: (x.casefold(), x)
            )

            files.sort(key=lambda x: (x.casefold(), x))

            for filename in files:

                path = os.path.join(current, filename)

                if (
                    filename.startswith("deduplicator_report_")
                    and filename.endswith(".csv")
                ):
                    continue

                try:
                    if os.path.islink(path):
                        continue

                    size = os.path.getsize(path)
                    size_groups[size].append(path)
                    scanned += 1

                except OSError as e:
                    self.log(f"SKIPPED: {path} - {e}")

        self.log(f"Files scanned: {scanned:,}")

        # Only hash files sharing a size.
        candidates = [
            path
            for paths in size_groups.values()
            if len(paths) > 1
            for path in paths
        ]

        hash_groups = defaultdict(list)

        self.log(f"Files requiring hash checks: {len(candidates):,}")

        for i, path in enumerate(candidates, 1):

            try:
                before = os.stat(path)
                digest = get_hash(path)
                after = os.stat(path)

                if (
                    before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                ):
                    self.log(f"CHANGED DURING SCAN: {path}")
                    continue

                hash_groups[(after.st_size, digest)].append(path)

            except OSError as e:
                self.log(f"ERROR: {path} - {e}")

            if i % 20 == 0:
                percent = i / len(candidates) * 100
                self.root.after(
                    0,
                    lambda p=percent: self.progress.configure(value=p)
                )
                self.root.after(
                    0,
                    lambda i=i: self.status.config(
                        text=f"Hashing {i:,}/{len(candidates):,}"
                    )
                )

        # First encountered file is kept.
        self.groups = []

        for (size, digest), paths in hash_groups.items():

            if len(paths) > 1:

                self.groups.append({
                    "keeper": paths[0],
                    "duplicates": paths[1:],
                    "size": size,
                    "hash": digest
                })

        duplicate_count = sum(
            len(g["duplicates"]) for g in self.groups
        )

        self.log("")
        self.log(f"Duplicate groups: {len(self.groups):,}")
        self.log(f"Duplicate files: {duplicate_count:,}")

        total_bytes = sum(
            g["size"] * len(g["duplicates"])
            for g in self.groups
        )

        self.log(
            f"Potential space recovered: "
            f"{total_bytes / (1024**3):.2f} GB"
        )

        self.log("")
        self.log("Sample results:")

        for group in self.groups[:10]:

            self.log(f"KEEP: {group['keeper']}")

            for duplicate in group["duplicates"][:5]:
                self.log(f"  DUPLICATE: {duplicate}")

        self.root.after(0, self.scan_complete)

    def scan_complete(self):
        self.scan_button.config(state="normal")
        self.progress.configure(value=100)
        self.status.config(text="Scan complete - nothing moved")

        if self.groups:
            self.move_button.config(state="normal")
        else:
            self.log("No exact duplicates found.")

    def move_duplicates(self):

        count = sum(
            len(g["duplicates"]) for g in self.groups
        )

        if not messagebox.askyesno(
            "Confirm",
            f"Move {count:,} duplicate files?\n\n"
            "One copy of each identical file will remain "
            "in its original location.\n\n"
            "The other copies will be moved into Duplicates."
        ):
            return

        self.move_button.config(state="disabled")
        self.scan_button.config(state="disabled")

        threading.Thread(
            target=self.move_worker,
            daemon=True
        ).start()

    def move_worker(self):

        duplicates_folder = os.path.join(
            self.folder, "Duplicates"
        )

        os.makedirs(duplicates_folder, exist_ok=True)

        report_path = os.path.join(
            self.folder,
            "deduplicator_report_" +
            datetime.now().strftime("%Y%m%d_%H%M%S") +
            ".csv"
        )

        moved = 0
        failures = 0
        rows = []

        total = sum(
            len(g["duplicates"]) for g in self.groups
        )
        processed = 0

        for group in self.groups:

            keeper = group["keeper"]
            expected_hash = group["hash"]
            size = group["size"]

            for source in group["duplicates"]:

                processed += 1

                try:
                    # Verify the file again before moving.
                    if os.path.getsize(source) != size:
                        raise ValueError("File size changed")

                    if get_hash(source) != expected_hash:
                        raise ValueError("Hash changed")

                    relative = os.path.relpath(
                        source, self.folder
                    )

                    destination = os.path.join(
                        duplicates_folder, relative
                    )

                    os.makedirs(
                        os.path.dirname(destination),
                        exist_ok=True
                    )

                    destination = safe_destination(destination)

                    # Move only after successful verification.
                    shutil.move(source, destination)

                    moved += 1

                    rows.append([
                        "MOVED",
                        source,
                        destination,
                        keeper,
                        size,
                        expected_hash
                    ])

                except Exception as e:

                    failures += 1

                    rows.append([
                        "NOT MOVED",
                        source,
                        "",
                        keeper,
                        size,
                        expected_hash
                    ])

                    self.log(f"FAILED: {source} - {e}")

                if processed % 20 == 0:
                    percent = processed / total * 100
                    self.root.after(
                        0,
                        lambda p=percent:
                        self.progress.configure(value=p)
                    )

        try:
            with open(
                report_path, "w",
                newline="", encoding="utf-8-sig"
            ) as f:

                writer = csv.writer(f)

                writer.writerow([
                    "Status",
                    "Original Path",
                    "New Path",
                    "Kept Original",
                    "Size",
                    "SHA256"
                ])

                writer.writerows(rows)

        except OSError as e:
            self.log(f"REPORT ERROR: {e}")

        self.root.after(
            0,
            lambda: self.finished(moved, failures, report_path)
        )

    def finished(self, moved, failures, report):

        self.scan_button.config(state="normal")
        self.progress.configure(value=100)
        self.status.config(text="Finished")

        self.log("")
        self.log(f"Successfully moved: {moved:,}")
        self.log(f"Failed/skipped: {failures:,}")
        self.log(f"Report: {report}")

        messagebox.showinfo(
            "Finished",
            f"Duplicates moved: {moved:,}\n"
            f"Failed/skipped: {failures:,}\n\n"
            f"Report:\n{report}"
        )


if __name__ == "__main__":

    root = tk.Tk()
    app = Deduplicator(root)
    root.mainloop()
