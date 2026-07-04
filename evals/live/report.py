"""Report generation for live eval suite."""

import json
from datetime import UTC, datetime
from pathlib import Path

from evals.live.conftest import LiveEvalReport


def generate_report(report: LiveEvalReport, output_dir: Path) -> None:
    """Generate JSON and Markdown reports for live eval run.

    Args:
        report: LiveEvalReport with aggregated results
        output_dir: Directory to write report artifacts
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).isoformat()

    # JSON report for machine consumption
    json_data = {
        "timestamp": timestamp,
        "summary": {
            "total_checks": report.total_checks,
            "passed_checks": report.passed_checks,
            "failed_checks": report.failed_checks,
            "total_cost_usd": round(report.total_cost_usd, 4),
        },
        "cost_breakdown": [
            {
                "check_name": record.check_name,
                "model": record.model,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "cost_usd": round(record.cost_usd, 6),
            }
            for record in report.cost_records
        ],
        "check_results": report.check_results,
    }

    json_path = output_dir / "live_eval_report.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2)

    # Markdown report for human consumption
    md_lines = [
        "# Live Eval Report",
        "",
        f"**Generated:** {timestamp}",
        "",
        "## Summary",
        "",
        f"- **Total Checks:** {report.total_checks}",
        f"- **Passed:** {report.passed_checks} ✅",
        f"- **Failed:** {report.failed_checks} ❌",
        f"- **Total Cost:** ${report.total_cost_usd:.4f}",
        "",
        "## Cost Breakdown",
        "",
        "| Check | Model | Input Tokens | Output Tokens | Cost (USD) |",
        "|-------|-------|--------------|---------------|------------|",
    ]

    for record in report.cost_records:
        md_lines.append(
            f"| {record.check_name} | {record.model} | {record.input_tokens} | "
            f"{record.output_tokens} | ${record.cost_usd:.6f} |"
        )

    md_lines.extend(
        [
            "",
            "## Check Results",
            "",
        ]
    )

    for check_name, result in report.check_results.items():
        status = result["status"]
        status_emoji = "✅" if status == "passed" else "❌"
        md_lines.extend(
            [
                f"### {check_name} {status_emoji}",
                "",
                f"**Status:** {status}",
                "",
            ]
        )

        # Add relevant details
        for key, value in result.items():
            if key != "status":
                md_lines.append(f"- **{key}:** {value}")

        md_lines.append("")

    md_path = output_dir / "live_eval_report.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    print("Reports generated:")
    print(f"  - JSON: {json_path}")
    print(f"  - Markdown: {md_path}")
