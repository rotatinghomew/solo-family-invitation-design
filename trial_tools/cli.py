"""命令行入口：把试验设计、跟踪、分析与入组判断串成可反复办理的流程。

用法示例：
  python -m trial_tools.cli audit data/records.json
  python -m trial_tools.cli households data/records.json
  python -m trial_tools.cli plan data/records.json --seed 7 --ledger run/ledger.jsonl
  python -m trial_tools.cli status data/records.json --look 24
  python -m trial_tools.cli analyze data/records.json --look 24 --comparisons 4
  python -m trial_tools.cli randomization-check data/records.json --look 24
  python -m trial_tools.cli trace run/ledger.jsonl --entry E0001
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analysis import ESTIMANDS, FactorialAnalysis
from .design import CELLS, DesignEngine
from .households import HouseholdResolver
from .ledger import DecisionLedger
from .monitor import Monitor
from .outcomes import OutcomeBuilder
from .records import load_records


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def _model_to_json(model) -> dict[str, Any]:
    return {
        "outcome": model.outcome,
        "n_obs": model.n_obs,
        "n_classes": model.n_classes,
        "n_households": model.n_households,
        "variance_method": model.variance_method,
        "excluded_tainted": list(model.excluded_tainted),
        "coefficients_b0_bS_bA_bSA": list(model.coefficients),
        "cell_rates": {f"{s},{a}": v for (s, a), v in model.cell_rates.items()},
        "contrasts": [
            {
                "estimand": c.estimand,
                "label": c.label,
                "estimate": c.estimate,
                "se": c.se,
                "ci95": [c.ci_low, c.ci_high],
                "t": c.t_value,
                "p": c.p_value,
                "df": c.df,
                "variance_method": c.variance_method,
            }
            for c in model.results
        ],
    }


def cmd_audit(args: argparse.Namespace) -> None:
    data = load_records(args.records)
    resolver = HouseholdResolver(data)
    engine = DesignEngine(data, resolver)
    audit = engine.audit_current()
    out = {
        "frozen_draw": {
            "selected_index": audit.selected_index,
            "n_alternatives": audit.n_alternatives,
            "probabilities": list(audit.probabilities),
        },
        "common_support": audit.common_support,
        "school_confounded": audit.school_confounded,
        "slot_confounded": audit.slot_confounded,
        "cell_class_counts": {f"{s},{a}": n for (s, a), n in audit.cell_class_counts.items()},
        "cell_seat_counts": {f"{s},{a}": n for (s, a), n in audit.cell_seat_counts.items()},
        "block_diagnostics": [
            {
                "scope": d.scope,
                "key": d.key,
                "cells_present": [list(c) for c in d.cells_present],
                "fully_confounded": d.fully_confounded,
            }
            for d in audit.diagnostics
        ],
        "conflicted_households": list(audit.conflicted_households),
        "tainted_classes": list(audit.tainted_classes),
        "non_comparable_students": list(audit.non_comparable_students),
        "locked_sent_dispatches": audit.locked_sent,
        "adjustable_unsent": list(audit.adjustable_unsent),
    }
    print(_dump(out))


def cmd_households(args: argparse.Namespace) -> None:
    data = load_records(args.records)
    resolver = HouseholdResolver(data)
    out = {
        "amended_students": sorted(resolver.amended_students),
        "cross_class_households": [
            {
                "household": h.household,
                "classes": list(h.classes),
                "members": [m.student for m in h.members],
                "adult_arms_seen": list(h.adult_arms_seen),
                "adult_arms_sent": list(h.adult_arms_sent),
                "conflicted": h.conflicted,
            }
            for h in resolver.cross_class_households()
        ],
        "conflicted_households": [
            {
                "household": h.household,
                "classes": list(h.classes),
                "adult_arms_sent": list(h.adult_arms_sent),
                "members": [
                    {
                        "student": m.student,
                        "class": m.klass,
                        "adult_arm": m.adult_arm,
                        "sent": m.sent,
                    }
                    for m in h.members
                ],
            }
            for h in resolver.conflicted_households()
        ],
        "adult_arm_locks_next_round": resolver.sent_adult_arm_lock(),
    }
    print(_dump(out))


def _plan_payload(data, resolver, engine, args, links=None) -> dict[str, Any]:
    plan = engine.build_next_plan(links=links, seed=args.seed, n_batches=args.batches)
    opportunities = engine.marginal_opportunities(plan)
    return {
        "feasible": plan.feasible,
        "infeasibility_reasons": list(plan.infeasibility_reasons),
        "rng_seed": plan.rng_seed,
        "batch_count": len(plan.batches),
        "batch_probabilities": list(plan.probabilities),
        "selected_index": plan.selected_index,
        "household_constraints": plan.household_constraints,
        "cell_free_seats": {f"{s},{a}": n for (s, a), n in plan.cell_free_seats.items()},
        "block_diagnostics": [
            {
                "scope": d.scope,
                "key": d.key,
                "cells_present": [list(c) for c in d.cells_present],
                "fully_confounded": d.fully_confounded,
            }
            for d in plan.diagnostics
        ],
        "rows": [
            {
                "class": r.klass,
                "school": r.school,
                "slot": r.slot,
                "student_arm": r.student_arm,
                "adult_arm": r.adult_arm,
                "free_seats": r.free_seats,
                "confirmed_seats": r.confirmed_seats,
                "status": r.status,
                "reasons": list(r.reasons),
            }
            for r in plan.rows
        ],
        "service_only": [
            {
                "class": r.klass,
                "school": r.school,
                "slot": r.slot,
                "free_seats": r.free_seats,
                "confirmed_seats": r.confirmed_seats,
                "reasons": list(r.reasons),
            }
            for r in plan.service_only
        ],
        "marginal_opportunities": {
            klass: {f"{s},{a}": p for (s, a), p in tally.items()}
            for klass, tally in opportunities.items()
        },
        "non_comparable_scope": plan.non_comparable_scope,
        "adjustable_unsent": engine.adjustable_unsent(),
    }


def cmd_plan(args: argparse.Namespace) -> None:
    data = load_records(args.records)
    resolver = HouseholdResolver(data)
    engine = DesignEngine(data, resolver)

    # 下一轮新增班级：登记容量后参与本轮受限随机化（无历史凭证，不受冻结批次约束）。
    for spec in args.new_class or []:
        try:
            klass, school, slot, available = spec.split(":")
            engine = engine.add_class_capacity(
                klass, school, slot, int(available), confirmed=0
            )
        except ValueError as exc:
            raise SystemExit(
                f"--new-class 取值须为 班级:学校:时段:名额，收到 {spec!r}: {exc}"
            ) from exc

    # 新发现手足对：家庭:班级1,班级2[,班级3]
    links = None
    for spec in args.sibling_link or []:
        try:
            household, class_list = spec.split(":")
            classes = [c.strip() for c in class_list.split(",") if c.strip()]
        except ValueError as exc:
            raise SystemExit(
                f"--sibling-link 取值须为 家庭:班级1,班级2，收到 {spec!r}: {exc}"
            ) from exc
        links = engine.register_new_sibling_link(household, classes, extra_links=links)

    payload = _plan_payload(data, resolver, engine, args, links=links)
    print(_dump(payload))
    if args.ledger:
        ledger = DecisionLedger(args.ledger)
        conflicts = [
            {
                "household": h.household,
                "classes": list(h.classes),
                "adult_arms_sent": list(h.adult_arms_sent),
            }
            for h in resolver.conflicted_households()
        ]
        ledger.append(
            kind="next_round_enrollment",
            payload={
                "feasible": payload["feasible"],
                "rows": payload["rows"],
                "service_only": payload["service_only"],
                "non_comparable_scope": payload["non_comparable_scope"],
                "infeasibility_reasons": payload["infeasibility_reasons"],
                "new_classes": args.new_class or [],
                "new_sibling_links": args.sibling_link or [],
            },
            batch_ref={
                "source": "design_engine_restricted_randomization",
                "seed": payload["rng_seed"],
                "batch_count": payload["batch_count"],
                "probabilities": payload["batch_probabilities"],
                "selected_index": payload["selected_index"],
            },
            matured_results={},
            unresolved_conflicts=conflicts,
        )


def cmd_status(args: argparse.Namespace) -> None:
    data = load_records(args.records)
    resolver = HouseholdResolver(data)
    builder = OutcomeBuilder(data, resolver, window_days=args.window)
    look = builder.build(args.look)
    print(_dump(builder.status_table(look)))


def cmd_analyze(args: argparse.Namespace) -> None:
    data = load_records(args.records)
    resolver = HouseholdResolver(data)
    builder = OutcomeBuilder(data, resolver, window_days=args.window)
    look = builder.build(args.look)
    analyzer = FactorialAnalysis()
    student_model = analyzer.analyze_students(look, include_tainted=args.include_tainted)
    household_model = analyzer.analyze_households(look)

    look_index = data.study["look_days"].index(args.look)
    monitor = Monitor(
        data.study["look_days"],
        data.study["look_alpha"],
        data.study["familywise_alpha"],
        n_comparisons=args.comparisons,
    )
    # 监测门槛只对学生端四个预指定对比施加；家长端同样登记但单独计数时由 comparisons 控制。
    student_decision = monitor.evaluate(
        look_index, args.look, student_model.results[: len(ESTIMANDS)]
    )
    out = {
        "look_day": args.look,
        "look_index": look_index,
        "window_days": look.window_days,
        "maturity": builder.status_table(look),
        "monitoring_plan": {
            "look_days": list(monitor.plan.look_days),
            "look_alpha": list(monitor.plan.look_alpha),
            "familywise_alpha": monitor.plan.familywise_alpha,
            "registered_comparisons": monitor.plan.n_comparisons,
            "per_comparison_alpha": list(monitor.plan.per_comparison_alpha),
        },
        "student_model": _model_to_json(student_model),
        "household_model": _model_to_json(household_model),
        "student_look_decision": {
            "is_final": student_decision.is_final,
            "per_comparison_alpha": student_decision.per_comparison_alpha,
            "overall": student_decision.overall,
            "decisions": list(student_decision.decisions),
        },
    }
    print(_dump(out))
    if args.ledger:
        ledger = DecisionLedger(args.ledger)
        ledger.append(
            kind="look_analysis",
            payload={
                "look_day": args.look,
                "look_index": look_index,
                "student_look_decision": out["student_look_decision"],
            },
            batch_ref={
                "source": "frozen_original_draw",
                "selected_index": data.original_draw["selected_index"],
                "probabilities": data.original_draw["probabilities"],
            },
            matured_results=builder.status_table(look),
            unresolved_conflicts=[
                {
                    "household": h.household,
                    "classes": list(h.classes),
                    "adult_arms_sent": list(h.adult_arms_sent),
                }
                for h in resolver.conflicted_households()
            ],
        )


def cmd_randomization_check(args: argparse.Namespace) -> None:
    data = load_records(args.records)
    resolver = HouseholdResolver(data)
    builder = OutcomeBuilder(data, resolver, window_days=args.window)
    look = builder.build(args.look)
    analyzer = FactorialAnalysis()
    alternatives = [
        {row["class"]: (row["student_arm"], row["adult_arm"]) for row in alt}
        for alt in data.original_draw["alternatives"]
    ]
    result = analyzer.randomization_check(look, alternatives, outcome="student")
    print(_dump(result))


def cmd_trace(args: argparse.Namespace) -> None:
    ledger = DecisionLedger(args.ledger)
    if args.entry:
        print(_dump(ledger.audit_trail(args.entry)))
    else:
        print(_dump([ledger.audit_trail(e.entry_id) for e in ledger.read_all()]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trial_tools", description="家校双邀请两因素试验办理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("audit", help="核对冻结设计、共同支持与冲突范围")
    p.add_argument("records", type=Path)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("households", help="解析家庭/手足/跨班连接与家长邀请冲突")
    p.add_argument("records", type=Path)
    p.set_defaults(func=cmd_households)

    p = sub.add_parser("plan", help="生成下一轮可发出安排、分配机会与不可比较范围")
    p.add_argument("records", type=Path)
    p.add_argument("--seed", type=int, default=20261124)
    p.add_argument("--batches", type=int, default=4)
    p.add_argument(
        "--new-class",
        action="append",
        metavar="班级:学校:时段:名额",
        help="下一轮新增班级，可重复给出，例如 C24:S2:T0:8",
    )
    p.add_argument(
        "--sibling-link",
        action="append",
        metavar="家庭:班级1,班级2",
        help="新发现的手足对，可重复给出，例如 F200:C02,C08",
    )
    p.add_argument("--ledger", type=Path, default=None)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("status", help="某查看日的成熟度与删失分布（不把缺失当零）")
    p.add_argument("records", type=Path)
    p.add_argument("--look", type=int, required=True)
    p.add_argument("--window", type=int, default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("analyze", help="ITT 两因素估计、稳健方差与查看门槛判断")
    p.add_argument("records", type=Path)
    p.add_argument("--look", type=int, required=True)
    p.add_argument("--window", type=int, default=None)
    p.add_argument("--comparisons", type=int, default=4, help="首次查看前登记并锁定的比较数量")
    p.add_argument("--include-tainted", action="store_true", help="附带污染范围的敏感性估计")
    p.add_argument("--ledger", type=Path, default=None)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("randomization-check", help="在冻结备选批次上做随机化推断核对")
    p.add_argument("records", type=Path)
    p.add_argument("--look", type=int, required=True)
    p.add_argument("--window", type=int, default=None)
    p.set_defaults(func=cmd_randomization_check)

    p = sub.add_parser("trace", help="追溯台账中的入组/查看决定")
    p.add_argument("ledger", type=Path)
    p.add_argument("--entry", type=str, default=None)
    p.set_defaults(func=cmd_trace)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
