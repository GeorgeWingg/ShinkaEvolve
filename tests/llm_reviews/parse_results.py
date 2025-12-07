#!/usr/bin/env python3
"""
Parse LLM review JSON outputs and generate a summary report.

Usage:
    python parse_results.py <results_directory>

The script reads all .json files in the specified directory,
parses the review findings, and generates:
    1. summary.md - Human-readable markdown summary
    2. summary.json - Machine-readable aggregate data
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Finding:
    """A single review finding."""
    title: str
    body: str
    priority: int  # 0=P0, 1=P1, 2=P2
    confidence_score: float
    file_path: str
    line_start: int
    line_end: int
    review_id: str


@dataclass
class ReviewResult:
    """Results from a single review run."""
    review_id: str
    findings: list[Finding] = field(default_factory=list)
    overall_correctness: str = ""
    overall_explanation: str = ""
    overall_confidence: float = 0.0
    error: Optional[str] = None


def parse_review_json(json_path: Path) -> ReviewResult:
    """Parse a single review JSON file."""
    review_id = json_path.stem

    try:
        with open(json_path, 'r') as f:
            content = f.read().strip()

        if not content:
            return ReviewResult(review_id=review_id, error="Empty file")

        # Handle JSONL (multiple lines) - take last valid JSON object
        # codex review --json outputs events as JSONL
        lines = content.split('\n')
        data = None

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                # Look for ReviewOutputEvent structure
                if 'findings' in parsed:
                    data = parsed
                    break
                # Or it might be wrapped in an event
                if isinstance(parsed, dict):
                    for key in ['review_output', 'data', 'result']:
                        if key in parsed and isinstance(parsed[key], dict):
                            if 'findings' in parsed[key]:
                                data = parsed[key]
                                break
                    if data:
                        break
            except json.JSONDecodeError:
                continue

        if not data:
            return ReviewResult(review_id=review_id, error="No valid review data found")

        result = ReviewResult(
            review_id=review_id,
            overall_correctness=data.get('overall_correctness', ''),
            overall_explanation=data.get('overall_explanation', ''),
            overall_confidence=data.get('overall_confidence_score', 0.0),
        )

        for f in data.get('findings', []):
            loc = f.get('code_location', {})
            line_range = loc.get('line_range', {})

            finding = Finding(
                title=f.get('title', 'Untitled'),
                body=f.get('body', ''),
                priority=f.get('priority', 2),
                confidence_score=f.get('confidence_score', 0.0),
                file_path=str(loc.get('absolute_file_path', '')),
                line_start=line_range.get('start', 0),
                line_end=line_range.get('end', 0),
                review_id=review_id,
            )
            result.findings.append(finding)

        return result

    except Exception as e:
        return ReviewResult(review_id=review_id, error=str(e))


def priority_label(p: int) -> str:
    """Convert priority int to label."""
    return {0: 'P0', 1: 'P1', 2: 'P2'}.get(p, f'P{p}')


def generate_summary(results: list[ReviewResult], output_dir: Path):
    """Generate summary reports."""

    # Aggregate stats
    total_findings = 0
    by_priority = defaultdict(list)
    by_review = {}
    errors = []

    for result in results:
        if result.error:
            errors.append((result.review_id, result.error))
            continue

        by_review[result.review_id] = {
            'count': len(result.findings),
            'p0': sum(1 for f in result.findings if f.priority == 0),
            'p1': sum(1 for f in result.findings if f.priority == 1),
            'p2': sum(1 for f in result.findings if f.priority == 2),
        }

        for finding in result.findings:
            total_findings += 1
            by_priority[finding.priority].append(finding)

    # Generate markdown summary
    md_lines = [
        "# LLM Review Results Summary",
        "",
        f"**Total Findings**: {total_findings}",
        f"- **P0 (Critical)**: {len(by_priority[0])}",
        f"- **P1 (Important)**: {len(by_priority[1])}",
        f"- **P2 (Minor)**: {len(by_priority[2])}",
        "",
    ]

    if errors:
        md_lines.extend([
            "## Errors",
            "",
        ])
        for review_id, error in errors:
            md_lines.append(f"- **{review_id}**: {error}")
        md_lines.append("")

    md_lines.extend([
        "## Results by Review",
        "",
        "| Review | Total | P0 | P1 | P2 |",
        "|--------|-------|----|----|-----|",
    ])

    for review_id, stats in sorted(by_review.items()):
        md_lines.append(
            f"| {review_id} | {stats['count']} | {stats['p0']} | {stats['p1']} | {stats['p2']} |"
        )

    md_lines.append("")

    # P0 findings detail
    if by_priority[0]:
        md_lines.extend([
            "## P0 Findings (Critical)",
            "",
        ])
        for f in by_priority[0]:
            md_lines.extend([
                f"### [{f.review_id}] {f.title}",
                f"**Location**: `{f.file_path}:{f.line_start}-{f.line_end}`",
                "",
                f.body,
                "",
            ])

    # P1 findings detail
    if by_priority[1]:
        md_lines.extend([
            "## P1 Findings (Important)",
            "",
        ])
        for f in by_priority[1]:
            md_lines.extend([
                f"### [{f.review_id}] {f.title}",
                f"**Location**: `{f.file_path}:{f.line_start}-{f.line_end}`",
                "",
                f.body,
                "",
            ])

    # P2 summary (just titles)
    if by_priority[2]:
        md_lines.extend([
            "## P2 Findings (Minor)",
            "",
        ])
        for f in by_priority[2]:
            md_lines.append(f"- [{f.review_id}] {f.title} (`{f.file_path}:{f.line_start}`)")
        md_lines.append("")

    # Write markdown
    summary_md = output_dir / "summary.md"
    with open(summary_md, 'w') as f:
        f.write('\n'.join(md_lines))
    print(f"Summary written to: {summary_md}")

    # Write JSON summary
    summary_json = output_dir / "summary.json"
    json_data = {
        'total_findings': total_findings,
        'by_priority': {
            'p0': len(by_priority[0]),
            'p1': len(by_priority[1]),
            'p2': len(by_priority[2]),
        },
        'by_review': by_review,
        'errors': dict(errors),
        'findings': [
            {
                'review_id': f.review_id,
                'title': f.title,
                'priority': priority_label(f.priority),
                'file_path': f.file_path,
                'line_start': f.line_start,
                'line_end': f.line_end,
            }
            for p in [0, 1, 2]
            for f in by_priority[p]
        ]
    }

    with open(summary_json, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"JSON summary written to: {summary_json}")

    # Print quick summary to stdout
    print("")
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Total findings: {total_findings}")
    print(f"  P0 (Critical):  {len(by_priority[0])}")
    print(f"  P1 (Important): {len(by_priority[1])}")
    print(f"  P2 (Minor):     {len(by_priority[2])}")

    if len(by_priority[0]) > 0:
        print("")
        print("P0 ISSUES FOUND - Review required!")
        sys.exit(1)
    elif len(by_priority[1]) > 3:
        print("")
        print("Multiple P1 issues found - Review recommended")
        sys.exit(0)
    else:
        print("")
        print("No critical issues found")
        sys.exit(0)


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_results.py <results_directory>")
        sys.exit(1)

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"Error: {results_dir} is not a directory")
        sys.exit(1)

    # Find all JSON files
    json_files = list(results_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON files found in {results_dir}")
        sys.exit(1)

    print(f"Parsing {len(json_files)} review files...")

    results = []
    for json_file in json_files:
        # Skip summary files
        if json_file.stem == 'summary':
            continue
        result = parse_review_json(json_file)
        results.append(result)
        status = f"({len(result.findings)} findings)" if not result.error else f"(error: {result.error})"
        print(f"  {json_file.stem}: {status}")

    generate_summary(results, results_dir)


if __name__ == "__main__":
    main()
