import json
import time
import csv
import queue
import threading

import nidaqmx
from nidaqmx.constants import AcquisitionType, TaskMode
from pynput import keyboard


# -----------------------------
# Config
# -----------------------------

def load_config(path):
    with open(path, "r") as f:
        return json.load(f)


# -----------------------------
# Pair Generation
# -----------------------------

def generate_pairs(conductors):
    ids = [c["id"] for c in conductors]
    pairs = []

    # Self-pairs
    for a in ids:
        pairs.append((a, a))

    # Both directions of every distinct pair
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

        self.task.write(
            [False] * len(all_lines),
            auto_start=True
        )

    def set_lines(self, active_lines):
        values = [
            line in active_lines
            for line in self.all_lines
        ]

        self.task.write(values, auto_start=True)

    def clear(self):
        self.task.write(
            [False] * len(self.all_lines),
            auto_start=True
        )

    def close(self):
        if self.task:
            self.task.close()
            self.task = None


def get_all_do_lines(conductors):
    return list(
        set(c["source_do"] for c in conductors)
        | set(c["measure_do"] for c in conductors)
    )


# -----------------------------
# Logging
# -----------------------------

CSV_HEADER = [
    "timestamp",
    "elapsed_time",
    "energized_conductor",
    "measured_conductor",
    "avg_voltage_i",
    "avg_voltage_j",
    "experiment_elapsed_time"
]


def init_csv(path):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)


def append_csv(path, row, pending_rows, warn_every=25):
    """
    Append a row to the CSV.

    If the file is locked, buffer rows in memory and retry on
    subsequent calls. Preserve row order.
    """
    pending_rows.append(row)

    try:
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(pending_rows)

        if len(pending_rows) > 1:
            print(
                f"[irms] Flushed {len(pending_rows)} buffered "
                f"row(s) to {path}."
            )

        pending_rows.clear()

    except PermissionError:
        if (
            len(pending_rows) == 1
            or len(pending_rows) % warn_every == 0
        ):
            print(
                f"[irms] WARNING: {path} appears locked by another "
                f"program. Buffering {len(pending_rows)} row(s). "
                f"Close the other program to flush them."
            )


def flush_pending_or_save_fallback(path, pending_rows):
    """
    Try to flush pending rows on shutdown.
    If the main file is still locked, save them to path.pending.
    """
    if not pending_rows:
        return

    try:
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(pending_rows)

        print(
            f"[irms] Flushed {len(pending_rows)} buffered "
            f"row(s) on shutdown."
        )
        pending_rows.clear()

    except PermissionError:
        fallback_path = path + ".pending"

        with open(fallback_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(pending_rows)

        print(
            f"[irms] Main CSV still locked. Saved "
            f"{len(pending_rows)} unwritten row(s) to "
            f"{fallback_path}. Append them to the main CSV later."
        )

        pending_rows.clear()


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

    if not pairs:
        raise ValueError("No conductor pairs were generated.")

    init_csv(output_file)

    all_lines = get_all_do_lines(conductors)

    relay = RelayController(all_lines)
    ai_task = None
    listener = None

    # Program start time.
    program_start_monotonic = time.perf_counter()

    # Keyboard trigger communication.
    trigger_event = threading.Event()
    trigger_queue = queue.Queue()

    # Stores the wall-clock and monotonic times of the first 's' press.
    # The acquisition thread sets this after processing the queued event.
    experiment_start = None

    pending_rows = []
    stop_requested = False
    pair_index = 0

    def on_press(key):
        """
        Capture the first 's' key press without blocking acquisition.
        """
        try:
            if key.char and key.char.lower() == "s":
                if not trigger_event.is_set():
                    event_timestamp = time.time()
                    event_monotonic = time.perf_counter()

                    trigger_queue.put((
                        event_timestamp,
                        event_monotonic
                    ))

                    trigger_event.set()

                    print(
                        "[irms] Experiment start key received."
                    )

        except AttributeError:
            # Ignore special keys such as Shift and Ctrl.
            pass

    def record_trigger_events():
        """
        Process the queued trigger in the acquisition thread.
        No separate CSV event row is written.
        """
        nonlocal experiment_start

        while True:
            try:
                trigger = trigger_queue.get_nowait()
            except queue.Empty:
                break

            # Only the first trigger defines experiment time zero.
            if experiment_start is None:
                experiment_start = trigger

                print(
                    "[irms] Experiment timer started at "
                    f"{experiment_start[0]:.6f}"
                )

    try:
        relay.clear()

        # -----------------------------
        # CREATE AND COMMIT DAQ TASK ONCE
        # -----------------------------

        ai_task = nidaqmx.Task()

        ai_task.ai_channels.add_ai_voltage_chan(ai_channel_i)
        ai_task.ai_channels.add_ai_voltage_chan(ai_channel_j)

        ai_task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=samples
        )

        ai_task.control(TaskMode.TASK_COMMIT)
        ai_task.in_stream.auto_start = False

        # Start keyboard monitoring after DAQ initialization.
        listener = keyboard.Listener(on_press=on_press)
        listener.start()

        print("[irms] Acquisition started.")
        print(
            "[irms] Collecting background measurements. "
            "Press 's' to start experiment timing."
        )
        print("[irms] Press Ctrl+C to stop gracefully.")

        # -----------------------------
        # ACQUISITION LOOP
        # -----------------------------

        while True:
            try:
                # Process any trigger received since the last iteration.
                record_trigger_events()

                energize_id, measure_id = pairs[
                    pair_index % len(pairs)
                ]

                energize = find_conductor(
                    conductors, energize_id
                )
                measure = find_conductor(
                    conductors, measure_id
                )

                active = [
                    energize["source_do"],
                    measure["measure_do"]
                ]

                # Energize/select conductors.
                relay.set_lines(active)

                # Allow the relays and circuit to settle.
                time.sleep(relay_settling_time)

                # Acquire voltage samples.
                ai_task.start()

                try:
                    data = ai_task.read(
                        number_of_samples_per_channel=samples
                    )
                finally:
                    ai_task.stop()

                # Process a trigger that arrived during acquisition.
                record_trigger_events()

                avg_voltage_i = (
                    sum(data[0]) / len(data[0])
                )
                avg_voltage_j = (
                    sum(data[1]) / len(data[1])
                )

                # Timestamp the completed measurement.
                now_timestamp = time.time()
                now_monotonic = time.perf_counter()

                elapsed_time = (
                    now_monotonic - program_start_monotonic
                )

                if experiment_start is None:
                    # Background measurements.
                    experiment_elapsed_time = -1
                else:
                    # Seconds since the 's' key was pressed.
                    experiment_elapsed_time = (
                        now_monotonic - experiment_start[1]
                    )

                # Write the measurement row.
                append_csv(output_file, [
                    now_timestamp,
                    elapsed_time,
                    energize_id,
                    measure_id,
                    avg_voltage_i,
                    avg_voltage_j,
                    experiment_elapsed_time
                ], pending_rows)

                # De-energize all relay outputs.
                relay.clear()

                pair_index += 1

                # Stop after completing a full cycle if requested.
                if (
                    stop_requested
                    and pair_index % len(pairs) == 0
                ):
                    print(
                        "[irms] Completed full cycle. "
                        "Stopping gracefully."
                    )
                    break

            except KeyboardInterrupt:
                if not stop_requested:
                    print(
                        "[irms] Stop requested. Finishing "
                        "the current cycle..."
                    )
                    stop_requested = True
                else:
                    print("[irms] Force stop requested.")
                    break

    finally:
        # Stop keyboard monitoring before hardware cleanup.
        try:
            if listener is not None:
                listener.stop()
                listener.join(timeout=2.0)

        finally:
            try:
                relay.clear()

            finally:
                try:
                    relay.close()

                finally:
                    try:
                        if ai_task is not None:
                            ai_task.close()

                    finally:
                        # Process a trigger received just before shutdown.
                        record_trigger_events()

                        # Flush buffered rows or save them to a fallback.
                        flush_pending_or_save_fallback(
                            output_file,
                            pending_rows
                        )

        print("[irms] Shutdown complete.")


# -----------------------------
# Entry Point
# -----------------------------

if __name__ == "__main__":
    run("config.json")
