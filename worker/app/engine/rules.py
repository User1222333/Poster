"""Regułowy silnik mapowania. Bez eval, bez dowolnego kodu, bez AI.

Reguła = docelowa kolumna + lista kroków wykonywanych po kolei. Każdy krok jest jednym z
zamkniętej listy typów (STEP_TYPES). Dwuznaczność nigdy nie jest rozstrzygana automatycznie —
powstaje zgłoszenie REVIEW, a wartość pozostaje pusta lub niezmieniona.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser

from .aliases import CANONICAL_FIELDS, hint_field
from .normalize import (
    clean_text,
    convert_unit,
    fold,
    format_decimal,
    normalize_unit,
    parse_quantity,
    single_line,
    split_dimensions,
)

ROW_TARGET = "__row__"
STEP_TYPES = [
    "direct_field", "fixed_value", "alias_lookup", "value_dictionary", "category_mapping",
    "regex_extract", "normalize_units", "split_dimension", "concatenate", "conditional",
    "sanitize_description", "transform_html", "skip_row", "flag_missing", "require_review",
]
SOURCE_STEPS = {"direct_field", "fixed_value", "alias_lookup", "concatenate"}
# Zamknięta lista bezpiecznych wzorców (liniowych, bez zagnieżdżonych kwantyfikatorów).
SAFE_PATTERNS: dict[str, tuple[str, str]] = {
    "first_number": (r"([+-]?\d+(?:[.,]\d+)?)", "Pierwsza liczba w tekście"),
    "first_integer": (r"(\d+)", "Pierwsza liczba całkowita"),
    "percent": (r"(\d+(?:[.,]\d+)?)\s*%", "Liczba przed znakiem %"),
    "before_comma": (r"^([^,]+)", "Tekst przed pierwszym przecinkiem"),
    "after_last_slash": (r"([^/>]+)\s*$", "Ostatni element ścieżki (po / lub >)"),
    "digits_only": (r"(\d[\d ]*\d|\d)", "Ciąg cyfr"),
}
_COMPILED = {k: re.compile(v[0]) for k, v in SAFE_PATTERNS.items()}
OPS = ["equals", "not_equals", "contains", "empty", "not_empty", "in"]
SEVERITIES = ("BLOCKER", "REVIEW", "WARNING")
_IMG_REF = re.compile(r"^images\[(\d+)\]$")
VOID_TAGS = {"br", "hr"}


# ------------------------------------------------------------------- narzędzia
def field_refs_valid(ref: str) -> bool:
    return ref in CANONICAL_FIELDS or ref == "$value" or bool(_IMG_REF.match(ref)) or \
        (ref.startswith("custom:") and len(ref) > 7)


def get_field(product: dict, ref: str) -> str:
    if ref.startswith("custom:"):
        return product.get("custom_attributes", {}).get(ref[7:], "")
    m = _IMG_REF.match(ref)
    if m:
        imgs = product.get("images") or []
        i = int(m.group(1))
        return imgs[i] if i < len(imgs) else ""
    v = product.get(ref, "")
    return "|".join(v) if isinstance(v, list) else (v or "")


def similar(query: str, options: list[tuple[str, str]], limit: int = 3, cutoff: float = 0.55) -> list[dict]:
    """Kandydaci z wynikiem podobieństwa — wyłącznie do wyświetlenia użytkownikowi."""
    q = fold(query)
    scored = []
    for code, label in options:
        score = max(SequenceMatcher(None, q, fold(t)).ratio() for t in (code, label or code))
        if score >= cutoff:
            scored.append({"value": code, "label": label, "score": round(score, 2)})
    scored.sort(key=lambda s: (-s["score"], s["value"]))
    return scored[:limit]


class _Sanitizer(HTMLParser):
    def __init__(self, allowed: set[str]):
        super().__init__(convert_charrefs=True)
        self.allowed, self.out, self.skip, self.removed = allowed, [], 0, set()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            self.removed.add(tag)
        elif self.skip:
            return
        elif tag in self.allowed:
            self.out.append(f"<{tag}>")
        else:
            self.removed.add(tag)
            if tag in ("br", "p", "li", "div", "tr", "h1", "h2", "h3", "h4"):
                self.out.append("\n" if not self.allowed else " ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(self.skip - 1, 0)
        elif not self.skip and tag in self.allowed and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False) if self.allowed else data)


def sanitize_html(value: str, allowed_tags: list[str]) -> tuple[str, list[str]]:
    """Zostawia tylko dozwolone znaczniki (bez atrybutów). Pusta lista = sam tekst."""
    p = _Sanitizer({t.lower() for t in allowed_tags})
    p.feed(value)
    p.close()
    return clean_text("".join(p.out)), sorted(p.removed)


def html_tags(value: str) -> set[str]:
    return {m.lower() for m in re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)", value)}


def remove_phrases(value: str, phrases: list[str]) -> tuple[str, list[str]]:
    removed = []
    for phrase in phrases:
        words = [re.escape(w) for w in single_line(phrase).split(" ") if w]
        if not words:
            continue
        rx = re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)[!.]*", re.IGNORECASE)
        if rx.search(value):
            removed.append(phrase)
            value = rx.sub("", value)
    if removed:
        value = re.sub(r"[ \t]{2,}", " ", value)
        value = re.sub(r" +([,.;:!?])", r"\1", value)
        value = re.sub(r"(<[a-z0-9]+>)\s*(</[a-z0-9]+>)", "", value)
    return clean_text(value), removed


# -------------------------------------------------------------------- kontekst
@dataclass
class Env:
    marketplace: str
    columns: list[str]
    category_column: str | None
    requirements: dict
    category_maps: dict[str, dict] = field(default_factory=dict)   # fold(źródło) -> {code, label}
    value_maps: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)  # (kolumna, kat) -> {fold: cel}
    rule_profile: dict = field(default_factory=dict)
    generated_at: str = ""


@dataclass
class _Ctx:
    product: dict
    env: Env
    target: str
    category: str = ""
    issues: list[dict] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    skip: bool = False

    def issue(self, severity: str, code: str, message: str, raw: str = "", value: str = "",
              suggestions: list | None = None) -> None:
        self.issues.append({"severity": severity, "code": code, "message": message,
                            "target_column": None if self.target == ROW_TARGET else self.target,
                            "raw_value": raw, "processed_value": value, "suggestions": suggestions or []})


def _test(cond: dict, value: str, ctx: _Ctx) -> bool:
    ref = cond.get("field", "$value")
    actual = value if ref == "$value" else get_field(ctx.product, ref)
    op, expected = cond.get("op"), cond.get("value", "")
    a = fold(actual)
    if op == "empty":
        return not actual.strip()
    if op == "not_empty":
        return bool(actual.strip())
    if op == "equals":
        return a == fold(expected)
    if op == "not_equals":
        return a != fold(expected)
    if op == "contains":
        return fold(expected) in a
    if op == "in":
        return a in {fold(v) for v in (expected if isinstance(expected, list) else [expected])}
    return False


def _to_unit(num, unit, raw_unit, step: dict, ctx: _Ctx, raw: str) -> str | None:
    """Wspólna konwersja jednostek. Brak/nieznana jednostka => REVIEW, nigdy domysł."""
    target = step.get("to")
    if raw_unit and unit is None:
        ctx.issue("REVIEW", "UNIT_UNKNOWN", f"Nieznana jednostka „{raw_unit}”.", raw, raw)
        return None
    if not target:
        if unit:
            ctx.issue("REVIEW", "UNIT_UNEXPECTED", f"Wartość zawiera jednostkę „{unit}”, a reguła nie określa "
                      "jednostki docelowej.", raw, raw)
            return None
        return format_decimal(num)
    if unit is None:
        unit = normalize_unit(get_field(ctx.product, step["unit_field"])) if step.get("unit_field") else None
        unit = unit or step.get("default_unit")
    if not unit:
        ctx.issue("REVIEW", "UNIT_MISSING", f"Brak jednostki w wartości „{raw}” — nie można przeliczyć na "
                  f"{target}. Ustaw w regule zatwierdzoną jednostkę domyślną albo popraw dane.", raw, raw)
        return None
    converted = convert_unit(num, unit, target)
    if converted is None:
        ctx.issue("REVIEW", "UNIT_MISMATCH", f"Jednostki „{unit}” nie da się przeliczyć na „{target}”.", raw, raw)
        return None
    if unit != target:
        ctx.trace.append(f"{unit}→{target}")
    return format_decimal(converted)


def _run_steps(steps: list[dict], value: str, ctx: _Ctx) -> str:
    for step in steps:
        value = _apply(step, value, ctx)
    return value


def _apply(step: dict, value: str, ctx: _Ctx) -> str:  # noqa: C901
    kind = step["type"]
    env = ctx.env
    ctx.trace.append(kind)
    if kind == "direct_field":
        ctx.sources.append(step["field"])
        return get_field(ctx.product, step["field"])
    if kind == "fixed_value":
        if step.get("approved") is not True:
            ctx.issue("BLOCKER", "FIXED_VALUE_NOT_APPROVED", "Stała wartość nie została jawnie zatwierdzona.")
            return ""
        return str(step.get("value", ""))
    if kind == "alias_lookup":
        found = {}
        for ref in step.get("fields", []):
            v = get_field(ctx.product, ref)
            if v.strip():
                found.setdefault(v, ref)
        if len(found) > 1:
            ctx.issue("REVIEW", "AMBIGUOUS_SOURCE", "Pola źródłowe zawierają różne wartości: " +
                      "; ".join(f"{r} = „{v}”" for v, r in found.items()) + ". Wybierz właściwą.",
                      suggestions=[{"value": v, "label": r, "score": None} for v, r in found.items()])
            return ""
        for v, ref in found.items():
            ctx.sources.append(ref)
            return v
        return ""
    if kind == "concatenate":
        parts = []
        for part in step.get("parts", []):
            if "field" in part:
                ctx.sources.append(part["field"])
                parts.append(get_field(ctx.product, part["field"]))
            else:
                parts.append(str(part.get("text", "")))
        if step.get("skip_empty", True):
            parts = [p for p in parts if p.strip()]
        return str(step.get("separator", " ")).join(parts)
    if kind == "conditional":
        branch = step.get("then", []) if _test(step.get("when", {}), value, ctx) else step.get("otherwise", [])
        return _run_steps(branch, value, ctx)
    if kind == "skip_row":
        if _test(step.get("when", {}), value, ctx):
            ctx.skip = True
        return value
    if kind == "flag_missing":
        if not value.strip():
            ctx.issue(step.get("severity", "BLOCKER"), "FLAGGED_MISSING",
                      step.get("message") or "Brak wartości — wymaga uzupełnienia.")
        return value
    if kind == "require_review":
        ctx.issue("REVIEW", "REVIEW_REQUIRED", step.get("reason") or "Wartość wymaga ręcznego potwierdzenia.",
                  value, value)
        return value
    if kind == "category_mapping":
        raw = value
        if not raw.strip():
            ctx.issue("BLOCKER", "CATEGORY_MISSING", "Produkt nie ma kategorii źródłowej.")
            return ""
        hit = env.category_maps.get(fold(raw))
        if hit:
            ctx.trace.append("zatwierdzony alias kategorii")
            return hit["code"]
        enum = (env.requirements.get(ctx.target) or {}).get("enum") or []
        leaf = re.split(r"[/>|]", raw)[-1]
        sugg = similar(leaf, [(e["code"], e.get("label") or "") for e in enum])
        ctx.issue("REVIEW", "CATEGORY_UNMAPPED", f"Kategoria źródłowa „{raw}” nie ma zatwierdzonego "
                  "przypisania do kategorii rynku.", raw, "", sugg)
        return ""
    if kind == "value_dictionary":
        if not value.strip():
            return value
        key = fold(value)
        for scope in ((ctx.target, ctx.category), (ctx.target, "")):
            mapped = env.value_maps.get(scope, {}).get(key)
            if mapped is not None:
                ctx.trace.append("słownik wartości")
                return mapped
        enum = (env.requirements.get(ctx.target) or {}).get("enum") or []
        if any(e["code"] == value for e in enum):
            return value
        hits = {e["code"] for e in enum if key in (fold(e["code"]), fold(e.get("label") or ""))}
        if len(hits) == 1:
            ctx.trace.append("dopasowanie do kodu z listy")
            return hits.pop()
        if step.get("strict") and not enum:
            ctx.issue("REVIEW", "VALUE_NOT_IN_DICTIONARY", f"Wartość „{value}” nie ma wpisu w słowniku.",
                      value, value)
        return value
    if kind == "regex_extract":
        m = _COMPILED[step["pattern"]].search(value)
        return m.group(1).strip() if m else ""
    if kind == "normalize_units":
        if not value.strip():
            return value
        q = parse_quantity(value)
        if q is None:
            ctx.issue("REVIEW", "NUMBER_UNPARSEABLE", f"Nie można odczytać liczby z „{value}”.", value, value)
            return value
        out = _to_unit(q[0], q[1], q[2], step, ctx, value)
        return value if out is None else out
    if kind == "split_dimension":
        if not value.strip():
            return ""
        if step.get("order_trusted") is not True:
            ctx.issue("REVIEW", "DIMENSION_ORDER_UNTRUSTED", "Kolejność wymiarów w źródle nie została "
                      "potwierdzona jako zaufana — nie rozdzielono wartości.", value, "")
            return ""
        dims = split_dimensions(value)
        idx = int(step.get("index", 0))
        if dims is None or idx >= len(dims[0]):
            ctx.issue("REVIEW", "DIMENSION_UNPARSEABLE", f"Nie można jednoznacznie rozdzielić wymiarów „{value}”.",
                      value, "")
            return ""
        out = _to_unit(dims[0][idx], dims[1], dims[1] or "", step, ctx, value)
        return "" if out is None else out
    if kind == "sanitize_description":
        phrases = list(step.get("banned_phrases") or [])
        if step.get("use_profile", True):
            phrases += [p for p in env.rule_profile.get("banned_phrases", []) if p not in phrases]
        out, removed = remove_phrases(clean_text(value), phrases)
        if removed:
            ctx.trace.append("usunięto: " + ", ".join(removed))
        return out
    if kind == "transform_html":
        allowed = step.get("allowed_tags")
        if step.get("mode", "allowlist") == "strip":
            allowed = []
        elif allowed is None:
            allowed = env.rule_profile.get("allowed_html_tags", [])
        out, removed = sanitize_html(value, allowed)
        if removed:
            ctx.trace.append("usunięto znaczniki: " + ", ".join(removed))
        return out
    raise ValueError(f"Nieznany typ kroku: {kind}")


# ------------------------------------------------------------- walidacja reguł
def _validate_steps(steps, errors: list[str], where: str, depth: int = 0) -> None:
    if not isinstance(steps, list):
        errors.append(f"{where}: lista kroków jest nieprawidłowa.")
        return
    for i, step in enumerate(steps):
        at = f"{where}, krok {i + 1}"
        kind = step.get("type") if isinstance(step, dict) else None
        if kind not in STEP_TYPES:
            errors.append(f"{at}: nieznany typ „{kind}”.")
            continue
        refs = []
        if kind == "direct_field":
            refs.append(step.get("field", ""))
        if kind == "alias_lookup":
            refs += step.get("fields") or [""]
        if kind == "concatenate":
            refs += [p["field"] for p in step.get("parts", []) if "field" in p]
            if not step.get("parts"):
                errors.append(f"{at}: brak elementów do połączenia.")
        if kind in ("conditional", "skip_row"):
            cond = step.get("when") or {}
            refs.append(cond.get("field", "$value"))
            if cond.get("op") not in OPS:
                errors.append(f"{at}: nieznany operator warunku „{cond.get('op')}”.")
        if kind == "normalize_units" and step.get("unit_field"):
            refs.append(step["unit_field"])
        for ref in refs:
            if not field_refs_valid(str(ref)):
                errors.append(f"{at}: nieznane pole źródłowe „{ref}”.")
        if kind == "fixed_value" and step.get("approved") is not True:
            errors.append(f"{at}: stała wartość wymaga jawnego zatwierdzenia.")
        if kind == "regex_extract" and step.get("pattern") not in SAFE_PATTERNS:
            errors.append(f"{at}: wzorzec spoza listy dozwolonych.")
        if kind in ("normalize_units", "split_dimension"):
            for k in ("to", "default_unit"):
                if step.get(k) and normalize_unit(step[k]) != step[k]:
                    errors.append(f"{at}: nieznana jednostka „{step[k]}”.")
        if kind == "flag_missing" and step.get("severity", "BLOCKER") not in SEVERITIES:
            errors.append(f"{at}: nieznana waga zgłoszenia.")
        if kind == "conditional":
            if depth >= 2:
                errors.append(f"{at}: zbyt głębokie zagnieżdżenie warunków.")
            else:
                _validate_steps(step.get("then", []), errors, at + " (wtedy)", depth + 1)
                _validate_steps(step.get("otherwise", []), errors, at + " (w przeciwnym razie)", depth + 1)


def validate_rules(rules: list[dict], columns: list[str]) -> list[str]:
    errors: list[str] = []
    seen = set()
    for rule in rules:
        target = rule.get("target_column")
        if target != ROW_TARGET and target not in columns:
            errors.append(f"Kolumna docelowa „{target}” nie istnieje w szablonie.")
        if target in seen:
            errors.append(f"Kolumna „{target}” ma więcej niż jedną regułę.")
        seen.add(target)
        steps = rule.get("steps")
        _validate_steps(steps, errors, f"Reguła „{target}”")
        if isinstance(steps, list) and steps and target != ROW_TARGET and \
                isinstance(steps[0], dict) and steps[0].get("type") not in SOURCE_STEPS | {"conditional"}:
            errors.append(f"Reguła „{target}”: pierwszy krok musi wskazywać źródło wartości.")
    return errors


def suggest_rules(columns: list[dict], available_fields: set[str], category_column: str | None,
                  requirements: dict) -> list[dict]:
    """Propozycja startowa wg słownika aliasów (do przejrzenia i zapisania przez użytkownika)."""
    by_field: dict[str, list[str]] = {}
    for c in columns:
        f = hint_field(c["code"]) or hint_field(c.get("label") or "")
        if f:
            by_field.setdefault(f, []).append(c["key"])
    rules = []
    for f, keys in by_field.items():
        if f not in available_fields:
            continue
        if f == "images":
            for i, key in enumerate(keys):
                rules.append({"target_column": key, "steps": [{"type": "direct_field", "field": f"images[{i}]"}]})
            continue
        if len(keys) != 1:
            continue  # niejednoznaczne — zostaje dla użytkownika
        steps: list[dict] = [{"type": "direct_field", "field": f}]
        if keys[0] == category_column:
            steps.append({"type": "category_mapping"})
        elif (requirements.get(keys[0]) or {}).get("type") == "enum":
            steps.append({"type": "value_dictionary"})
        rules.append({"target_column": keys[0], "steps": steps})
    order = {c["key"]: i for i, c in enumerate(columns)}
    rules.sort(key=lambda r: order[r["target_column"]])
    return rules


# ------------------------------------------------------------------ wykonanie
def evaluate_row(product: dict, rules: list[dict], env: Env) -> dict:
    values = {c: "" for c in env.columns}
    provenance: dict[str, dict] = {}
    issues: list[dict] = []
    skipped = False
    ordered = sorted(rules, key=lambda r: 0 if r["target_column"] == env.category_column else 1)
    category = ""
    for rule in ordered:
        target = rule["target_column"]
        ctx = _Ctx(product=product, env=env, target=target, category=category)
        value = _run_steps(rule["steps"], "", ctx)
        issues.extend(ctx.issues)
        if target == ROW_TARGET:
            skipped = skipped or ctx.skip
            continue
        value = clean_text(value) if "\n" in value else single_line(value)
        values[target] = value
        provenance[target] = {"source_field": ", ".join(dict.fromkeys(ctx.sources)) or None,
                              "rule_id": rule.get("id"), "transformation": " > ".join(ctx.trace),
                              "user_override": False, "generated_at": env.generated_at}
        if target == env.category_column:
            category = value
    return {"values": values, "provenance": provenance, "issues": issues, "skipped": skipped,
            "category": category}
