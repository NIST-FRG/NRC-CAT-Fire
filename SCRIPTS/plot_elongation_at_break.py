"""Create four PDF scatter plots from the elongation-at-break workbook.

Requirements: Python 3, matplotlib, openpyxl
Install with: python -m pip install matplotlib openpyxl
Run with:     python plot_elongation_at_break.py
Optionally pass a workbook path as the first argument. PDFs are written beside
the workbook. The workbook is read without modification, using cached values
for any Excel formulas. Save/recalculate in Excel first if formulas change.
"""

import argparse
from collections import Counter, defaultdict
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Write PDFs without requiring a graphical desktop.
import matplotlib.pyplot as plt
from matplotlib.ticker import LogLocator, NullFormatter, StrMethodFormatter
from openpyxl import load_workbook


GROUPS = (("TS", "JACKET"), ("TS", "INSULATION"),
          ("TP", "JACKET"), ("TP", "INSULATION"))
COLORS = ("#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7",
          "#56B4E9", "#000000", "#7B61A8")
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "*")


def read_data(workbook_path):
    """Return individual measurements grouped by cable, material, temperature."""
    data = defaultdict(list)
    skipped = Counter()
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value).strip().upper() for value in next(rows)]
        required = ("CABLE TYPE", "JACKET OR INSULATION", "OVEN TEMP ( C)",
                    "DAYS IN OVEN", "ELONGATION PERCENTAGE")
        missing = set(required) - set(headers)
        if missing:
            raise ValueError(f"Missing columns: {sorted(missing)}")
        indices = [headers.index(name) for name in required]
        for row_number, row in enumerate(rows, start=2):
            if all(value is None for value in row):
                continue
            cable, material, temperature, days, elongation = (
                row[index] for index in indices
            )
            if str(temperature).strip().upper() in {"N/A", "#N/A"}:
                skipped["N/A oven temperature"] += 1
                continue
            group = (str(cable).strip().upper(), str(material).strip().upper())
            if group not in GROUPS:
                raise ValueError(f"Row {row_number}: unexpected group {group}")
            try:
                temperature, days, elongation = map(float, (temperature, days, elongation))
            except (TypeError, ValueError) as error:
                raise ValueError(f"Row {row_number}: missing or nonnumeric plot data") from error
            if not all(math.isfinite(value) for value in (temperature, days, elongation)):
                raise ValueError(f"Row {row_number}: nonfinite plot data")
            if days <= 0:
                raise ValueError(f"Row {row_number}: DAYS IN OVEN must be positive for a log axis")
            # Retain every observation, including repeated values; do not average.
            data[(*group, temperature)].append((days, elongation))
    finally:
        workbook.close()
    return data, skipped


def make_plots(workbook_path):
    data, skipped = read_data(workbook_path)
    temperatures = sorted({key[2] for key in data})
    if not temperatures:
        raise ValueError("No measurements remain after excluding N/A temperatures")
    if len(temperatures) > len(MARKERS):
        raise ValueError("Add colors and markers for the additional oven temperatures")
    styles = dict(zip(temperatures, zip(COLORS, MARKERS)))
    for cable, material in GROUPS:
        if not any(key[:2] == (cable, material) for key in data):
            raise ValueError(f"No measurements for {cable} + {material}")

    output_dir = (Path(__file__).resolve().parent / "../SCRIPT_FIGURES").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shared limits and temperature styles make the four plots comparable.
    points = [point for series in data.values() for point in series]
    x_limits = (min(x for x, _ in points) / 1.25,
                max(x for x, _ in points) * 1.25)
    y_min, y_max = 0, 500
    padding = 0
    y_limits = (min(0, y_min - padding), y_max + padding)
    plt.rcParams.update({"font.size": 11, "pdf.fonttype": 42})
    for cable, material in GROUPS:
        fig, ax = plt.subplots(figsize=(8, 5.5), layout="constrained")
        count = 0
        for temperature in temperatures:
            series = data.get((cable, material, temperature), [])
            if not series:
                continue
            color, marker = styles[temperature]
            days, elongation = zip(*series)
            ax.scatter(days, elongation, s=42, color=color, marker=marker,
                       edgecolors="black", linewidths=0.35,
                       label=f"{temperature:g}", zorder=3)
            count += len(series)
        ax.set(xscale="log", yscale="linear", xlim=x_limits, ylim=y_limits,
               xlabel="DAYS IN OVEN", ylabel="ELONGATION PERCENTAGE",
               title=f"{cable} + {material}")
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.xaxis.set_major_formatter(StrMethodFormatter("{x:g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.grid(True, which="major", color="0.88", linewidth=0.6)
        ax.set_axisbelow(True)
        ax.legend(title="OVEN TEMP (°C)", loc="upper left",
                  bbox_to_anchor=(1.02, 1), frameon=False)
        output = output_dir / f"Elongation_at_Break_{cable}_{material}.pdf"
        fig.savefig(output, format="pdf", metadata={"Title": f"{cable} + {material}"})
        plt.close(fig)
        print(f"Saved {output.name}: {count} points")
    for reason, count in skipped.items():
        print(f"Excluded {count} rows: {reason}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workbook",
        nargs="?",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "../DATA/EaB/Elongation_at_Break_Results_Short_Form.xlsx"
        ).resolve(),
    )
    args = parser.parse_args()
    make_plots(args.workbook.resolve())
