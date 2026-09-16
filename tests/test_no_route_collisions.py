from app.main import app


def test_no_ambiguous_routes():
    seen: dict[tuple[str, str], object] = {}
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path_regex"):
            continue
        for method in route.methods:
            key = (method, route.path_regex.pattern)
            if key in seen and seen[key] is not route.endpoint:
                raise AssertionError(
                    f"Route collision: {method} {route.path} "
                    f"({seen[key].__name__} vs {route.endpoint.__name__})"
                )
            seen[key] = route.endpoint
