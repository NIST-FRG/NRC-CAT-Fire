"""
Live GUI monitor for the cable insulation test rig.

Reads the CSV log produced by the acquisition script (results.csv, as defined
in config.json) while it is being written, and displays a grid of plots:
one subplot per conductor (as the *energized* conductor), each containing
n-1 lines -- one per other conductor -- showing that conductor's
measured-side voltage (V_j) over elapsed time.

A sustained rise on a single line means insulation is breaking down between
the energized conductor for that subplot and the specific neighbor that
line represents.

Usage:
    python cable_monitor_gui.py [config.json]

Run this alongside (or after starting) the main acquisition script. It only
reads the CSV/config; it never touches the DAQ hardware.
"""

import csv
import json
import math
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


# -----------------------------
# Config
# -----------------------------

def load_config(path):
    with open(path, "r") as f:
        return json.load(f)


# -----------------------------
# Live Monitor
# -----------------------------

class LiveCableMonitor:
    def __init__(self, config_path="config.json", poll_interval_ms=500, max_points=None):
        config = load_config(config_path)

        self.csv_path = config["output_file"]
        conductors = config["cable"]["conductors"]
        self.conductor_ids = [c["id"] for c in conductors]
        self.n = len(self.conductor_ids)
        self.max_points = max_points  # cap history length per line; None = unbounded

        # data[energized_id][measured_id] = ([x...], [y...])
        self.data = {
            e: {m: ([], []) for m in self.conductor_ids if m != e}
            for e in self.conductor_ids
        }

        self._file_pos = 0
        self._header_skipped = False
        self._locked = False

        self.fig, self.axes, self.lines_by_pair = self._build_figure()

        self.ani = FuncAnimation(
            self.fig,
            self._update,
            interval=poll_interval_ms,
            cache_frame_data=False,
        )

    # ---- figure setup ----

    def _grid_shape(self):
        cols = math.ceil(math.sqrt(self.n))
        rows = math.ceil(self.n / cols)
        return rows, cols

    def _build_figure(self):
        rows, cols = self._grid_shape()
        fig, axes = plt.subplots(rows, cols, squeeze=False, figsize=(4 * cols, 3 * rows))
        fig.suptitle("Cable Insulation Monitor \u2014 V_j by energized conductor")

        lines_by_pair = {}  # (energized_id, measured_id) -> Line2D

        for idx, energized_id in enumerate(self.conductor_ids):
            r, c = divmod(idx, cols)
            ax = axes[r][c]
            ax.set_title(f"Energized: {energized_id}")
            ax.set_xlabel("Elapsed time (s)")
            ax.set_ylabel("V_j (measured side)")

            for measured_id in self.conductor_ids:
                if measured_id == energized_id:
                    continue
                (line,) = ax.plot([], [], label=f"Meas {measured_id}")
                lines_by_pair[(energized_id, measured_id)] = line

            ax.legend(fontsize="small", loc="upper left")

        # Hide unused grid cells if n doesn't fill the grid exactly
        total_cells = rows * cols
        for idx in range(self.n, total_cells):
            r, c = divmod(idx, cols)
            axes[r][c].axis("off")

        fig.tight_layout(rect=[0, 0, 1, 0.96])
        return fig, axes, lines_by_pair

    # ---- CSV tailing ----

    def _read_new_rows(self):
        """Return any CSV rows appended since the last poll.

        Handles the file not existing yet, being truncated/recreated
        (e.g. the acquisition script was restarted), and being briefly
        locked by another program (e.g. opening results.csv in Excel),
        by skipping the poll and trying again next tick rather than
        crashing the GUI.
        """
        if not os.path.exists(self.csv_path):
            return []

        try:
            size = os.path.getsize(self.csv_path)
        except OSError:
            return []

        if size < self._file_pos:
            self._file_pos = 0
            self._header_skipped = False
            for e in self.data:
                for m in self.data[e]:
                    self.data[e][m] = ([], [])

        new_rows = []
        try:
            with open(self.csv_path, "r", newline="") as f:
                f.seek(self._file_pos)
                reader = csv.reader(f)

                for row in reader:
                    if not row:
                        continue
                    if not self._header_skipped:
                        self._header_skipped = True
                        continue
                    new_rows.append(row)

                self._file_pos = f.tell()
        except PermissionError:
            # Another program (e.g. Excel) currently holds an exclusive
            # lock on the file. Don't advance self._file_pos -- once the
            # file is readable again we'll pick up any rows we missed.
            if not self._locked:
                print(f"[monitor] {self.csv_path} is locked by another "
                      f"program (e.g. open in Excel) -- retrying...")
                self._locked = True
            return []
        except OSError:
            return []

        if self._locked:
            print(f"[monitor] {self.csv_path} is readable again, resuming.")
            self._locked = False

        return new_rows

    # ---- animation callback ----

    def _update(self, frame):
        rows = self._read_new_rows()
        if not rows:
            return []

        changed_pairs = set()

        for row in rows:
            try:
                _timestamp, elapsed, energized_id, measured_id, _avg_v_i, avg_v_j = row
                elapsed = float(elapsed)
                energized_id = int(energized_id)
                measured_id = int(measured_id)
                avg_v_j = float(avg_v_j)
            except (ValueError, IndexError):
                continue  # skip malformed/partial rows

            if energized_id == measured_id:
                continue  # self-pair, not part of the n-1 lines

            pair = (energized_id, measured_id)
            if pair not in self.lines_by_pair:
                continue  # unknown conductor id (config/CSV mismatch), ignore

            xs, ys = self.data[energized_id][measured_id]
            xs.append(elapsed)
            ys.append(avg_v_j)

            if self.max_points is not None and len(xs) > self.max_points:
                del xs[: len(xs) - self.max_points]
                del ys[: len(ys) - self.max_points]

            changed_pairs.add(pair)

        updated_artists = []
        touched_axes = set()

        for pair in changed_pairs:
            energized_id, measured_id = pair
            xs, ys = self.data[energized_id][measured_id]
            line = self.lines_by_pair[pair]
            line.set_data(xs, ys)
            updated_artists.append(line)
            touched_axes.add(line.axes)

        for ax in touched_axes:
            ax.relim()
            ax.autoscale_view()

        return updated_artists

    def show(self):
        plt.show()


# -----------------------------
# Entry Point
# -----------------------------

if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    monitor = LiveCableMonitor(config_path=config_path, poll_interval_ms=500)
    monitor.show()
