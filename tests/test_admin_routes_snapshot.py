from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "admin_routes.txt"


def _serialize_admin_routes(app):
    lines = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        if not rule.endpoint.startswith("admin."):
            continue
        methods = ",".join(sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"}))
        lines.append(f"{rule.endpoint}|{methods}|{rule.rule}")
    return "\n".join(lines) + "\n"


def test_admin_routes_snapshot(app):
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    current = _serialize_admin_routes(app)
    assert current == expected
