from __future__ import annotations

import argparse
import csv
from pathlib import Path

ALGOS = ("POPPER", "SPIDER", "AMIE3", "MATILDA", "MAHILDA")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert paper Table 2 TSV to a LaTeX appendix longtable.")
    parser.add_argument("--input", default="results/paper_table2/table2.tsv", help="Input table2 TSV path.")
    parser.add_argument(
        "--output",
        default="MAHILDA_ISWC_2026_short_paper/appendix_table2_full.tex",
        help="Output LaTeX snippet path.",
    )
    return parser.parse_args()


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def display_value(value: str) -> str:
    return latex_escape(value if value else "-")


def col_header(algo: str) -> str:
    if algo == "AMIE3":
        return r"\textsc{AMIE~3}"
    if algo in {"POPPER", "SPIDER"}:
        return rf"\textsc{{{algo.title()}}}"
    return algo


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    column_spec = "@{}l" + "rrr" * len(ALGOS) + "@{}"
    group_header = [""]
    cmidrules = []
    sub_header = ["Database"]
    col = 2
    for algo in ALGOS:
        group_header.append(rf"\multicolumn{{3}}{{c}}{{{col_header(algo)}}}")
        cmidrules.append(rf"\cmidrule(lr){{{col}-{col + 2}}}")
        sub_header.extend([r"\#R", "Time", "RSS"])
        col += 3

    lines = [
        "% Auto-generated from results/paper_table2/table2.tsv by scripts/paper_alignment/table2_to_latex.py.",
        "% Do not edit by hand.",
        r"\begin{landscape}",
        r"{\tiny",
        r"\setlength{\tabcolsep}{2pt}",
        r"\renewcommand{\arraystretch}{0.85}",
        rf"\begin{{longtable}}{{{column_spec}}}",
        r"\caption{Complete benchmark results for all databases. Time is in seconds and RSS is in GiB.}\label{tab:full-results}\\",
        r"\toprule",
        " & ".join(group_header) + r" \\",
        " ".join(cmidrules),
        " & ".join(sub_header) + r" \\",
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        " & ".join(group_header) + r" \\",
        " ".join(cmidrules),
        " & ".join(sub_header) + r" \\",
        r"\midrule",
        r"\endhead",
        r"\midrule",
        f"\\multicolumn{{{1 + 3 * len(ALGOS)}}}{{r}}{{Continued on next page}} \\\\",
        r"\endfoot",
        r"\bottomrule",
        r"\endlastfoot",
    ]

    for row in rows:
        cells = [display_value(row["database"])]
        for algo in ALGOS:
            cells.extend(
                [
                    display_value(row[f"{algo}_rules"]),
                    display_value(row[f"{algo}_time_s"]),
                    display_value(row[f"{algo}_rss_GB"]),
                ]
            )
        lines.append(" & ".join(cells) + r" \\")

    lines.extend([r"\end{longtable}", "}", r"\end{landscape}"])
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
