"""Walidacja wartości wyjściowych względem ZATWIERDZONEGO profilu wymagań szablonu."""

from __future__ import annotations

from .canonical import is_http_url
from .gtin import GTIN_MESSAGES, validate_gtin
from .normalize import fold, parse_decimal
from .rules import Env, html_tags, similar

SEVERITY_ORDER = {"BLOCKER": 0, "REVIEW": 1, "WARNING": 2}


def _issue(severity, code, message, column, value="", suggestions=None) -> dict:
    return {"severity": severity, "code": code, "message": message, "target_column": column,
            "raw_value": "", "processed_value": value, "suggestions": suggestions or []}


def validate_values(values: dict[str, str], category: str, env: Env, labels: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    profile = env.rule_profile
    banned = [p for p in profile.get("banned_phrases", []) if p.strip()]
    allowed_tags = {t.lower() for t in profile.get("allowed_html_tags", [])}
    image_ext = {e.lower().lstrip(".") for e in profile.get("image_extensions", [])}
    for col in env.columns:
        req = env.requirements.get(col) or {}
        cats = req.get("categories")
        if cats and category not in cats:
            continue
        value = values.get(col, "")
        name = labels.get(col) or col
        if not value.strip():
            if req.get("required") is True:
                out.append(_issue("BLOCKER", "REQUIRED_MISSING", f"Brak wymaganej wartości: {name}.", col))
            continue
        kind = req.get("type")
        if kind == "gtin":
            ok, code = validate_gtin(value)
            if not ok:
                out.append(_issue("BLOCKER", "GTIN_INVALID", f"Błędny EAN/GTIN w polu {name}: "
                                  f"{GTIN_MESSAGES[code]}.", col, value))
        elif kind in ("number", "integer"):
            num = parse_decimal(value)
            if num is None or (kind == "integer" and num != num.to_integral_value()):
                expected = "liczby całkowitej" if kind == "integer" else "liczby"
                out.append(_issue("BLOCKER", "TYPE_INVALID", f"Pole {name} wymaga {expected}, a zawiera "
                                  f"„{value}”.", col, value))
            else:
                lo, hi = parse_decimal(req.get("min") or ""), parse_decimal(req.get("max") or "")
                if (lo is not None and num < lo) or (hi is not None and num > hi):
                    out.append(_issue("BLOCKER", "OUT_OF_RANGE", f"Wartość {value} w polu {name} jest poza "
                                      f"dozwolonym zakresem.", col, value))
        elif kind == "enum":
            enum = req.get("enum") or []
            if not any(e["code"] == value for e in enum):
                sugg = similar(value, [(e["code"], e.get("label") or "") for e in enum])
                out.append(_issue("REVIEW", "ENUM_NOT_ALLOWED", f"Wartość „{value}” nie znajduje się na liście "
                                  f"dozwolonych wartości pola {name}.", col, value, sugg))
        elif kind in ("url", "image_url"):
            if not is_http_url(value):
                out.append(_issue("BLOCKER", "URL_INVALID", f"Niepoprawny adres URL w polu {name}.", col, value))
            elif kind == "image_url" and image_ext:
                ext = value.split("?")[0].rsplit(".", 1)[-1].lower() if "." in value.split("/")[-1] else ""
                if ext not in image_ext:
                    out.append(_issue("BLOCKER", "IMAGE_FORMAT", f"Format zdjęcia „.{ext}” nie jest dozwolony "
                                      f"w profilu ({', '.join(sorted(image_ext))}).", col, value))
        if kind in ("html", "text", None) and banned:
            folded = fold(value)
            hit = [p for p in banned if fold(p) in folded]
            if hit and kind is not None:
                out.append(_issue("BLOCKER", "BANNED_PHRASE", f"Pole {name} zawiera zakazany zwrot: "
                                  f"{', '.join(hit)}.", col, value))
        if kind == "html" and allowed_tags:
            extra = html_tags(value) - allowed_tags
            if extra:
                out.append(_issue("BLOCKER", "HTML_NOT_ALLOWED", f"Pole {name} zawiera niedozwolone znaczniki "
                                  f"HTML: {', '.join(sorted(extra))}.", col, value))
        elif kind == "text" and html_tags(value):
            out.append(_issue("WARNING", "HTML_IN_TEXT", f"Pole tekstowe {name} zawiera znaczniki HTML.",
                              col, value))
        limit = req.get("max_length")
        if limit and len(value) > limit:
            out.append(_issue("BLOCKER", "TOO_LONG", f"Pole {name} ma {len(value)} znaków, a limit to {limit}.",
                              col, value))
    return out


def dataset_issues(rows: list[dict]) -> dict[int, list[dict]]:
    """Duplikaty w obrębie eksportu. `rows`: [{row_number, sku, gtin, skipped}]."""
    out: dict[int, list[dict]] = {}
    by_sku: dict[str, list[dict]] = {}
    by_gtin: dict[str, list[dict]] = {}
    for r in rows:
        if r["skipped"]:
            continue
        if r["sku"]:
            by_sku.setdefault(r["sku"], []).append(r)
        if r["gtin"]:
            by_gtin.setdefault(r["gtin"], []).append(r)
    for sku, group in by_sku.items():
        if len(group) > 1:
            lines = ", ".join(str(g["row_number"]) for g in group)
            for g in group:
                out.setdefault(g["row_number"], []).append(_issue(
                    "BLOCKER", "DUPLICATE_SKU", f"SKU „{sku}” występuje {len(group)} razy (wiersze {lines}).",
                    None, sku))
    for gtin, group in by_gtin.items():
        skus = {g["sku"] for g in group}
        if len(group) > 1 and len(skus) > 1:
            for g in group:
                out.setdefault(g["row_number"], []).append(_issue(
                    "WARNING", "GTIN_SHARED", f"EAN „{gtin}” jest przypisany do {len(skus)} różnych SKU.",
                    None, gtin))
    return out


def row_status(issues: list[dict]) -> str:
    sev = {i["severity"] for i in issues}
    return "blocked" if "BLOCKER" in sev else "review" if "REVIEW" in sev else "ready"


def project_issues(template: dict, has_rules: bool) -> list[dict]:
    """Zgłoszenia na poziomie całego projektu (nie wiersza)."""
    out = []
    if not template.get("confirmed"):
        out.append(_issue("BLOCKER", "TEMPLATE_UNCONFIRMED", "Struktura i wymagania szablonu nie zostały "
                          "zatwierdzone. Bez tego plik nie może otrzymać statusu „gotowy”.", None))
    if template.get("kind") == "unknown":
        out.append(_issue("BLOCKER", "TEMPLATE_KIND_UNKNOWN", "Nie określono, czy szablon dotyczy produktów, "
                          "czy ofert.", None))
    if not has_rules:
        out.append(_issue("BLOCKER", "NO_RULES", "Brak zapisanych reguł mapowania.", None))
    unknown = sum(1 for r in (template.get("requirements") or {}).values() if r.get("required") is None)
    if template.get("confirmed") and unknown:
        out.append(_issue("WARNING", "REQUIREMENTS_PARTIAL", f"{unknown} kolumn szablonu ma nieustaloną wymagalność "
                          "(potwierdzono traktowanie ich jako niewymaganych). Sprawdź dokumentację rynku.", None))
    if template.get("is_demo_fixture"):
        out.append(_issue("WARNING", "DEMO_TEMPLATE", "Szablon demonstracyjny (fixture testowy). Wynik NIE jest "
                          "plikiem gotowym do wysłania na marketplace.", None))
    if template.get("kind") == "mixed":
        out.append(_issue("WARNING", "TEMPLATE_MIXED", "Szablon łączy dane produktowe i ofertowe — upewnij się, "
                          "że rynek oczekuje ich w jednym pliku.", None))
    return out
