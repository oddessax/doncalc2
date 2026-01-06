import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
from typing import Any, Dict, List, Optional
from uuid import uuid4


__version__ = "0.1.2"
_UPDATE_REPO = "oddessax/doncalc2"
_UPDATE_ASSET_SUFFIX = ".exe"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, int):
            return int(value)
        s = str(value).strip()
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default


def _default_db() -> Dict[str, Any]:
    return {"version": 1, "items": []}


def _parse_version(s: str) -> List[int]:
    t = str(s or "").strip()
    if t.startswith("v") or t.startswith("V"):
        t = t[1:]
    out: List[int] = []
    cur = ""
    for ch in t:
        if ch.isdigit():
            cur += ch
        else:
            if cur:
                out.append(int(cur))
                cur = ""
    if cur:
        out.append(int(cur))
    while len(out) < 3:
        out.append(0)
    return out[:3]


def _is_newer_version(new_tag: str, current: str) -> bool:
    return _parse_version(new_tag) > _parse_version(current)


def _github_latest_release(repo: str) -> Optional[Dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"GoodsCostManager/{__version__}",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    obj = json.loads(data)
    if not isinstance(obj, dict):
        return None
    return obj


def _pick_windows_exe_asset(release_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    assets = release_obj.get("assets")
    if not isinstance(assets, list):
        return None
    best = None
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name", ""))
        if not name.lower().endswith(_UPDATE_ASSET_SUFFIX):
            continue
        if best is None:
            best = a
            continue
        if "goodscostmanager" in name.lower() and "goodscostmanager" not in str(best.get("name", "")).lower():
            best = a
    return best


def _download_file(url: str, dest_path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"GoodsCostManager/{__version__}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)


def _run_windows_self_update(*, current_exe: Path, new_exe: Path) -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="doncalc_update_"))
    bat_path = tmp_dir / "update.bat"
    old_exe = current_exe.with_suffix(current_exe.suffix + ".old")
    relaunch_args = " ".join([f'"{a}"' for a in sys.argv[1:] if a not in ("--no-update",)])

    bat = "\r\n".join(
        [
            "@echo off",
            "setlocal enableextensions",
            "title GoodsCostManager Updater",
            "timeout /t 1 /nobreak >nul",
            ":waitloop",
            f"tasklist /FI \"IMAGENAME eq {current_exe.name}\" 2>NUL | find /I \"{current_exe.name}\" >NUL",
            "if %ERRORLEVEL%==0 (timeout /t 1 /nobreak >nul & goto waitloop)",
            f"if exist \"{old_exe}\" del /f /q \"{old_exe}\"",
            f"move /y \"{current_exe}\" \"{old_exe}\" >nul",
            f"move /y \"{new_exe}\" \"{current_exe}\" >nul",
            f"start \"\" \"{current_exe}\" {relaunch_args}",
            "endlocal",
            "exit /b 0",
        ]
    )

    bat_path.write_text(bat, encoding="utf-8", errors="replace")
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        close_fds=True,
    )


def _can_auto_update() -> bool:
    if platform.system().lower() != "windows":
        return False
    if not getattr(sys, "frozen", False):
        return False
    try:
        _ = Path(sys.executable)
    except Exception:
        return False
    return True


def _normalize_db(db: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(db, dict):
        return _default_db()
    items = db.get("items")
    if not isinstance(items, list):
        items = []
    out_items = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out_items.append(_normalize_item(it))
    return {"version": int(db.get("version", 1) or 1), "items": out_items}


def _normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    item_id = it.get("id") or str(uuid4())
    name = str(it.get("name", "")).strip()
    unit_name = str(it.get("unit_name", "unit")).strip() or "unit"
    batch_size = _safe_int(it.get("batch_size", 1), 1)
    if batch_size <= 0:
        batch_size = 1

    materials = it.get("materials")
    if not isinstance(materials, list):
        materials = []
    norm_materials = []
    for m in materials:
        if not isinstance(m, dict):
            continue
        legacy_qty = _safe_float(m.get("qty", 0.0), 0.0)
        legacy_unit = str(m.get("unit", "")).strip()
        legacy_unit_cost = _safe_float(m.get("unit_cost", 0.0), 0.0)

        pack_qty = _safe_float(m.get("pack_qty", 0.0), 0.0)
        pack_unit = str(m.get("pack_unit", "")).strip()
        pack_cost = _safe_float(m.get("pack_cost", 0.0), 0.0)
        used_qty = _safe_float(m.get("used_qty", 0.0), 0.0)
        used_unit = str(m.get("used_unit", "")).strip()
        gst_mode = str(m.get("gst_mode", "ex")).strip().lower() or "ex"
        if gst_mode not in ("free", "inc", "ex"):
            gst_mode = "ex"

        if pack_qty <= 0 and ("qty" in m or "unit_cost" in m):
            pack_qty = 1.0
            pack_unit = legacy_unit
            pack_cost = legacy_unit_cost
            used_qty = legacy_qty
            used_unit = legacy_unit
        norm_materials.append(
            {
                "name": str(m.get("name", "")).strip(),
                "pack_qty": pack_qty,
                "pack_unit": pack_unit,
                "pack_cost": pack_cost,
                "used_qty": used_qty,
                "used_unit": used_unit,
                "gst_mode": gst_mode,
            }
        )

    labour = it.get("labour")
    if not isinstance(labour, dict):
        labour = {}
    mode = str(labour.get("mode", "percent")).strip() or "percent"
    if mode not in ("percent", "tasks"):
        mode = "percent"
    percent = _safe_float(labour.get("percent", 0.0), 0.0)
    tasks = labour.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    norm_tasks = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        norm_tasks.append(
            {
                "name": str(t.get("name", "")).strip(),
                "hours": _safe_float(t.get("hours", 0.0), 0.0),
                "wage_per_hour": _safe_float(t.get("wage_per_hour", 0.0), 0.0),
                "units": _safe_float(t.get("units", 0.0), 0.0),
            }
        )

    pricing = it.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    rrp_inc_gst = _safe_float(pricing.get("rrp_inc_gst", 0.0), 0.0)

    return {
        "id": str(item_id),
        "name": name,
        "unit_name": unit_name,
        "batch_size": batch_size,
        "materials": norm_materials,
        "labour": {"mode": mode, "percent": percent, "tasks": norm_tasks},
        "pricing": {"rrp_inc_gst": rrp_inc_gst},
    }


def _materials_total(item: Dict[str, Any]) -> float:
    total = 0.0
    for m in item.get("materials", []) or []:
        if not isinstance(m, dict):
            continue
        total += _material_line_cost_ex_gst(m)
    return total


def _norm_unit(u: str) -> str:
    s = str(u or "").strip().lower()
    aliases = {
        "grams": "g",
        "gram": "g",
        "g": "g",
        "kilograms": "kg",
        "kilogram": "kg",
        "kg": "kg",
        "millilitres": "ml",
        "milliliters": "ml",
        "millilitre": "ml",
        "milliliter": "ml",
        "ml": "ml",
        "litres": "l",
        "liters": "l",
        "litre": "l",
        "liter": "l",
        "l": "l",
        "ea": "each",
        "each": "each",
        "pc": "each",
        "pcs": "each",
    }
    return aliases.get(s, s)


def _unit_to_base(u: str) -> Optional[tuple[str, float]]:
    u = _norm_unit(u)
    if u == "g":
        return ("mass", 1.0)
    if u == "kg":
        return ("mass", 1000.0)
    if u == "ml":
        return ("volume", 1.0)
    if u == "l":
        return ("volume", 1000.0)
    return None


def _convert_qty(qty: float, from_unit: str, to_unit: str) -> Optional[float]:
    fu = _unit_to_base(from_unit)
    tu = _unit_to_base(to_unit)
    if fu is None or tu is None:
        return None
    if fu[0] != tu[0]:
        return None
    base = qty * fu[1]
    return base / tu[1]


def _pack_cost_ex_gst(m: Dict[str, Any]) -> float:
    cost = _safe_float(m.get("pack_cost", 0.0), 0.0)
    mode = str(m.get("gst_mode", "ex")).strip().lower() or "ex"
    if mode == "inc":
        return cost / 1.1 if cost > 0 else 0.0
    return cost


def _material_line_cost_ex_gst(m: Dict[str, Any]) -> float:
    pack_qty = _safe_float(m.get("pack_qty", 0.0), 0.0)
    used_qty = _safe_float(m.get("used_qty", 0.0), 0.0)
    if pack_qty <= 0:
        return 0.0

    pack_unit = str(m.get("pack_unit", "")).strip()
    used_unit = str(m.get("used_unit", "")).strip()
    pack_cost_ex = _pack_cost_ex_gst(m)

    pack_unit_n = _norm_unit(pack_unit)
    used_unit_n = _norm_unit(used_unit)

    if pack_unit_n and used_unit_n and pack_unit_n == used_unit_n:
        ratio = used_qty / pack_qty
        return max(0.0, pack_cost_ex * ratio)

    conv_used = _convert_qty(used_qty, used_unit_n, pack_unit_n)
    if conv_used is not None:
        ratio = conv_used / pack_qty
        return max(0.0, pack_cost_ex * ratio)

    ratio = used_qty / pack_qty
    return max(0.0, pack_cost_ex * ratio)


def _materials_per_unit(item: Dict[str, Any]) -> float:
    batch = _safe_int(item.get("batch_size", 1), 1)
    if batch <= 0:
        batch = 1
    return _materials_total(item) / float(batch)


def _labour_per_unit(item: Dict[str, Any]) -> float:
    labour = item.get("labour", {}) or {}
    mode = labour.get("mode", "percent")
    if mode == "tasks":
        total = 0.0
        for t in labour.get("tasks", []) or []:
            if not isinstance(t, dict):
                continue
            hours = _safe_float(t.get("hours", 0.0), 0.0)
            wage = _safe_float(t.get("wage_per_hour", 0.0), 0.0)
            units = _safe_float(t.get("units", 0.0), 0.0)
            if units > 0:
                total += (hours * wage) / units
        return total
    percent = _safe_float(labour.get("percent", 0.0), 0.0)
    return _materials_per_unit(item) * (percent / 100.0)


def _total_per_unit(item: Dict[str, Any]) -> float:
    return _materials_per_unit(item) + _labour_per_unit(item)


def _rrp_inc_gst(item: Dict[str, Any]) -> float:
    pricing = item.get("pricing", {}) or {}
    return _safe_float(pricing.get("rrp_inc_gst", 0.0), 0.0)


def _rrp_ex_gst(item: Dict[str, Any]) -> float:
    inc = _rrp_inc_gst(item)
    return inc / 1.1 if inc > 0 else 0.0


def _gst_amount(item: Dict[str, Any]) -> float:
    inc = _rrp_inc_gst(item)
    ex = _rrp_ex_gst(item)
    return max(0.0, inc - ex)


def _gross_profit_per_unit(item: Dict[str, Any]) -> float:
    return _rrp_ex_gst(item) - _materials_per_unit(item)


def _gross_margin(item: Dict[str, Any]) -> float:
    rev = _rrp_ex_gst(item)
    if rev <= 0:
        return 0.0
    return _gross_profit_per_unit(item) / rev


def _net_profit_per_unit(item: Dict[str, Any]) -> float:
    return _rrp_ex_gst(item) - _total_per_unit(item)


def _net_margin(item: Dict[str, Any]) -> float:
    rev = _rrp_ex_gst(item)
    if rev <= 0:
        return 0.0
    return _net_profit_per_unit(item) / rev


def _format_money(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".")


class LineDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, title: str):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self._grabbed = False
        self.result: Optional[Dict[str, Any]] = None
        self._body = ttk.Frame(self, padding=12)
        self._body.grid(row=0, column=0, sticky="nsew")
        self._buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        self._buttons.grid(row=1, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)

        self.after(0, self._try_grab)

    def _try_grab(self):
        if self._grabbed:
            return
        try:
            self.wait_visibility()
            self.grab_set()
            self._grabbed = True
        except tk.TclError:
            self.after(25, self._try_grab)

    def close(self):
        try:
            if self._grabbed:
                self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class MaterialDialog(LineDialog):
    def __init__(self, parent: tk.Widget, initial: Optional[Dict[str, Any]] = None):
        super().__init__(parent, "Material")
        self.var_name = tk.StringVar(value=(initial or {}).get("name", ""))
        self.var_pack_qty = tk.StringVar(value=str((initial or {}).get("pack_qty", 0.0)))
        self.var_pack_unit = tk.StringVar(value=(initial or {}).get("pack_unit", ""))
        self.var_pack_cost = tk.StringVar(value=str((initial or {}).get("pack_cost", 0.0)))
        self.var_used_qty = tk.StringVar(value=str((initial or {}).get("used_qty", 0.0)))
        self.var_used_unit = tk.StringVar(value=(initial or {}).get("used_unit", ""))
        self.var_gst_mode = tk.StringVar(value=str((initial or {}).get("gst_mode", "ex")) or "ex")
        if self.var_gst_mode.get() not in ("free", "inc", "ex"):
            self.var_gst_mode.set("ex")
        self.var_preview = tk.StringVar(value="")

        ttk.Label(self._body, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(self._body, textvariable=self.var_name, width=40).grid(row=1, column=0, sticky="ew")

        pack = ttk.LabelFrame(self._body, text="Pack / purchase", padding=10)
        pack.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        pack.columnconfigure(0, weight=1)
        pack.columnconfigure(1, weight=1)
        pack.columnconfigure(2, weight=1)

        ttk.Label(pack, text="Pack qty").grid(row=0, column=0, sticky="w")
        ttk.Entry(pack, textvariable=self.var_pack_qty, width=12).grid(row=1, column=0, sticky="ew")
        ttk.Label(pack, text="Pack unit").grid(row=0, column=1, sticky="w", padx=(10, 0))
        units = ["g", "kg", "ml", "l", "each"]
        pack_unit_cb = ttk.Combobox(pack, textvariable=self.var_pack_unit, values=units, width=10, state="readonly")
        pack_unit_cb.grid(row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Label(pack, text="Pack cost").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Entry(pack, textvariable=self.var_pack_cost, width=12).grid(row=1, column=2, sticky="ew", padx=(10, 0))

        gst_row = ttk.Frame(pack)
        gst_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(gst_row, text="GST mode").pack(side="left")
        gst_combo = ttk.Combobox(gst_row, textvariable=self.var_gst_mode, values=["ex", "inc", "free"], width=8, state="readonly")
        gst_combo.pack(side="left", padx=(10, 0))
        ttk.Label(gst_row, text="ex=exclusive, inc=inclusive, free=GST-free").pack(side="left", padx=(10, 0))

        used = ttk.LabelFrame(self._body, text="Used in this product", padding=10)
        used.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        used.columnconfigure(0, weight=1)
        used.columnconfigure(1, weight=1)

        ttk.Label(used, text="Used qty").grid(row=0, column=0, sticky="w")
        ttk.Entry(used, textvariable=self.var_used_qty, width=12).grid(row=1, column=0, sticky="ew")
        ttk.Label(used, text="Used unit").grid(row=0, column=1, sticky="w", padx=(10, 0))
        used_unit_cb = ttk.Combobox(used, textvariable=self.var_used_unit, values=units, width=10, state="readonly")
        used_unit_cb.grid(row=1, column=1, sticky="w", padx=(10, 0))

        ttk.Label(self._body, textvariable=self.var_preview).grid(row=4, column=0, sticky="w", pady=(10, 0))

        ttk.Button(self._buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(self._buttons, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._ok())
        self.after(50, self._focus_first)

        self.var_pack_qty.trace_add("write", lambda *a: self._refresh_preview())
        self.var_pack_unit.trace_add("write", lambda *a: self._refresh_preview())
        self.var_pack_cost.trace_add("write", lambda *a: self._refresh_preview())
        self.var_used_qty.trace_add("write", lambda *a: self._refresh_preview())
        self.var_used_unit.trace_add("write", lambda *a: self._refresh_preview())
        self.var_gst_mode.trace_add("write", lambda *a: self._refresh_preview())
        self.after(60, self._refresh_preview)

    def _focus_first(self):
        for child in self._body.winfo_children():
            if isinstance(child, ttk.Entry):
                child.focus_set()
                child.selection_range(0, tk.END)
                break

    def _cancel(self):
        self.result = None
        self.close()

    def _ok(self):
        name = self.var_name.get().strip()
        pack_qty = _safe_float(self.var_pack_qty.get(), 0.0)
        pack_unit = self.var_pack_unit.get().strip()
        pack_cost = _safe_float(self.var_pack_cost.get(), 0.0)
        used_qty = _safe_float(self.var_used_qty.get(), 0.0)
        used_unit = self.var_used_unit.get().strip()
        gst_mode = (self.var_gst_mode.get() or "ex").strip().lower()
        if gst_mode not in ("free", "inc", "ex"):
            gst_mode = "ex"
        self.result = {
            "name": name,
            "pack_qty": pack_qty,
            "pack_unit": pack_unit,
            "pack_cost": pack_cost,
            "used_qty": used_qty,
            "used_unit": used_unit,
            "gst_mode": gst_mode,
        }
        self.close()

    def _refresh_preview(self):
        m = {
            "pack_qty": _safe_float(self.var_pack_qty.get(), 0.0),
            "pack_unit": self.var_pack_unit.get().strip(),
            "pack_cost": _safe_float(self.var_pack_cost.get(), 0.0),
            "used_qty": _safe_float(self.var_used_qty.get(), 0.0),
            "used_unit": self.var_used_unit.get().strip(),
            "gst_mode": (self.var_gst_mode.get() or "ex").strip().lower(),
        }
        line = _material_line_cost_ex_gst(m)
        pack_ex = _pack_cost_ex_gst(m)
        self.var_preview.set(f"Line cost (ex GST): {_format_money(line)} | Pack cost ex GST: {_format_money(pack_ex)}")


class TaskDialog(LineDialog):
    def __init__(self, parent: tk.Widget, initial: Optional[Dict[str, Any]] = None):
        super().__init__(parent, "Labour task")
        self.var_name = tk.StringVar(value=(initial or {}).get("name", ""))
        self.var_hours = tk.StringVar(value=str((initial or {}).get("hours", 0.0)))
        self.var_wage = tk.StringVar(value=str((initial or {}).get("wage_per_hour", 0.0)))
        self.var_units = tk.StringVar(value=str((initial or {}).get("units", 0.0)))

        ttk.Label(self._body, text="Task name").grid(row=0, column=0, sticky="w")
        ttk.Entry(self._body, textvariable=self.var_name, width=44).grid(row=1, column=0, sticky="ew")

        grid = ttk.Frame(self._body)
        grid.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)

        ttk.Label(grid, text="Hours").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.var_hours, width=12).grid(row=1, column=0, sticky="ew")
        ttk.Label(grid, text="Wage / hour").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Entry(grid, textvariable=self.var_wage, width=12).grid(row=1, column=1, sticky="ew", padx=(10, 0))
        ttk.Label(grid, text="Units done").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Entry(grid, textvariable=self.var_units, width=12).grid(row=1, column=2, sticky="ew", padx=(10, 0))

        ttk.Button(self._buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(self._buttons, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._ok())
        self.after(50, self._focus_first)

    def _focus_first(self):
        for child in self._body.winfo_children():
            if isinstance(child, ttk.Entry):
                child.focus_set()
                child.selection_range(0, tk.END)
                break

    def _cancel(self):
        self.result = None
        self.close()

    def _ok(self):
        name = self.var_name.get().strip()
        hours = _safe_float(self.var_hours.get(), 0.0)
        wage = _safe_float(self.var_wage.get(), 0.0)
        units = _safe_float(self.var_units.get(), 0.0)
        self.result = {"name": name, "hours": hours, "wage_per_hour": wage, "units": units}
        self.close()


class ItemEditor(tk.Toplevel):
    def __init__(self, parent: tk.Widget, item: Dict[str, Any]):
        super().__init__(parent)
        self.title("Item")
        self.minsize(760, 540)
        self.transient(parent.winfo_toplevel())
        self._grabbed = False
        self.after(0, self._try_grab)

        self._original = _normalize_item(item)
        self.item = json.loads(json.dumps(self._original))

        self.result: Optional[Dict[str, Any]] = None

        self.var_name = tk.StringVar(value=self.item.get("name", ""))
        self.var_unit_name = tk.StringVar(value=self.item.get("unit_name", "unit"))
        self.var_batch_size = tk.StringVar(value=str(self.item.get("batch_size", 1)))

        labour = self.item.get("labour", {})
        self.var_labour_mode = tk.StringVar(value=labour.get("mode", "percent"))
        self.var_labour_percent = tk.StringVar(value=str(labour.get("percent", 0.0)))

        pricing = self.item.get("pricing", {})
        self.var_rrp_inc_gst = tk.StringVar(value=str((pricing or {}).get("rrp_inc_gst", 0.0)))

        self.summary_var = tk.StringVar(value="")
        self.pricing_summary_var = tk.StringVar(value="")

        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        basics = ttk.LabelFrame(header, text="Basics", padding=10)
        basics.grid(row=0, column=0, sticky="ew")
        basics.columnconfigure(0, weight=1)

        ttk.Label(basics, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(basics, textvariable=self.var_name).grid(row=1, column=0, sticky="ew")

        row2 = ttk.Frame(basics)
        row2.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=1)

        ttk.Label(row2, text="Unit name").grid(row=0, column=0, sticky="w")
        unit_options = ["unit", "each", "g", "kg", "ml", "l"]
        ttk.Combobox(row2, textvariable=self.var_unit_name, values=unit_options, state="readonly").grid(
            row=1, column=0, sticky="ew"
        )
        ttk.Label(row2, text="Batch size (# units)").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Entry(row2, textvariable=self.var_batch_size).grid(row=1, column=1, sticky="ew", padx=(10, 0))

        self.nb = ttk.Notebook(root)
        self.nb.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        root.rowconfigure(1, weight=1)

        self.materials_tab = ttk.Frame(self.nb, padding=10)
        self.labour_tab = ttk.Frame(self.nb, padding=10)
        self.pricing_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.materials_tab, text="Materials")
        self.nb.add(self.labour_tab, text="Labour")
        self.nb.add(self.pricing_tab, text="Pricing")

        self._build_materials_tab()
        self._build_labour_tab()
        self._build_pricing_tab()

        footer = ttk.Frame(root)
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)

        ttk.Label(footer, textvariable=self.summary_var, anchor="w").grid(row=0, column=0, sticky="ew")

        btns = ttk.Frame(footer)
        btns.grid(row=0, column=1, sticky="e")
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Control-Return>", lambda e: self._save())

        self.var_name.trace_add("write", lambda *a: self._refresh_summary())
        self.var_unit_name.trace_add("write", lambda *a: self._refresh_summary())
        self.var_batch_size.trace_add("write", lambda *a: self._refresh_summary())
        self.var_labour_mode.trace_add("write", lambda *a: self._on_labour_mode_changed())
        self.var_labour_percent.trace_add("write", lambda *a: self._refresh_summary())
        self.var_rrp_inc_gst.trace_add("write", lambda *a: self._refresh_summary())

        self.after(50, self._refresh_summary)

    def _try_grab(self):
        if self._grabbed:
            return
        try:
            self.wait_visibility()
            self.grab_set()
            self._grabbed = True
        except tk.TclError:
            self.after(25, self._try_grab)

    def _build_materials_tab(self):
        self.materials_tab.rowconfigure(0, weight=1)
        self.materials_tab.columnconfigure(0, weight=1)

        self.materials_tree = ttk.Treeview(
            self.materials_tab,
            columns=("name", "used", "pack", "pack_cost", "gst", "line_cost"),
            show="headings",
            height=10,
        )
        for col, label, w in [
            ("name", "Name", 260),
            ("used", "Used", 140),
            ("pack", "Pack", 140),
            ("pack_cost", "Pack cost", 110),
            ("gst", "GST", 70),
            ("line_cost", "Line cost (ex)", 120),
        ]:
            self.materials_tree.heading(col, text=label)
            self.materials_tree.column(col, width=w, anchor="w")
        self.materials_tree.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(self.materials_tab, orient="vertical", command=self.materials_tree.yview)
        self.materials_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(self.materials_tab)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Add", command=self._add_material).pack(side="left")
        ttk.Button(actions, text="Edit", command=self._edit_material).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Remove", command=self._remove_material).pack(side="left", padx=(8, 0))

        self.materials_tree.bind("<Double-1>", lambda e: self._edit_material())
        self._refresh_materials_tree()

    def _build_labour_tab(self):
        self.labour_tab.rowconfigure(2, weight=1)
        self.labour_tab.columnconfigure(0, weight=1)

        mode_frame = ttk.LabelFrame(self.labour_tab, text="Labour mode", padding=10)
        mode_frame.grid(row=0, column=0, sticky="ew")

        ttk.Radiobutton(
            mode_frame,
            text="A: Percent of materials (flat %)",
            variable=self.var_labour_mode,
            value="percent",
        ).grid(row=0, column=0, sticky="w")

        percent_row = ttk.Frame(mode_frame)
        percent_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(percent_row, text="Percent").pack(side="left")
        self.percent_entry = ttk.Entry(percent_row, textvariable=self.var_labour_percent, width=10)
        self.percent_entry.pack(side="left", padx=(8, 0))
        ttk.Label(percent_row, text="% of materials per unit").pack(side="left", padx=(8, 0))

        ttk.Separator(self.labour_tab, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=10)

        tasks_frame = ttk.LabelFrame(self.labour_tab, text="B: Tasks (normalized labour per unit)", padding=10)
        tasks_frame.grid(row=2, column=0, sticky="nsew")
        tasks_frame.rowconfigure(0, weight=1)
        tasks_frame.columnconfigure(0, weight=1)

        ttk.Radiobutton(
            tasks_frame,
            text="Enable tasks mode", 
            variable=self.var_labour_mode,
            value="tasks",
        ).grid(row=0, column=0, sticky="w")

        tree_host = ttk.Frame(tasks_frame)
        tree_host.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        tree_host.rowconfigure(0, weight=1)
        tree_host.columnconfigure(0, weight=1)

        self.tasks_tree = ttk.Treeview(
            tree_host,
            columns=("name", "hours", "wage", "units", "cost_per_unit"),
            show="headings",
            height=8,
        )
        for col, label, w in [
            ("name", "Task", 260),
            ("hours", "Hours", 90),
            ("wage", "Wage/hr", 100),
            ("units", "Units", 90),
            ("cost_per_unit", "Cost/unit", 120),
        ]:
            self.tasks_tree.heading(col, text=label)
            self.tasks_tree.column(col, width=w, anchor="w")
        self.tasks_tree.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(tree_host, orient="vertical", command=self.tasks_tree.yview)
        self.tasks_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        task_actions = ttk.Frame(tasks_frame)
        task_actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(task_actions, text="Add task", command=self._add_task).pack(side="left")
        ttk.Button(task_actions, text="Edit task", command=self._edit_task).pack(side="left", padx=(8, 0))
        ttk.Button(task_actions, text="Remove task", command=self._remove_task).pack(side="left", padx=(8, 0))

        self.tasks_tree.bind("<Double-1>", lambda e: self._edit_task())
        self._refresh_tasks_tree()
        self._on_labour_mode_changed()

    def _build_pricing_tab(self):
        self.pricing_tab.columnconfigure(0, weight=1)

        price_frame = ttk.LabelFrame(self.pricing_tab, text="RRP", padding=10)
        price_frame.grid(row=0, column=0, sticky="ew")
        price_frame.columnconfigure(1, weight=1)

        ttk.Label(price_frame, text="RRP (inc GST)").grid(row=0, column=0, sticky="w")
        ttk.Entry(price_frame, textvariable=self.var_rrp_inc_gst, width=16).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(price_frame, text="GST assumed: 10% (AU)").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        summary = ttk.LabelFrame(self.pricing_tab, text="Viability (per unit)", padding=10)
        summary.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        summary.columnconfigure(0, weight=1)

        ttk.Label(summary, textvariable=self.pricing_summary_var, anchor="w", justify="left").grid(row=0, column=0, sticky="ew")

    def _refresh_materials_tree(self):
        for i in self.materials_tree.get_children():
            self.materials_tree.delete(i)
        for idx, m in enumerate(self.item.get("materials", []) or []):
            used_qty = _safe_float(m.get("used_qty", 0.0), 0.0)
            used_unit = str(m.get("used_unit", "")).strip()
            pack_qty = _safe_float(m.get("pack_qty", 0.0), 0.0)
            pack_unit = str(m.get("pack_unit", "")).strip()
            pack_cost = _safe_float(m.get("pack_cost", 0.0), 0.0)
            gst_mode = str(m.get("gst_mode", "ex")).strip().lower() or "ex"
            line_cost = _material_line_cost_ex_gst(m)
            self.materials_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    m.get("name", ""),
                    f"{used_qty} {used_unit}".strip(),
                    f"{pack_qty} {pack_unit}".strip(),
                    _format_money(pack_cost),
                    gst_mode,
                    _format_money(line_cost),
                ),
            )

    def _selected_material_index(self) -> Optional[int]:
        sel = self.materials_tree.selection()
        if not sel:
            return None
        return _safe_int(sel[0], -1)

    def _add_material(self):
        dlg = MaterialDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self.item.setdefault("materials", []).append(dlg.result)
        self._refresh_materials_tree()
        self._refresh_summary()

    def _edit_material(self):
        idx = self._selected_material_index()
        if idx is None:
            return
        mats = self.item.get("materials", []) or []
        if idx < 0 or idx >= len(mats):
            return
        dlg = MaterialDialog(self, initial=mats[idx])
        self.wait_window(dlg)
        if dlg.result is None:
            return
        mats[idx] = dlg.result
        self._refresh_materials_tree()
        self._refresh_summary()

    def _remove_material(self):
        idx = self._selected_material_index()
        if idx is None:
            return
        mats = self.item.get("materials", []) or []
        if idx < 0 or idx >= len(mats):
            return
        del mats[idx]
        self._refresh_materials_tree()
        self._refresh_summary()

    def _refresh_tasks_tree(self):
        for i in self.tasks_tree.get_children():
            self.tasks_tree.delete(i)
        labour = self.item.get("labour", {}) or {}
        tasks = labour.get("tasks", []) or []
        for idx, t in enumerate(tasks):
            hours = _safe_float(t.get("hours", 0.0), 0.0)
            wage = _safe_float(t.get("wage_per_hour", 0.0), 0.0)
            units = _safe_float(t.get("units", 0.0), 0.0)
            cpu = (hours * wage) / units if units > 0 else 0.0
            self.tasks_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    t.get("name", ""),
                    str(hours),
                    str(wage),
                    str(units),
                    _format_money(cpu),
                ),
            )

    def _selected_task_index(self) -> Optional[int]:
        sel = self.tasks_tree.selection()
        if not sel:
            return None
        return _safe_int(sel[0], -1)

    def _add_task(self):
        labour = self.item.setdefault("labour", {"mode": "percent", "percent": 0.0, "tasks": []})
        labour.setdefault("tasks", [])
        dlg = TaskDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        labour["tasks"].append(dlg.result)
        self._refresh_tasks_tree()
        self._refresh_summary()

    def _edit_task(self):
        idx = self._selected_task_index()
        if idx is None:
            return
        labour = self.item.get("labour", {}) or {}
        tasks = labour.get("tasks", []) or []
        if idx < 0 or idx >= len(tasks):
            return
        dlg = TaskDialog(self, initial=tasks[idx])
        self.wait_window(dlg)
        if dlg.result is None:
            return
        tasks[idx] = dlg.result
        self._refresh_tasks_tree()
        self._refresh_summary()

    def _remove_task(self):
        idx = self._selected_task_index()
        if idx is None:
            return
        labour = self.item.get("labour", {}) or {}
        tasks = labour.get("tasks", []) or []
        if idx < 0 or idx >= len(tasks):
            return
        del tasks[idx]
        self._refresh_tasks_tree()
        self._refresh_summary()

    def _on_labour_mode_changed(self):
        mode = self.var_labour_mode.get()
        if mode == "tasks":
            self.percent_entry.configure(state="disabled")
        else:
            self.percent_entry.configure(state="normal")
        self.item.setdefault("labour", {"mode": "percent", "percent": 0.0, "tasks": []})
        self.item["labour"]["mode"] = mode
        self._refresh_summary()

    def _refresh_summary(self):
        self.item["name"] = self.var_name.get().strip()
        self.item["unit_name"] = self.var_unit_name.get().strip() or "unit"
        self.item["batch_size"] = max(1, _safe_int(self.var_batch_size.get(), 1))
        self.item.setdefault("labour", {"mode": "percent", "percent": 0.0, "tasks": []})
        self.item["labour"]["mode"] = self.var_labour_mode.get()
        self.item["labour"]["percent"] = _safe_float(self.var_labour_percent.get(), 0.0)
        self.item.setdefault("pricing", {"rrp_inc_gst": 0.0})
        self.item["pricing"]["rrp_inc_gst"] = _safe_float(self.var_rrp_inc_gst.get(), 0.0)

        mats_total = _materials_total(self.item)
        mats_unit = _materials_per_unit(self.item)
        lab_unit = _labour_per_unit(self.item)
        total_unit = _total_per_unit(self.item)
        unit_name = self.item.get("unit_name", "unit")

        rrp_inc = _rrp_inc_gst(self.item)
        rrp_ex = _rrp_ex_gst(self.item)
        gst = _gst_amount(self.item)
        gross_profit = _gross_profit_per_unit(self.item)
        gross_margin = _gross_margin(self.item)
        net_profit = _net_profit_per_unit(self.item)
        net_margin = _net_margin(self.item)

        self.summary_var.set(
            f"Materials total (ex GST): { _format_money(mats_total) } | Materials/{unit_name} (ex): { _format_money(mats_unit) } | Labour/{unit_name}: { _format_money(lab_unit) } | Total/{unit_name}: { _format_money(total_unit) } | RRP inc GST: { _format_money(rrp_inc) }"
        )

        self.pricing_summary_var.set(
            "\n".join(
                [
                    f"RRP inc GST: { _format_money(rrp_inc) }",
                    f"RRP ex GST: { _format_money(rrp_ex) } | GST: { _format_money(gst) }",
                    f"Gross profit: { _format_money(gross_profit) } | Gross margin: { _format_money(gross_margin * 100.0) }%",
                    f"Net profit: { _format_money(net_profit) } | Profit margin: { _format_money(net_margin * 100.0) }%",
                ]
            )
        )
        self._refresh_tasks_tree()

    def _cancel(self):
        self.result = None
        try:
            if self._grabbed:
                self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _save(self):
        self._refresh_summary()
        self.result = _normalize_item(self.item)
        try:
            if self._grabbed:
                self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.master = master
        self.master.title("Goods Cost Manager")
        self.master.minsize(980, 560)

        base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        self.db_path = base_dir / "goods_data.json"
        self.db: Dict[str, Any] = _default_db()
        self._dirty = False
        self._details_item_for_graph: Optional[Dict[str, Any]] = None

        self._update_thread: Optional[threading.Thread] = None
        self._update_ui: Optional[tk.Toplevel] = None

        self.grid(row=0, column=0, sticky="nsew")
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)

        self._build_menu()
        self._build_layout()
        self._load_db()
        self._refresh_items_tree()

        self.after(800, self._start_update_check)

    def _start_update_check(self):
        if "--no-update" in sys.argv:
            return
        if not _can_auto_update():
            return
        if self._update_thread and self._update_thread.is_alive():
            return

        self._update_thread = threading.Thread(target=self._update_check_worker, daemon=True)
        self._update_thread.start()

    def _update_check_worker(self):
        try:
            rel = _github_latest_release(_UPDATE_REPO)
            if not rel:
                return
            tag = str(rel.get("tag_name", "")).strip()
            if not tag:
                return
            if not _is_newer_version(tag, __version__):
                return
            asset = _pick_windows_exe_asset(rel)
            if not asset:
                return
            url = str(asset.get("browser_download_url", "")).strip()
            if not url:
                return

            self.master.after(0, lambda: self._prompt_and_update(tag, url))
        except Exception:
            return

    def _prompt_and_update(self, tag: str, url: str):
        try:
            if not messagebox.askyesno(
                "Update available",
                f"A new version ({tag}) is available.\n\nUpdate now?",
                parent=self.master,
            ):
                return
        except Exception:
            return

        current_exe = Path(sys.executable)
        if not os.access(str(current_exe.parent), os.W_OK):
            messagebox.showwarning(
                "Update not possible",
                "The app does not have permission to update itself in this folder.\n\n"
                "Move the EXE to a writable folder (like Desktop) and try again.",
                parent=self.master,
            )
            return

        self._show_update_ui("Downloading update…")

        t = threading.Thread(target=self._download_and_swap_worker, args=(url,), daemon=True)
        t.start()

    def _show_update_ui(self, text: str):
        if self._update_ui and self._update_ui.winfo_exists():
            try:
                self._update_status_var.set(text)
            except Exception:
                pass
            return

        win = tk.Toplevel(self.master)
        win.title("Updating")
        win.resizable(False, False)
        win.transient(self.master)
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        frm = ttk.Frame(win, padding=14)
        frm.grid(row=0, column=0, sticky="nsew")

        self._update_status_var = tk.StringVar(value=text)
        ttk.Label(frm, textvariable=self._update_status_var).grid(row=0, column=0, sticky="w")
        pb = ttk.Progressbar(frm, mode="indeterminate", length=280)
        pb.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        pb.start(12)

        win.update_idletasks()
        try:
            x = self.master.winfo_rootx() + (self.master.winfo_width() // 2) - (win.winfo_width() // 2)
            y = self.master.winfo_rooty() + (self.master.winfo_height() // 2) - (win.winfo_height() // 2)
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self._update_ui = win

    def _close_update_ui(self):
        if self._update_ui and self._update_ui.winfo_exists():
            try:
                self._update_ui.destroy()
            except Exception:
                pass
        self._update_ui = None

    def _download_and_swap_worker(self, url: str):
        current_exe = Path(sys.executable)
        tmp_dir = Path(tempfile.mkdtemp(prefix="doncalc_download_"))
        new_exe = tmp_dir / (current_exe.stem + ".new.exe")
        try:
            self.master.after(0, lambda: self._update_status_var.set("Downloading update…"))
            _download_file(url, new_exe)

            if not new_exe.exists() or new_exe.stat().st_size < 1024:
                raise RuntimeError("Downloaded file is invalid")

            self.master.after(0, lambda: self._update_status_var.set("Installing update…"))
            _run_windows_self_update(current_exe=current_exe, new_exe=new_exe)
            self.master.after(0, self.master.destroy)
        except Exception:
            self.master.after(0, self._close_update_ui)
            self.master.after(
                0,
                lambda: messagebox.showerror(
                    "Update failed",
                    "The update could not be installed. You can try again later.",
                    parent=self.master,
                ),
            )

    def _build_menu(self):
        m = tk.Menu(self.master)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="Open JSON...", command=self.open_json)
        filem.add_command(label="Save", command=self.save_json)
        filem.add_command(label="Save As...", command=self.save_as_json)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.master.destroy)
        m.add_cascade(label="File", menu=filem)
        self.master.config(menu=m)

    def _build_layout(self):
        outer = ttk.Frame(self)
        outer.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=2)
        outer.columnconfigure(1, weight=3)

        left = ttk.LabelFrame(outer, text="Items", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.items_tree = ttk.Treeview(
            left,
            columns=("name", "batch", "unit", "materials_unit", "labour_unit", "total_unit", "rrp_inc", "profit_unit", "margin"),
            show="headings",
        )
        for col, label, w in [
            ("name", "Name", 220),
            ("batch", "Batch", 80),
            ("unit", "Unit", 110),
            ("materials_unit", "Materials/unit", 120),
            ("labour_unit", "Labour/unit", 120),
            ("total_unit", "Total/unit", 120),
            ("rrp_inc", "RRP inc GST", 120),
            ("profit_unit", "Profit/unit", 110),
            ("margin", "Profit %", 90),
        ]:
            self.items_tree.heading(col, text=label)
            self.items_tree.column(col, width=w, anchor="w")
        self.items_tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        left_actions = ttk.Frame(left)
        left_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(left_actions, text="New", command=self.new_item).pack(side="left")
        ttk.Button(left_actions, text="Edit", command=self.edit_selected_item).pack(side="left", padx=(8, 0))
        ttk.Button(left_actions, text="Delete", command=self.delete_selected_item).pack(side="left", padx=(8, 0))
        ttk.Button(left_actions, text="Save", command=self.save_json).pack(side="right")

        right = ttk.LabelFrame(outer, text="Details", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.details_title = tk.StringVar(value="Select an item")
        ttk.Label(right, textvariable=self.details_title, font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        self.details_text = tk.Text(right, height=10, wrap="word")
        self.details_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.details_text.configure(state="disabled")

        self.details_canvas = tk.Canvas(right, height=150, highlightthickness=0)
        self.details_canvas.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.details_canvas.bind("<Configure>", lambda e: self._draw_breakdown(self._details_item_for_graph))

        bottom = ttk.Frame(self)
        bottom.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")

        self.items_tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_details())
        self.items_tree.bind("<Double-1>", lambda e: self.edit_selected_item())

    def _load_db(self):
        self._load_on_startup()

    def _load_on_startup(self):
        if self.db_path.exists():
            try:
                self.db = self._load_db_from_path(self.db_path)
            except Exception:
                self.db = _default_db()
        else:
            self.db = _default_db()
            try:
                self._save_db_to_path(self.db_path)
            except Exception:
                pass
        self._dirty = False
        self._refresh_items_tree()
        self._refresh_details()
        self._set_status(f"Loaded {len(self.db.get('items', []))} items from {self.db_path.name}")

    def _load_db_from_path(self, path: Path) -> Dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_db(data)

    def _save_db_to_path(self, path: Path):
        path.write_text(json.dumps(self.db, indent=2, ensure_ascii=False), encoding="utf-8")

    def _mark_dirty(self, dirty: bool = True):
        self._dirty = dirty
        title = "Goods Cost Manager"
        if dirty:
            title += " *"
        self.master.title(title)

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def open_json(self):
        p = filedialog.askopenfilename(
            title="Open JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*")],
        )
        if not p:
            return
        path = Path(p)
        try:
            self.db = self._load_db_from_path(path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return
        self.db_path = path
        self._mark_dirty(False)
        self._refresh_items_tree()
        self._refresh_details()
        self._set_status(f"Opened {path}")

    def save_json(self):
        try:
            self._save_db_to_path(self.db_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self._mark_dirty(False)
        self._set_status(f"Saved to {self.db_path}")

    def save_as_json(self):
        p = filedialog.asksaveasfilename(
            title="Save As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if not p:
            return
        path = Path(p)
        self.db_path = path
        self.save_json()

    def _items(self) -> List[Dict[str, Any]]:
        items = self.db.get("items")
        if not isinstance(items, list):
            self.db["items"] = []
            return self.db["items"]
        return items

    def _refresh_items_tree(self):
        for i in self.items_tree.get_children():
            self.items_tree.delete(i)
        for it in self._items():
            itn = _normalize_item(it)
            unit_name = itn.get("unit_name", "unit")
            mats_unit = _materials_per_unit(itn)
            lab_unit = _labour_per_unit(itn)
            tot_unit = _total_per_unit(itn)
            rrp_inc = _rrp_inc_gst(itn)
            profit_unit = _net_profit_per_unit(itn)
            margin = _net_margin(itn) * 100.0
            self.items_tree.insert(
                "",
                "end",
                iid=itn["id"],
                values=(
                    itn.get("name", ""),
                    str(itn.get("batch_size", 1)),
                    unit_name,
                    _format_money(mats_unit),
                    _format_money(lab_unit),
                    _format_money(tot_unit),
                    _format_money(rrp_inc),
                    _format_money(profit_unit),
                    f"{_format_money(margin)}%",
                ),
            )

    def _selected_item_id(self) -> Optional[str]:
        sel = self.items_tree.selection()
        if not sel:
            return None
        return sel[0]

    def _find_item_by_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        for it in self._items():
            if str(it.get("id")) == str(item_id):
                return it
        return None

    def new_item(self):
        it = _normalize_item({"id": str(uuid4()), "name": "", "unit_name": "unit", "batch_size": 1})
        dlg = ItemEditor(self.master, it)
        self.master.wait_window(dlg)
        if dlg.result is None:
            return
        self._items().append(dlg.result)
        self._mark_dirty(True)
        self._refresh_items_tree()
        self._select_item(dlg.result["id"])
        self._refresh_details()

    def edit_selected_item(self):
        item_id = self._selected_item_id()
        if not item_id:
            return
        it = self._find_item_by_id(item_id)
        if it is None:
            return
        dlg = ItemEditor(self.master, it)
        self.master.wait_window(dlg)
        if dlg.result is None:
            return
        it.clear()
        it.update(dlg.result)
        self._mark_dirty(True)
        self._refresh_items_tree()
        self._select_item(item_id)
        self._refresh_details()

    def delete_selected_item(self):
        item_id = self._selected_item_id()
        if not item_id:
            return
        it = self._find_item_by_id(item_id)
        if it is None:
            return
        name = str(it.get("name", ""))
        if not messagebox.askyesno("Delete", f"Delete item '{name}'?"):
            return
        self.db["items"] = [x for x in self._items() if str(x.get("id")) != str(item_id)]
        self._mark_dirty(True)
        self._refresh_items_tree()
        self._refresh_details()

    def _select_item(self, item_id: str):
        try:
            self.items_tree.selection_set(item_id)
            self.items_tree.focus(item_id)
            self.items_tree.see(item_id)
        except Exception:
            pass

    def _refresh_details(self):
        item_id = self._selected_item_id()
        if not item_id:
            self.details_title.set("Select an item")
            self._set_details_text("")
            self._details_item_for_graph = None
            self._draw_breakdown(None)
            return
        it = self._find_item_by_id(item_id)
        if it is None:
            self.details_title.set("Select an item")
            self._set_details_text("")
            self._details_item_for_graph = None
            self._draw_breakdown(None)
            return

        itn = _normalize_item(it)
        self.details_title.set(itn.get("name", "(unnamed item)") or "(unnamed item)")

        unit_name = itn.get("unit_name", "unit")
        batch = itn.get("batch_size", 1)
        mats_total = _materials_total(itn)
        mats_unit = _materials_per_unit(itn)
        lab_unit = _labour_per_unit(itn)
        tot_unit = _total_per_unit(itn)
        tot_batch = tot_unit * float(batch)

        rrp_inc = _rrp_inc_gst(itn)
        rrp_ex = _rrp_ex_gst(itn)
        gst = _gst_amount(itn)
        gross_profit = _gross_profit_per_unit(itn)
        gross_margin = _gross_margin(itn)
        net_profit = _net_profit_per_unit(itn)
        net_margin = _net_margin(itn)

        labour = itn.get("labour", {}) or {}
        mode = labour.get("mode", "percent")
        if mode == "tasks":
            labour_desc = f"B (tasks): {len(labour.get('tasks', []) or [])} tasks"
        else:
            labour_desc = f"A (percent): { _safe_float(labour.get('percent', 0.0), 0.0) }%"

        lines = []
        lines.append(f"Unit: {unit_name}")
        lines.append(f"Batch size: {batch}")
        lines.append("")
        lines.append(f"Materials total (batch, ex GST): { _format_money(mats_total) }")
        lines.append(f"Materials per {unit_name} (ex GST): { _format_money(mats_unit) }")
        lines.append(f"Labour mode: {labour_desc}")
        lines.append(f"Labour per {unit_name}: { _format_money(lab_unit) }")
        lines.append("")
        lines.append(f"Total per {unit_name}: { _format_money(tot_unit) }")
        lines.append(f"Total per batch: { _format_money(tot_batch) }")
        lines.append("")

        lines.append(f"RRP inc GST: { _format_money(rrp_inc) }")
        lines.append(f"GST (10%): { _format_money(gst) } | RRP ex GST: { _format_money(rrp_ex) }")
        lines.append(f"Gross profit: { _format_money(gross_profit) } | Gross margin: { _format_money(gross_margin * 100.0) }%")
        lines.append(f"Net profit: { _format_money(net_profit) } | Profit margin: { _format_money(net_margin * 100.0) }%")
        if rrp_ex > 0 and net_profit < 0:
            lines.append(f"WARNING: Loss per {unit_name}: { _format_money(-net_profit) }")

        mats = itn.get("materials", []) or []
        if mats:
            lines.append("Materials:")
            for m in mats:
                used_qty = _safe_float(m.get("used_qty", 0.0), 0.0)
                used_unit = str(m.get("used_unit", "")).strip()
                pack_qty = _safe_float(m.get("pack_qty", 0.0), 0.0)
                pack_unit = str(m.get("pack_unit", "")).strip()
                pack_cost = _safe_float(m.get("pack_cost", 0.0), 0.0)
                gst_mode = str(m.get("gst_mode", "ex")).strip().lower() or "ex"
                line_cost_ex = _material_line_cost_ex_gst(m)
                pack_cost_ex = _pack_cost_ex_gst(m)

                used_str = f"{used_qty} {used_unit}".strip()
                pack_str = f"{pack_qty} {pack_unit}".strip()
                lines.append(
                    f"- {m.get('name','')}: used {used_str} of {pack_str} @ { _format_money(pack_cost) } ({gst_mode}) => { _format_money(line_cost_ex) } ex GST"
                )
                if gst_mode == "inc":
                    lines.append(f"  pack cost ex GST: { _format_money(pack_cost_ex) }")
        tasks = (itn.get("labour", {}) or {}).get("tasks", []) or []
        if tasks:
            lines.append("")
            lines.append("Labour tasks:")
            for t in tasks:
                hours = _safe_float(t.get("hours", 0.0), 0.0)
                wage = _safe_float(t.get("wage_per_hour", 0.0), 0.0)
                units = _safe_float(t.get("units", 0.0), 0.0)
                cpu = (hours * wage) / units if units > 0 else 0.0
                lines.append(f"- {t.get('name','')}: ({hours}h x {wage}) / {units} = { _format_money(cpu) } per {unit_name}")

        self._set_details_text("\n".join(lines))
        self._details_item_for_graph = itn
        self._draw_breakdown(itn)

    def _draw_breakdown(self, item: Optional[Dict[str, Any]]):
        c = self.details_canvas
        c.delete("all")
        w = c.winfo_width() or 1
        h = c.winfo_height() or 1
        pad_x = 14
        pad_y = 10
        x0 = pad_x
        x1 = w - pad_x
        y0 = pad_y
        y1 = h - pad_y

        if item is None:
            return

        rrp_inc = _rrp_inc_gst(item)
        if rrp_inc <= 0:
            c.create_text(x0, y0, anchor="nw", text="Set RRP inc GST to see breakdown")
            return

        gst = _gst_amount(item)
        revenue_ex = rrp_inc - gst
        mats = _materials_per_unit(item)
        lab = _labour_per_unit(item)
        costs = max(0.0, mats + lab)
        profit = revenue_ex - costs

        def _text_color(bg_hex: str) -> str:
            s = str(bg_hex or "").strip()
            if not s.startswith("#") or len(s) != 7:
                return "#111827"
            try:
                r = int(s[1:3], 16)
                g = int(s[3:5], 16)
                b = int(s[5:7], 16)
            except Exception:
                return "#111827"
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return "#111827" if lum > 160 else "#F9FAFB"

        def clamp01(x: float) -> float:
            if x < 0:
                return 0.0
            if x > 1:
                return 1.0
            return x

        def draw_stacked_bar(
            *,
            x: int,
            y: int,
            width: int,
            height: int,
            total: float,
            segments: List[Dict[str, Any]],
            label: str,
        ):
            c.create_text(x, y - 14, anchor="nw", text=label)
            if total <= 0:
                c.create_rectangle(x, y, x + width, y + height, outline="#111827")
                return

            cur_x = x
            for seg in segments:
                amt = float(seg.get("amount", 0.0) or 0.0)
                color = seg.get("color", "#9CA3AF")
                show = seg.get("show", True)
                if not show:
                    continue
                seg_w = int(round(width * (amt / total)))
                if seg_w <= 0:
                    continue
                c.create_rectangle(cur_x, y, cur_x + seg_w, y + height, fill=color, outline="")
                label_txt = str(seg.get("label", ""))
                if label_txt:
                    pct = clamp01(amt / total) * 100.0
                    txt = f"{label_txt} {_format_money(amt)} ({_format_money(pct)}%)"
                    tcol = _text_color(color)
                    if seg_w >= 140:
                        c.create_text(cur_x + seg_w // 2, y + height // 2, anchor="c", text=txt, fill=tcol)
                    elif seg_w >= 80:
                        c.create_text(
                            cur_x + seg_w // 2,
                            y + height // 2,
                            anchor="c",
                            text=f"{label_txt} ({_format_money(pct)}%)",
                            fill=tcol,
                        )
                cur_x += seg_w
            c.create_rectangle(x, y, x + width, y + height, outline="#111827")

        title_h = 16
        subtitle_h = 16
        gap = 10

        c.create_text(x0, y0, anchor="nw", text="Price viability")
        c.create_text(
            x0,
            y0 + title_h,
            anchor="nw",
            text=f"RRP inc GST: {_format_money(rrp_inc)} | RRP ex GST: {_format_money(revenue_ex)} | GST: {_format_money(gst)}",
        )

        bar_w = max(1, x1 - x0)
        available_h = (y1 - y0) - (title_h + subtitle_h + gap * 4)
        bar_h = max(18, int(min(44, available_h / 3)))

        by0 = y0 + title_h + subtitle_h + gap * 2
        draw_stacked_bar(
            x=x0,
            y=by0,
            width=bar_w,
            height=bar_h,
            total=rrp_inc,
            segments=[
                {"label": "GST", "amount": max(0.0, gst), "color": "#9CA3AF"},
                {"label": "Materials", "amount": max(0.0, mats), "color": "#60A5FA"},
                {"label": "Labour", "amount": max(0.0, lab), "color": "#F59E0B"},
                {"label": "Profit", "amount": max(0.0, revenue_ex - max(0.0, mats + lab)), "color": "#34D399"},
            ],
            label="Scaled to RRP inc GST",
        )

        by1 = by0 + bar_h + gap * 2
        profit_color = "#34D399" if profit >= 0 else "#EF4444"
        draw_stacked_bar(
            x=x0,
            y=by1,
            width=bar_w,
            height=bar_h,
            total=max(0.0, revenue_ex),
            segments=[
                {"label": "Materials", "amount": max(0.0, mats), "color": "#60A5FA"},
                {"label": "Labour", "amount": max(0.0, lab), "color": "#F59E0B"},
                {"label": "Profit", "amount": max(0.0, profit), "color": profit_color},
            ],
            label="Scaled to RRP ex GST (revenue)",
        )

        if profit < 0 and revenue_ex > 0:
            loss_abs = -profit
            loss_w = int(round(bar_w * (loss_abs / revenue_ex)))
            loss_w = max(6, min(loss_w, bar_w))
            c.create_rectangle(x0 + bar_w - loss_w, by1, x0 + bar_w, by1 + bar_h, outline="#DC2626", width=2)
            c.create_text(
                x0 + bar_w - loss_w + 4,
                by1 + 2,
                anchor="nw",
                text=f"Loss {_format_money(loss_abs)}",
                fill="#DC2626",
            )

        legend_y = by1 + bar_h + gap
        lx = x0
        ly = legend_y
        for label, color in [
            ("GST", "#9CA3AF"),
            ("Materials", "#60A5FA"),
            ("Labour", "#F59E0B"),
            ("Profit", profit_color),
        ]:
            item_w = 18 + max(30, len(label) * 7) + 18
            if lx + item_w > x1:
                lx = x0
                ly += 16
            c.create_rectangle(lx, ly, lx + 12, ly + 12, fill=color, outline="")
            c.create_text(lx + 18, ly + 6, anchor="w", text=label)
            lx += item_w

        if profit < 0:
            c.create_text(
                x0,
                ly + 18,
                anchor="nw",
                text=f"Loss per unit (ex GST): {_format_money(-profit)}",
                fill="#DC2626",
            )

    def _set_details_text(self, s: str):
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert("1.0", s)
        self.details_text.configure(state="disabled")


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
