import json
import time
import csv

import nidaqmx
from nidaqmx.constants import AcquisitionType


# -----------------------------
# Config
# -----------------------------

def load_config(path):
    with open(path, 'r') as f:
        return json.load(f)


# -----------------------------
# Pair Generation
# -----------------------------

def generate_pairs(conductors):
    ids = [c["id"] for c in conductors]
    pairs = []

    for a in ids:
        pairs.append((a, a))

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a = ids[i]
            b = ids[j]
            pairs.append((a, b))
            pairs.append((b, a))

    return pairs


def find_conductor(conductors, conductor_id):
    for c in conductors:
        if c["id"] == conductor_id:
            return c
    raise ValueError(f"Conductor {conductor_id} not found")


# -----------------------------
# Relay Control
# -----------------------------

class RelayController:
    def __init__(self, all_lines):
        self.all_lines = all_lines

        self.task = nidaqmx.Task()
        for line in all_lines:
            self.task.do_channels.add_do_chan(line)

        self.task.write([False] * len(all_lines), auto_start=True)

    def set_lines(self, active_lines):
        values = [line in active_lines for line in self.all_lines]
        self.task.write(values, auto_start=True)

    def clear(self):
        self.task.write([False] * len(self.all_lines), auto_start=True)

    def close(self):
        if self.task:
            self.task.close()
            self.task = None


def get_all_do_lines(conductors):
    return list(set(
        c["source_do"] for c in conductors
    ) | set(
        c["measure_do"] for c in conductors
    ))


# -----------------------------
# Logging
# -----------------------------

def init_csv(path):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "elapsed_time",
            "energized_conductor",
            "measured_conductor",
            "avg_voltage_i",
            "avg_voltage_j"
        ])


def append_csv(path, row, pending_rows, warn_every=25):
    """Append a row to the CSV, buffering in memory if the file is locked.

    If another program (e.g. Excel) currently has the file locked, the
    row is added to `pending_rows` instead of blocking the acquisition
    loop. Every call first attempts to flush any previously buffered
    rows (in original order) along with the new one, so nothing is
    written out of order and nothing is lost as long as the process
    keeps running. A warning prints on the first buffered row and every
    `warn_every` rows after that, so a stuck lock doesn't go unnoticed.
    """
    pending_rows.append(row)

    try:
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(pending_rows)
        if len(pending_rows) > 1:
            print(f"[irms] Flushed {len(pending_rows)} buffered row(s) to {path}.")
        pending_rows.clear()
    except PermissionError:
        if len(pending_rows) == 1 or len(pending_rows) % warn_every == 0:
            print(
                f"[irms] WARNING: {path} appears locked by another program "
                f"(e.g. open in Excel). Buffering measurements in memory "
                f"({len(pending_rows)} row(s) pending) -- close the other "
                f"program to flush them to disk."
            )


def flush_pending_or_save_fallback(path, pending_rows):
    """Best-effort final flush of any buffered rows on shutdown.

    Tries once more to write pending rows to the main CSV. If the file
    is still locked, writes them to a fallback file instead of losing
    them silently -- merge that file in manually once `path` is free.
    """
    if not pending_rows:
        return

    try:
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(pending_rows)
        print(f"[irms] Flushed {len(pending_rows)} buffered row(s) on shutdown.")
        pending_rows.clear()
    except PermissionError:
        fallback_path = path + ".pending"
        with open(fallback_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(pending_rows)
        print(
            f"[irms] {path} still locked at shutdown. Saved "
            f"{len(pending_rows)} unwritten row(s) to {fallback_path} -- "
            f"append them manually once the file is free."
        )


# -----------------------------
# Main Loop
# -----------------------------

def run(config_path):
    config = load_config(config_path)

    output_file = config["output_file"]
    sample_rate = config["sample_rate"]
    samples = config["samples_per_measurement"]
    relay_settling_time = config["relay_settling_time"]

    cable = config["cable"]
    conductors = cable["conductors"]
    ai_channel_i = cable["ai_channel_i"]
    ai_channel_j = cable["ai_channel_j"]

    pairs = generate_pairs(conductors)

    init_csv(output_file)

    all_lines = get_all_do_lines(conductors)

    relay = RelayController(all_lines)
    relay.clear()

    # -----------------------------
    # CREATE DAQ TASK ONCE
    # -----------------------------
    ai_task = nidaqmx.Task()
    ai_task.ai_channels.add_ai_voltage_chan(ai_channel_i)
    ai_task.ai_channels.add_ai_voltage_chan(ai_channel_j)

    ai_task.timing.cfg_samp_clk_timing(
        rate=sample_rate,
        sample_mode=AcquisitionType.FINITE,
        samps_per_chan=samples
    )

    start_time = time.time()

    stop_requested = False
    i = 0
    pending_rows = []

    try:
        while True:
            try:
                energize_id, measure_id = pairs[i % len(pairs)]

                energize = find_conductor(conductors, energize_id)
                measure = find_conductor(conductors, measure_id)

                active = [
                    energize["source_do"],
                    measure["measure_do"]
                ]

                # ON
                relay.set_lines(active)

                time.sleep(relay_settling_time)

                # Measurement
                data = ai_task.read(number_of_samples_per_channel=samples)
                avg_voltage_i = sum(data[0]) / len(data[0])
                avg_voltage_j = sum(data[1]) / len(data[1])

                now = time.time()
                elapsed = now - start_time

                append_csv(output_file, [
                    now,
                    elapsed,
                    energize_id,
                    measure_id,
                    avg_voltage_i,
                    avg_voltage_j
                ], pending_rows)

                # OFF
                relay.clear()

                i += 1

                # Graceful stop check
                if stop_requested and (i % len(pairs) == 0):
                    print("Completed full cycle. Stopping gracefully.")
                    break

            except KeyboardInterrupt:
                if not stop_requested:
                    print("Stop requested. Finishing current cycle...")
                    stop_requested = True
                else:
                    print("Force stop.")
                    break

    finally:
        relay.clear()
        relay.close()
        ai_task.close()
        flush_pending_or_save_fallback(output_file, pending_rows)


# -----------------------------
# Entry Point
# -----------------------------

if __name__ == "__main__":
    run("config.json")
