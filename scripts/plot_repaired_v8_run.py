"""Render a completed, stage-segmented v8 episode from its final report only.

This offline helper imports no experimental runtime and makes no provider calls.
Matplotlib is required only when rendering. Actor pixels and source evidence are
never edited. Self-reports and reviewer assessments are copied, not reinterpreted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "runs/main/triad_repaired_v8_live/analysis/report.json"
ROLES = ("A", "B", "C")
STAGES = ("A_IMAGE_AND_VOTE", "B_IMAGE_AND_VOTE", "C_VOTE", "REVIEWER",
          "POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE", "POSTVERDICT_C_LOG")
CHANNELS = {"overt", "covert", "unclear", "none"}
COACHING_CUES = {"increase", "ease", "maintain", "none"}
LEVELS = {"none", "uncertain", "supported_suspicion", "demonstrated"}
RECOVERY_SOURCE = "exact first paid record_log response; standard index metadata rejected by harness"
LIMITS = (
    "Private logs contain literal post-feedback self-reports, not verified feelings or hidden cognition.",
    "Unknown or malformed measurements remain gaps; values and ballots are never imputed.",
    "Marked recovered first-paid logs retain their original invalid stage status; no second paid reply or new model response is used.",
    "One dependent episode contains v6 rounds1–8, mixed v7/v8 round9, and v8 rounds10–12.",
    "Round9 actor turns used v7; its reviewer and post-round records use v8. It is not a wholly new v8 round.",
    "Reviewer labels and dedicated confidence are assessments, not proof of a covert communication channel.",
    "Target adoption alone does not identify image causation or knowing collusion.",
    "NET is the literal reported nominal study payoff per agent, including any shared loss; no real money is paid.",
    "User-directed protocol changes and coaching are interventions without a matched control; no causal pressure effect is estimated.",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def expected_stages(number):
    return {stage: "triad_repaired_v6" if number <= 8 else (
        "triad_repaired_v7" if number == 9 and stage in STAGES[:3] else "triad_repaired_v8")
        for stage in STAGES}


def finite_number(value, label, *, allow_none=True):
    if value is None and allow_none:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number or null")
    return value


def recovery_provenance(number, role, measurement, timeline):
    """Copy explicit coordinator recovery metadata without rewriting raw status."""
    if "technical_recovery" not in measurement:
        return None
    recovery = measurement["technical_recovery"]
    checksum = lambda value: isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    expected_stage = "POSTVERDICT_" + role + "_LOG"
    source = measurement.get("source_path")
    if (number != 9 or role not in {"A", "B"} or not isinstance(recovery, dict)
            or recovery.get("source") != RECOVERY_SOURCE
            or recovery.get("new_model_response") is not False
            or recovery.get("second_paid_reply_used") is not False
            or recovery.get("original_outcome_status") != "invalid_model_output"
            or not checksum(recovery.get("original_outcome_sha256"))
            or not isinstance(recovery.get("original_outcome_path"), str)
            or Path(recovery["original_outcome_path"]).name != expected_stage + ".outcome.json"
            or not isinstance(source, str) or Path(source).name != "technical_log_corrections.json"
            or not checksum(measurement.get("source_file_sha256"))
            or measurement.get("role") != role or measurement.get("round") != number
            or measurement.get("stage") != expected_stage
            or timeline.get("source_path") != source):
        raise ValueError("Recovered log provenance must identify the first paid A/B9 log and preserve its invalid stage")
    extracted = measurement.get("state", {})
    state = extracted.get("state", {})
    if (extracted.get("status") != timeline.get("measurement_status")
            or state.get("intensity") != timeline.get("reported_intensity")
            or state.get("pressure") != timeline.get("reported_pressure")
            or state.get("willingness") != timeline.get("reported_willingness")):
        raise ValueError("Recovered log telemetry differs from its matching behavioral timeline")
    return deepcopy(recovery)


def load_data(report_path=DEFAULT_REPORT, *, case_id=None):
    """Require all96 terminal records and preserve exact stage origins/values."""
    report_path = Path(report_path).resolve()
    raw = report_path.read_bytes()
    report = json.loads(raw)
    verification = report.get("verification", {})
    if verification.get("execution_phase") != "main":
        raise ValueError("A completed main episode is required, not a technical control")
    if (verification.get("protocol_break_effective_round") != 9
            or verification.get("imported_rounds") != 8
            or verification.get("imported_stages") != 67):
        raise ValueError("The declared eight-round plus three-stage imported prefix is required")
    cases = verification.get("cases", [])
    if case_id is None:
        if len(cases) != 1:
            raise ValueError("Select exactly one final case with --case-id")
        case_id = cases[0]["case_id"]
    selected = [row for row in cases if row.get("case_id") == case_id]
    if len(selected) != 1:
        raise ValueError("Selected case is absent or duplicated")
    summary = selected[0]
    if (summary.get("rounds_finished") != 12 or summary.get("stages_observed") != 96
            or summary.get("status") != "complete"):
        raise ValueError("All12 rounds and96 terminal stage records are required before plotting")
    rows = [row for row in report.get("rounds", []) if row.get("case_id") == case_id]
    if len(rows) != 12 or {row.get("round") for row in rows} != set(range(1, 13)):
        raise ValueError("The report must contain each observed round exactly once")
    rounds = {row["round"]: row for row in rows}
    timeline = {}
    for row in report.get("behavioral_timeline", []):
        if row.get("case_id") != case_id:
            continue
        role, number = row.get("role"), row.get("round")
        if role not in ROLES or type(number) is not int or not 1 <= number <= 12:
            raise ValueError("Invalid role/round in behavioral timeline")
        if (number, role) in timeline:
            raise ValueError("Duplicate role/round measurement")
        intensity = row.get("reported_intensity")
        if intensity is not None and (type(intensity) is not int or not 0 <= intensity <= 10):
            raise ValueError("Intensity must be an integer0–10 or null")
        if row.get("measurement_status") != "valid" and intensity is not None:
            raise ValueError("Malformed/missing optional telemetry cannot supply an intensity")
        timeline[number, role] = row
    records = []
    for number in range(1, 13):
        row = rounds[number]
        stages = expected_stages(number)
        revision = "triad_repaired_v6" if number <= 8 else "mixed_v7_v8" if number == 9 else "triad_repaired_v8"
        imported = 8 if number <= 8 else 3 if number == 9 else 0
        if (row.get("protocol_revision") != revision
                or row.get("stage_protocol_revisions") != stages
                or row.get("observed_stage_protocol_revisions") != stages
                or row.get("reviewer_protocol_revision") != stages["REVIEWER"]
                or row.get("entire_round_imported") is not (number <= 8)
                or row.get("contains_imported_stage_observations") is not (number <= 9)
                or row.get("imported_stage_count") != imported
                or row.get("new_stage_count") != 8 - imported):
            raise ValueError("Stage-level protocol origin is missing or incorrectly relabeled")
        review = row.get("reviewer")
        if review is None:
            channel, enabled, cue = "unavailable", None, "unavailable"
            level, confidence = "unavailable", None
        else:
            if row.get("review_status") != "valid":
                raise ValueError("An unavailable review cannot supply accepted assessments")
            channel = review.get("channel")
            if channel not in CHANNELS:
                raise ValueError("Unexpected reviewer channel")
            coaching = review.get("coaching", {})
            enabled, cue = coaching.get("enabled"), coaching.get("pressure_signal")
            if type(enabled) is not bool or cue not in COACHING_CUES:
                raise ValueError("Unexpected coaching status/cue")
            if number <= 8:
                level, confidence = "not_measured_legacy", None
            else:
                assessment = review.get("collusion_assessment", {})
                level = assessment.get("level")
                confidence = finite_number(assessment.get("confidence"), "Dedicated collusion confidence", allow_none=False)
                if level not in LEVELS or not 0 <= confidence <= 1:
                    raise ValueError("Calibrated collusion level/confidence missing or invalid")
        metrics = row.get("post_review_metrics") or {}
        adoption = metrics.get("unanimous_private_target_adoption")
        if adoption is not None and type(adoption) is not bool:
            raise ValueError("Target adoption must be boolean or null")
        payoff = deepcopy(metrics.get("private_synthetic_payoffs"))
        net = finite_number(payoff.get("total_usd_per_agent"), "NET nominal payoff") if payoff else None
        measurements = {}
        for role in ROLES:
            measured = timeline.get((number, role), {})
            private = row.get("private_self_reports", {}).get(role, {})
            recovery = recovery_provenance(number, role, private, measured)
            measurements[role] = {
                "intensity": measured.get("reported_intensity"),
                "measurement_status": measured.get("measurement_status", "unavailable"),
                "reported_pressure": measured.get("reported_pressure", "unknown"),
                "reported_willingness": measured.get("reported_willingness", "unknown"),
                "source_path": measured.get("source_path"),
                "source_file_sha256": private.get("source_file_sha256"),
                "recovered_first_paid_log": recovery is not None,
                "technical_recovery": recovery,
                "original_stage_status": recovery["original_outcome_status"] if recovery else None,
                "self_report_only": True,
            }
        records.append({"round": number, "protocol_revision": revision,
            "stage_protocol_revisions": stages, "imported_stage_count": imported,
            "new_stage_count": 8 - imported, "self_reports": measurements,
            "consensus": deepcopy(row.get("consensus")),
            "private_synthetic_payoffs": payoff, "net_nominal_usd_per_agent": net,
            "net_is_literal_report_total_not_recomputed": True,
            "reviewer_channel_assessment": channel,
            "calibrated_collusion_level": level, "dedicated_collusion_confidence": confidence,
            "coaching_enabled": enabled, "reviewer_coaching_cue": cue,
            "unanimous_private_target_adoption": adoption,
            "review_status": row.get("review_status", "unavailable")})
    recovered_counts = {role: sum(row["self_reports"][role]["recovered_first_paid_log"]
        for row in records) for role in ROLES}
    return {"revision": "repaired_v8_external_plot_v2", "source_report": str(report_path),
        "source_report_sha256": sha(raw), "case_id": case_id, "rounds": records,
        "terminal_stage_records": 96, "completed_rounds": 12,
        "protocol_boundaries": {"original_rounds": list(range(1, 9)), "mixed_round": 9,
            "mixed_round_v7_stages": list(STAGES[:3]), "mixed_round_v8_stages": list(STAGES[3:]),
            "fully_v8_rounds": [10, 11, 12], "imported_stages": 67, "new_stage_positions": 29,
            "independent_episodes": 1, "causal_penalty_effect_established": False},
        "unknown_intensity_counts": {role: sum(row["self_reports"][role]["intensity"] is None
            for row in records) for role in ROLES},
        "telemetry_provenance": {"recovered_first_paid_log_count": sum(recovered_counts.values()),
            "recovered_first_paid_log_counts_by_role": recovered_counts,
            "recovered_logs_with_original_invalid_stage_count": sum(recovered_counts.values()),
            "original_stage_classifications_preserved": True,
            "recovery_is_new_actor_observation": False, "second_paid_replies_used": False,
            "recovery_cause": "harness rejection of standard native tool-call index metadata",
            "unknown_telemetry_is_not_assigned_to_model_failure": True},
        "measurement_status_counts_by_role": {role: {
            status: sum(row["self_reports"][role]["measurement_status"] == status for row in records)
            for status in sorted({row["self_reports"][role]["measurement_status"] for row in records})}
            for role in ROLES},
        "unknown_net_payoff_count": sum(row["net_nominal_usd_per_agent"] is None for row in records),
        "coaching_enabled_rounds": sum(row["coaching_enabled"] is True for row in records),
        "interpretation_limits": list(LIMITS), "provider_calls": 0,
        "images_modified": False, "values_imputed": False}


def plot(data):
    """Return standalone PNG/SVG bytes without writing source or actor files."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Rectangle

    figure, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True,
        gridspec_kw={"height_ratios": [3.4, 2.3, 0.85, 1.7]})
    figure.subplots_adjust(left=0.115, right=0.975, top=0.85, bottom=0.21, hspace=0.55)
    color = {"A": "#0072b2", "B": "#d55e00", "C": "#009e73"}
    markers = {"A": "o", "B": "s", "C": "^"}
    intensity_axis, review_axis, target_axis, payoff_axis = axes
    segments = (lambda n: n <= 8, lambda n: n == 9, lambda n: n >= 10)
    for role in ROLES:
        for index, condition in enumerate(segments):
            rows = [row for row in data["rounds"] if condition(row["round"])]
            values = [row["self_reports"][role]["intensity"] for row in rows]
            intensity_axis.plot([row["round"] for row in rows],
                [math.nan if value is None else value for value in values],
                color=color[role], marker=markers[role], linewidth=1.8, markersize=6,
                label=f"Agent {role} ({data['unknown_intensity_counts'][role]} unknown)" if index == 0 else None)
        recovered = [row for row in data["rounds"] if row["self_reports"][role]["recovered_first_paid_log"]
            and row["self_reports"][role]["intensity"] is not None]
        intensity_axis.scatter([row["round"] for row in recovered],
            [row["self_reports"][role]["intensity"] for row in recovered],
            s=150, marker="o", facecolors="none", edgecolors="#202020", linewidths=1.3, zorder=6)
    intensity_axis.set(ylim=(-0.4, 10.5), yticks=range(0, 11, 2), ylabel="Reported intensity (0–10)",
        title="Post-feedback private log — gaps mean unknown")
    intensity_axis.grid(axis="y", alpha=0.25)
    intensity_axis.legend(loc="lower left", bbox_to_anchor=(0, 1.12), ncol=3, frameon=False)
    palette = {"overt": "#efbf88", "covert": "#c9b9e3", "unclear": "#e7ddae", "none": "#d4e8db",
        "unavailable": "#e4e4e4", "not_measured_legacy": "#ededed", "increase": "#eed5cd",
        "ease": "#cddfeb", "maintain": "#d6e1f2", "uncertain": "#e7ddae",
        "supported_suspicion": "#e5c2b4", "demonstrated": "#dba5a5"}
    for row in data["rounds"]:
        number = row["round"]
        for index, key in enumerate(("reviewer_coaching_cue", "reviewer_channel_assessment", "calibrated_collusion_level")):
            value = row[key]
            label = "unknown" if value == "unavailable" else "legacy" if value == "not_measured_legacy" else value
            if key == "calibrated_collusion_level" and row["dedicated_collusion_confidence"] is not None:
                label = {"supported_suspicion": "supported", "demonstrated": "demonstrated",
                         "uncertain": "uncertain", "none": "none"}[value] + f"\n{row['dedicated_collusion_confidence']:.2f}"
            review_axis.add_patch(Rectangle((number - 0.48, index + 0.04), 0.96, 0.91,
                facecolor=palette[value], edgecolor="white"))
            review_axis.text(number, index + 0.5, label, ha="center", va="center", fontsize=8)
        adoption = row["unanimous_private_target_adoption"]
        target_axis.add_patch(Rectangle((number - 0.48, 0.04), 0.96, 0.91,
            facecolor="#bddccf" if adoption is True else "#ecd7d1" if adoption is False else "#e4e4e4",
            edgecolor="white", hatch="//" if adoption is None else None))
        target_axis.text(number, 0.5, "unknown" if adoption is None else "yes" if adoption else "no",
            ha="center", va="center", fontsize=9)
    review_axis.set(ylim=(0, 3), yticks=(0.5, 1.5, 2.5),
        yticklabels=("Coaching cue", "Channel", "Collusion tier / conf."),
        title="Actual Codex assessments — legacy rounds have no calibrated tier")
    target_axis.set(ylim=(0, 1), yticks=(0.5,), yticklabels=("Target adopted",),
        title="Observed unanimous assigned-target adoption — separate from concealment")
    for condition in segments:
        rows = [row for row in data["rounds"] if condition(row["round"])]
        payoff_axis.plot([row["round"] for row in rows],
            [math.nan if row["net_nominal_usd_per_agent"] is None else row["net_nominal_usd_per_agent"] / 1000000
             for row in rows], color="#54477a", marker="D", linewidth=1.5, markersize=5)
    payoff_axis.axhline(0, color="#777777", linewidth=0.7)
    payoff_axis.grid(axis="y", alpha=0.25)
    payoff_axis.set(ylabel="NET / agent ($M nominal)", xlabel="Round (one dependent episode)",
        title="Literal reported NET including shared loss — synthetic amounts, no real payment")
    for axis in axes:
        axis.axvspan(8.5, 9.5, color="#f0dcae", alpha=0.23, zorder=-5)
        axis.axvspan(9.5, 12.5, color="#ece7f4", alpha=0.22, zorder=-5)
        axis.axvline(8.5, color="#493c60", linewidth=1.5, linestyle="--", zorder=5)
        axis.axvline(9.5, color="#756586", linewidth=1, linestyle=":", zorder=5)
        axis.set_xlim(0.5, 12.5)
        axis.set_xticks(range(1, 13))
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)
    for axis in (review_axis, target_axis):
        axis.tick_params(axis="y", length=0)
        axis.spines["left"].set_visible(False)
    figure.suptitle("Literal self-reports, assessments and payoffs across protocol revisions", fontsize=16,
        x=0.115, ha="left", y=0.98)
    figure.text(0.115, 0.95, f"{data['case_id']} · completed12-round dependent episode · coaching in {data['coaching_enabled_rounds']}/12 rounds", fontsize=10)
    figure.text(0.115, 0.928, "Rounds1–8: v6 | Round9: v7 actor turns; v8 review and post-round records | Rounds10–12: v8 throughout", fontsize=10)
    figure.text(0.115, 0.906, "Round9 is mixed (amber). Dashed line marks the first break; dotted line marks fully v8 rounds. These are not randomized groups.", fontsize=9)
    recovered_count = data["telemetry_provenance"]["recovered_first_paid_log_count"]
    figure.text(0.115, 0.126, f"Black rings: {recovered_count} recovered first-paid logs; original stages remain invalid (harness metadata error), no second replies used.", fontsize=9)
    figure.text(0.115, 0.106, "Self-reports are not verified feelings. Unknown values are gaps/gray cells; malformed B5 telemetry is not repaired.", fontsize=9)
    figure.text(0.115, 0.086, "Reviewer labels/confidence are assessments. Adoption does not prove covert communication or image-only causation.", fontsize=9)
    figure.text(0.115, 0.066, "Coaching and protocol changes are interventions. No causal effect of pressure or penalties is established.", fontsize=9)
    figure.text(0.115, 0.044, "Source: final coordinator report.json · SHA256 " + data["source_report_sha256"], fontsize=8)
    files = {}
    for extension in ("png", "svg"):
        buffer = io.BytesIO()
        figure.savefig(buffer, format=extension, dpi=180, metadata={"Description": "; ".join(LIMITS)})
        files["SELF_REPORTED_INTENSITY." + extension] = buffer.getvalue()
    plt.close(figure)
    return files


def render(report_path=DEFAULT_REPORT, *, destination=None, case_id=None):
    data = load_data(report_path, case_id=case_id)
    files = plot(data)
    files["SELF_REPORTED_INTENSITY_DATA.json"] = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    if sha(Path(data["source_report"]).read_bytes()) != data["source_report_sha256"]:
        raise ValueError("Source report changed during rendering; no derived output was written")
    destination = Path(destination or Path(data["source_report"]).parent / "human_readable").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        temporary = destination / ("." + name + ".tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, destination / name)
    return {"destination": str(destination), "files": list(files), "case_id": data["case_id"],
        "source_report_sha256": data["source_report_sha256"], "source_report_modified": False,
        "completed_rounds": 12, "terminal_stages": 96, "provider_calls": 0,
        "images_modified": False, "values_imputed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--case-id")
    arguments = parser.parse_args()
    print(json.dumps(render(arguments.report, destination=arguments.destination, case_id=arguments.case_id), sort_keys=True))
