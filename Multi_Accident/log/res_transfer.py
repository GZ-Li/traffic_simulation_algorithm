import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

# python compute_roundtrip_pairs.py --input multi_res.json --output multi_accident_grouped_roundtrip.json --format accident-grouped --fill-missing 9999

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute round-trip time for each OD pair from raw OD result JSON."
    )
    parser.add_argument(
        "--input",
        default="multi_res.json",
        help="Input JSON file path (list of rows with start_road/end_road/eta_seconds).",
    )
    parser.add_argument(
        "--output",
        default="multi_roundtrip_pairs.json",
        help="Output JSON file path.",
    )
    parser.add_argument(
        "--format",
        choices=["pair-summary", "accident-grouped"],
        default="pair-summary",
        help="Output format type.",
    )
    parser.add_argument(
        "--fill-missing",
        type=int,
        default=9999,
        help="Value used when eta_seconds is missing (null).",
    )
    parser.add_argument(
        "--hospital-roads",
        default="200000480,200000154,200000033",
        help="Comma-separated hospital road ids used in accident-grouped mode.",
    )
    parser.add_argument(
        "--accident-roads",
        default="",
        help="Comma-separated accident road ids used in accident-grouped mode. Empty means auto-detect from data.",
    )
    return parser.parse_args()


def normalize_eta(value: Any, fill_missing: int) -> int:
    return fill_missing if value is None else int(value)


def compute_roundtrip(rows: List[Dict[str, Any]], fill_missing: int) -> List[Dict[str, Any]]:
    grouped: Dict[tuple, Dict[str, Any]] = {}

    for row in rows:
        start = str(row.get("start_road"))
        end = str(row.get("end_road"))
        eta = normalize_eta(row.get("eta_seconds"), fill_missing)

        a, b = sorted([start, end])
        key = (a, b)

        if key not in grouped:
            grouped[key] = {
                "road_a": a,
                "road_b": b,
                "a_to_b_seconds": fill_missing,
                "b_to_a_seconds": fill_missing,
                "roundtrip_seconds": None,
            }

        if start == a and end == b:
            grouped[key]["a_to_b_seconds"] = eta
        elif start == b and end == a:
            grouped[key]["b_to_a_seconds"] = eta

    result: List[Dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        item = grouped[key]
        item["roundtrip_seconds"] = int(item["a_to_b_seconds"]) + int(item["b_to_a_seconds"])
        result.append(item)

    return result


def parse_road_list(csv_value: str) -> List[str]:
    if not csv_value.strip():
        return []
    return [x.strip() for x in csv_value.split(",") if x.strip()]


def build_eta_lookup(rows: List[Dict[str, Any]], fill_missing: int) -> Dict[tuple, int]:
    eta_lookup: Dict[tuple, int] = {}
    for row in rows:
        start = str(row.get("start_road"))
        end = str(row.get("end_road"))
        eta_lookup[(start, end)] = normalize_eta(row.get("eta_seconds"), fill_missing)
    return eta_lookup


def compute_accident_grouped(
    rows: List[Dict[str, Any]],
    fill_missing: int,
    hospital_roads: List[str],
    accident_roads: List[str],
) -> Dict[str, Any]:
    if not hospital_roads:
        raise ValueError("hospital_roads cannot be empty in accident-grouped mode")

    if not accident_roads:
        seen_end_roads = sorted({str(row.get("end_road")) for row in rows})
        accident_roads = [r for r in seen_end_roads if r not in set(hospital_roads)]

    eta_lookup = build_eta_lookup(rows, fill_missing)
    hospitals = {f"Hospital_{idx}": int(road) for idx, road in enumerate(hospital_roads, start=1)}
    accidents = {f"Accident_{idx}": int(road) for idx, road in enumerate(accident_roads, start=1)}

    results_by_accident: Dict[str, Any] = {}
    for accident_name, accident_road_int in accidents.items():
        accident_road = str(accident_road_int)
        hospital_rows: Dict[str, Any] = {}
        for hospital_name, hospital_road_int in hospitals.items():
            hospital_road = str(hospital_road_int)
            go_seconds = eta_lookup.get((hospital_road, accident_road), fill_missing)
            return_seconds = eta_lookup.get((accident_road, hospital_road), fill_missing)
            hospital_rows[hospital_name] = {
                "hospital_road": hospital_road_int,
                "go_seconds": go_seconds,
                "return_seconds": return_seconds,
                "roundtrip_seconds": int(go_seconds) + int(return_seconds),
            }

        results_by_accident[accident_name] = {
            "accident_road": accident_road_int,
            "hospitals": hospital_rows,
        }

    return {
        "fill_value_for_missing": fill_missing,
        "hospitals": hospitals,
        "accidents": accidents,
        "results_by_accident": results_by_accident,
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open("r", encoding="utf-8") as f:
        rows = json.load(f)

    if not isinstance(rows, list):
        raise ValueError("Input JSON must be a list of OD rows.")

    if args.format == "pair-summary":
        summary = compute_roundtrip(rows, fill_missing=args.fill_missing)
        output = {
            "input_file": str(input_path),
            "fill_missing": args.fill_missing,
            "pair_count": len(summary),
            "pairs": summary,
        }
    else:
        hospital_roads = parse_road_list(args.hospital_roads)
        accident_roads = parse_road_list(args.accident_roads)
        output = compute_accident_grouped(
            rows=rows,
            fill_missing=args.fill_missing,
            hospital_roads=hospital_roads,
            accident_roads=accident_roads,
        )
        output["input_file"] = str(input_path)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved output to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
