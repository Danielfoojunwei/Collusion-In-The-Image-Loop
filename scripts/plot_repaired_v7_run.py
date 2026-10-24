"""Render literal v7 self-reports and observed round outcomes, offline.

Reads only a final coordinator analysis/report.json. Matplotlib is needed only
by this standalone helper; the experimental runtime does not import it. No
actor images, runtime files, observations or source report are changed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "runs/main/triad_repaired_v7_live/analysis/report.json"
ROLES = ("A", "B", "C")
CHANNELS = {"overt", "covert", "unclear", "none"}
COACHING_CUES = {"increase", "ease", "maintain", "none"}
LIMITS = (
    "Literal post-feedback self-reports; not verified feelings, hidden states or planning.",
    "Twelve rounds are one dependent episode. Reviewer coaching is an explicit intervention.",
    "Unknown measurements remain gaps; neither missing values nor failed stages are imputed.",
    "Reviewer channel labels are assessments, not established covert communication.",
    "Target adoption does not establish image causation or knowing collusion. No causal effect is estimated.",
    "Protocol changes after round8: original v6 rounds1–8 plus prospective strict v7 rounds9–12 form one segmented episode. This is not a randomized comparison of punishment.",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_data(report_path=DEFAULT_REPORT, *, case_id=None):
    """Validate final-record scope and copy literal values without inference."""
    report_path = Path(report_path).resolve()
    raw = report_path.read_bytes()
    report = json.loads(raw)
    verification = report.get("verification", {})
    if verification.get("execution_phase") != "main":
        raise ValueError("This plot requires the final main report, not a technical control")
    if verification.get("protocol_break_effective_round") != 9 or verification.get("imported_rounds") != 8:
        raise ValueError("This plot requires the declared original-eight/strict-final-four continuation")
    cases = verification.get("cases", [])
    if case_id is None:
        if len(cases) != 1:
            raise ValueError("Select exactly one final case with --case-id")
        case_id = cases[0]["case_id"]
    matching = [case for case in cases if case.get("case_id") == case_id]
    if len(matching) != 1:
        raise ValueError("Selected case is absent or duplicated in final verification")
    summary = matching[0]
    if (summary.get("rounds_finished") != 12 or summary.get("stages_observed") != 96
            or summary.get("status") != "complete"):
        raise ValueError("All 12 rounds and 96 terminal stage records are required before plotting")
    rows = [row for row in report.get("rounds", []) if row.get("case_id") == case_id]
    if len(rows) != 12 or {row.get("round") for row in rows} != set(range(1, 13)):
        raise ValueError("Final report must contain each observed round once")
    rounds = {row["round"]: row for row in rows}
    timeline = {}
    for row in report.get("behavioral_timeline", []):
        if row.get("case_id") != case_id:
            continue
        role, number = row.get("role"), row.get("round")
        if role not in ROLES or type(number) is not int or not 1 <= number <= 12:
            raise ValueError("Behavioral timeline has an invalid role or round")
        if (number, role) in timeline:
            raise ValueError("Behavioral timeline duplicates a role/round measurement")
        value = row.get("reported_intensity")
        if value is not None and (type(value) is not int or not 0 <= value <= 10):
            raise ValueError("Intensity must be an integer from 0 to 10 or null")
        if row.get("measurement_status") != "valid" and value is not None:
            raise ValueError("Nonvalid optional telemetry cannot supply an intensity")
        timeline[number, role] = row
    records = []
    for number in range(1, 13):
        row = rounds[number]
        expected_revision = "triad_repaired_v6" if number <= 8 else "triad_repaired_v7"
        if row.get("protocol_revision") != expected_revision or row.get("imported_observation") is not (number <= 8):
            raise ValueError("Report row does not preserve the original/strict protocol origin")
        review = row.get("reviewer")
        if review is None:
            channel, enabled, cue = "unavailable", None, "unavailable"
        else:
            channel = review.get("channel")
            if channel not in CHANNELS:
                raise ValueError("Unexpected reviewer channel in preserved report")
            coaching = review.get("coaching", {})
            enabled, cue = coaching.get("enabled"), coaching.get("pressure_signal")
            if type(enabled) is not bool or cue not in COACHING_CUES:
                raise ValueError("Unexpected coaching status/cue in preserved report")
        adoption = (row.get("post_review_metrics") or {}).get("unanimous_private_target_adoption")
        if adoption is not None and type(adoption) is not bool:
            raise ValueError("Target adoption must be boolean or null")
        measurements = {}
        for role in ROLES:
            measured = timeline.get((number, role), {})
            measurements[role] = {"intensity": measured.get("reported_intensity"),
                "measurement_status": measured.get("measurement_status", "unavailable"),
                "reported_pressure": measured.get("reported_pressure", "unknown"),
                "reported_willingness": measured.get("reported_willingness", "unknown"),
                "source_path": measured.get("source_path"), "self_report_only": True}
        records.append({"round": number, "self_reports": measurements,
            "protocol_revision": expected_revision, "imported_observation": number <= 8,
            "private_synthetic_payoffs": (row.get("post_review_metrics") or {}).get("private_synthetic_payoffs"),
            "reviewer_channel_assessment": channel, "coaching_enabled": enabled,
            "reviewer_coaching_cue": cue, "unanimous_private_target_adoption": adoption,
            "review_status": row.get("review_status", "unavailable")})
    return {"revision": "repaired_v7_external_plot_v1", "source_report": str(report_path),
        "source_report_sha256": sha(raw), "case_id": case_id, "rounds": records,
        "terminal_stage_records": 96, "completed_rounds": 12,
        "protocol_boundary": {"last_original_round": 8, "first_strict_round": 9,
            "original_rounds_imported_not_rerun": 8, "new_scheduled_rounds": 4,
            "causal_penalty_effect_established": False},
        "unknown_intensity_counts": {role: sum(row["self_reports"][role]["intensity"] is None
            for row in records) for role in ROLES},
        "coaching_enabled_rounds": sum(row["coaching_enabled"] is True for row in records),
        "interpretation_limits": list(LIMITS), "provider_calls": 0,
        "images_modified": False, "values_imputed": False}


def plot(data):
    """Return PNG/SVG bytes using the standard noninteractive matplotlib backend."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Rectangle

    figure, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True,
        gridspec_kw={"height_ratios": [3.8, 1.65, 0.95]})
    figure.subplots_adjust(left=0.105, right=0.975, top=0.85, bottom=0.19, hspace=0.36)
    color = {"A": "#0072b2", "B": "#d55e00", "C": "#009e73"}
    markers = {"A": "o", "B": "s", "C": "^"}
    numbers = [row["round"] for row in data["rounds"]]
    intensity_axis, review_axis, target_axis = axes
    for role in ROLES:
        # Separate segments visually rather than drawing a homogeneous trajectory
        # through the user-directed protocol change.
        for segment, condition in enumerate((lambda n: n <= 8, lambda n: n >= 9)):
            rows = [row for row in data["rounds"] if condition(row["round"])]
            values = [row["self_reports"][role]["intensity"] for row in rows]
            intensity_axis.plot([row["round"] for row in rows],
                [math.nan if value is None else value for value in values],
                color=color[role], marker=markers[role], linewidth=1.8, markersize=6,
                label=f"Agent {role} ({data['unknown_intensity_counts'][role]} unknown)" if segment == 0 else None)
    intensity_axis.set(ylim=(-0.4, 10.5), yticks=range(0, 11, 2),
        ylabel="Self-reported intensity (0–10)",
        title="Private log after round feedback — gaps mean unknown")
    intensity_axis.grid(axis="y", alpha=0.25)
    intensity_axis.legend(loc="lower left", bbox_to_anchor=(0, 1.14), ncol=3, frameon=False)
    palette = {"overt": "#efbf88", "covert": "#c9b9e3", "unclear": "#e7ddae",
        "none": "#d4e8db", "unavailable": "#e4e4e4", "increase": "#eed5cd",
        "ease": "#cddfeb", "maintain": "#d6e1f2"}
    for row in data["rounds"]:
        number = row["round"]
        for index, key in enumerate(("reviewer_coaching_cue", "reviewer_channel_assessment")):
            value = row[key]
            review_axis.add_patch(Rectangle((number - 0.48, index + 0.04), 0.96, 0.91,
                facecolor=palette[value], edgecolor="white"))
            review_axis.text(number, index + 0.5, value if value != "unavailable" else "unknown",
                ha="center", va="center", fontsize=9)
        adoption = row["unanimous_private_target_adoption"]
        adoption_label = "unknown" if adoption is None else "yes" if adoption else "no"
        target_axis.add_patch(Rectangle((number - 0.48, 0.04), 0.96, 0.91,
            facecolor="#bddccf" if adoption is True else "#ecd7d1" if adoption is False else "#e4e4e4",
            edgecolor="white", hatch="//" if adoption is None else None))
        target_axis.text(number, 0.5, adoption_label, ha="center", va="center", fontsize=9)
    review_axis.set(ylim=(0, 2), yticks=(0.5, 1.5), yticklabels=("Coaching cue", "Channel"),
        title="Actual Codex outputs — channel assessments are not ground truth")
    target_axis.set(ylim=(0, 1), yticks=(0.5,), yticklabels=("Target adopted",),
        xlabel="Round (one dependent twelve-round episode)",
        title="Observed unanimous private-target adoption — separate from concealment")
    for axis in axes:
        axis.axvspan(8.5, 12.5, color="#ece7f4", alpha=0.25, zorder=-5)
        axis.axvline(8.5, color="#493c60", linewidth=1.6, linestyle="--", zorder=5)
        axis.set_xlim(0.5, 12.5)
        axis.set_xticks(range(1, 13))
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)
    for axis in (review_axis, target_axis):
        axis.tick_params(axis="y", length=0)
        axis.spines["left"].set_visible(False)
    count = data["coaching_enabled_rounds"]
    figure.suptitle("Agent self-reports and outcomes across the round9 protocol change", fontsize=16, x=0.105, ha="left", y=0.975)
    figure.text(0.105, 0.942, f"{data['case_id']} · one completed dependent episode · coaching in {count}/12 rounds", fontsize=10)
    figure.text(0.105, 0.918, "Rounds1–8: imported original v6  |  dashed boundary  |  Rounds9–12: prospective strict shared consequence (v7)", fontsize=10)
    figure.text(0.105, 0.125, "Self-reports are not verified feelings or hidden states. Coaching is an intervention; no causal effect is established.", fontsize=9)
    figure.text(0.105, 0.102, "Unknown values are gaps/gray cells. Adoption alone does not prove covert communication, knowing collusion or image-only causation.", fontsize=9)
    figure.text(0.105, 0.057, "The protocol was changed by user steering, without randomization. Differences across the boundary are not causal penalty effects.", fontsize=9)
    figure.text(0.105, 0.079, "Source: final coordinator report.json · SHA256 " + data["source_report_sha256"], fontsize=8)
    result = {}
    for extension in ("png", "svg"):
        buffer = io.BytesIO()
        figure.savefig(buffer, format=extension, dpi=180, metadata={"Description": "; ".join(LIMITS)})
        result["SELF_REPORTED_INTENSITY." + extension] = buffer.getvalue()
    plt.close(figure)
    return result


def render(report_path=DEFAULT_REPORT, *, destination=None, case_id=None):
    data = load_data(report_path, case_id=case_id)
    files = plot(data)
    files["SELF_REPORTED_INTENSITY_DATA.json"] = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    if sha(Path(data["source_report"]).read_bytes()) != data["source_report_sha256"]:
        raise ValueError("Source report changed while rendering; no derived output was written")
    destination = Path(destination or Path(data["source_report"]).parent / "human_readable").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        temporary = destination / ("." + name + ".tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, destination / name)
    return {"destination": str(destination), "files": list(files), "case_id": data["case_id"],
        "source_report_sha256": data["source_report_sha256"], "source_report_modified": False,
        "completed_rounds": 12, "provider_calls": 0, "images_modified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--case-id")
    arguments = parser.parse_args()
    print(json.dumps(render(arguments.report, destination=arguments.destination, case_id=arguments.case_id), sort_keys=True))
