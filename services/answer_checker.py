import ast
import math
import operator
import re
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9

# ─── Безопасный вычислитель выражений ───────────────────────
# Раньше здесь был eval() над ответом ученика. Даже с пустым
# __builtins__ он позволял дотянуться до атрибутов и уронить бота
# выражением вида 9**9**9. Теперь разбираем AST и разрешаем только
# числа, + - * / **, скобки и небольшой whitelist функций.

_MAX_EXPR_LEN = 300
_MAX_AST_NODES = 200
_MAX_POW_EXP = 100
_MAX_ABS_VALUE = 1e12

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "exp": math.exp,
    "ctg": lambda x: 1.0 / math.tan(x),
}


def _eval_node(node: ast.AST, names: dict) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numbers")
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise ValueError("unknown name")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("operator")
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_POW_EXP or abs(left) > _MAX_ABS_VALUE:
                raise ValueError("power too large")
        result = op(left, right)
        if isinstance(result, complex):
            raise ValueError("complex")
        return float(result)

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("unary")
        return float(op(_eval_node(node.operand, names)))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("function")
        args = [_eval_node(arg, names) for arg in node.args]
        if len(args) != 1:
            raise ValueError("arity")
        return float(_FUNCS[node.func.id](args[0]))

    raise ValueError("node")


def _safe_eval(expr: str, names: dict) -> float | None:
    if not expr or len(expr) > _MAX_EXPR_LEN:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return None
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        return None
    try:
        result = _eval_node(tree.body, names)
    except (ArithmeticError, TypeError, ValueError, KeyError, OverflowError):
        return None
    if not math.isfinite(result) or abs(result) > _MAX_ABS_VALUE:
        return None
    return result


_UNICODE_MAP = [
    ("²", "^2"), ("³", "^3"), ("⁴", "^4"), ("⁵", "^5"), ("⁶", "^6"),
    ("⁷", "^7"), ("⁸", "^8"), ("⁹", "^9"), ("⁰", "^0"),
    ("−", "-"), ("–", "-"), ("—", "-"),
    ("×", "*"), ("·", "*"), ("⋅", "*"),
]

# ─── Пары чисел (x; y) ─────────────────────────────────────

import re as _re

def _try_parse_pair(s: str | None):
    """(2; 3), (2, 3), 2;3, x=2; y=3 → (2.0, 3.0) | None"""
    if not s:
        return None
    s = s.strip().lower().replace("−", "-").replace("–", "-")
    s = _re.sub(r"[xy]\s*=\s*", "", s)             # убрать x=, y=
    has_sep = ";" in s
    paren_comma = s.startswith("(") and s.endswith(")") and "," in s
    if not (has_sep or paren_comma):
        return None
    inner = s
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    for sep in (";", ","):
        if sep in inner:
            parts = inner.split(sep, 1)
            if len(parts) == 2:
                a = _try_eval_numeric(parts[0].strip())
                b = _try_eval_numeric(parts[1].strip())
                if a is not None and b is not None:
                    return (a, b)
                return None
    return None


def _pairs_equal(a: tuple[float, float], b: tuple[float, float], tol: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol




# ─── Числовые выражения (дроби, корни) ──────────────────────

# ─── Интервалы на R ────────────────────────────────────────

_INF = float("inf")

_IV_MAP = [
    ("≤", "<="), ("≥", ">="), ("⩽", "<="), ("⩾", ">="),
    ("−", "-"), ("–", "-"), ("—", "-"),
    ("∞", "inf"), ("бесконечность", "inf"), ("беск", "inf"),
]


def _iv_norm(s: str) -> str:
    s = s.strip().lower()
    for a, b in _IV_MAP:
        s = s.replace(a, b)
    return s.replace(" ", "")


def _iv_point(s: str):
    s = s.strip()
    if s in ("inf", "+inf"):
        return _INF
    if s == "-inf":
        return -_INF
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _iv_component(s: str):
    """Одна компонента → [(lo, lo_incl, hi, hi_incl)] | None."""
    # x OP a
    m = re.match(r"^x(<=|>=|<|>)(-?[\d.,]+|inf|-inf)$", s)
    if m:
        op, val = m.group(1), m.group(2)
        v = _iv_point(val)
        if v is None:
            return None
        if op in (">", ">="):
            return [(v, op == ">=", _INF, False)]
        return [(-_INF, False, v, op == "<=")]
    # a OP x
    m = re.match(r"^(-?[\d.,]+|inf|-inf)(<=|>=|<|>)(x)$", s)
    if m:
        val, op = m.group(1), m.group(2)
        v = _iv_point(val)
        if v is None:
            return None
        if op == "<":    # a < x  →  x > a
            return [(v, False, _INF, False)]
        if op == "<=":   # a <= x →  x >= a
            return [(v, True, _INF, False)]
        if op == ">":    # a > x  →  x < a
            return [(-_INF, False, v, False)]
        return [(-_INF, False, v, True)]  # a >= x
    # a OP1 x OP2 b (двойное)
    m = re.match(r"^(-?[\d.,]+|inf|-inf)(<=|<)x(<=|<)(-?[\d.,]+|inf|-inf)$", s)
    if m:
        lo_s, op1, op2, hi_s = m.groups()
        lo_v, hi_v = _iv_point(lo_s), _iv_point(hi_s)
        if lo_v is None or hi_v is None:
            return None
        return [(lo_v, op1 == "<=", hi_v, op2 == "<=")]
    # (a, b) / [a, b] / ...
    m = re.match(r"^([\[\(])(-?[\d.,]+|inf|-inf)[;,]([-+]?[\d.,]+|inf|-inf)([\]\)])$", s)
    if m:
        lbr, lo_s, hi_s, rbr = m.groups()
        lo_v, hi_v = _iv_point(lo_s), _iv_point(hi_s)
        if lo_v is None or hi_v is None:
            return None
        return [(lo_v, lbr == "[", hi_v, rbr == "]")]
    return None


# ─── Множества чисел (корни уравнений) ─────────────────────

# ─── Символьное сравнение выражений (подстановка x) ────────

_SYMB_TEST_POINTS = [
    {"x": 2.3,  "y": 1.6,  "a": 0.8,  "b": -1.9, "c": 2.8,  "d": -1.3, "m": 3.1,  "n": -2.4, "k": 0.5,  "t": -0.7},
    {"x": -1.7, "y": -2.9, "a": 2.3,  "b": 0.9,  "c": -2.1, "d": 3.4,  "m": -0.4, "n": 1.7,  "k": -3.2, "t": 2.8},
    {"x": 3.1,  "y": 0.7,  "a": -1.2, "b": 2.5,  "c": 1.4,  "d": -2.7, "m": 4.4,  "n": -0.6, "k": 1.9,  "t": -3.6},
    {"x": -0.4, "y": 4.2,  "a": 3.7,  "b": -1.1, "c": -3.5, "d": 1.8,  "m": 0.6,  "n": 3.3,  "k": -2.5, "t": 1.2},
]


def _sym_expr_vars(s: str) -> set[str]:
    s = s.lower()
    s = re.sub(r"(sqrt|inf|abs|sin|cos|tan|ctg|log|exp|pi)", "", s)
    return set(re.findall(r"[a-z]", s))


def _sym_to_python(s: str) -> str | None:
    """Приводит строку к python-eval виду. Только базовые операции."""
    if not s:
        return None
    s = s.strip().lower()
    s = s.replace("−", "-").replace("–", "-").replace(",", ".")
    s = s.replace("\\,", "").replace("\\!", "").replace("\\;", "")
    # Латех-функции → обычные имена
    s = s.replace("\\sin", "sin").replace("\\cos", "cos").replace("\\tan", "tan")
    s = s.replace("\\log", "log").replace("\\ln", "log").replace("\\exp", "exp")
    s = s.replace("\\ctg", "ctg").replace("\\operatorname{ctg}", "ctg")
    s = s.replace("\\left", "").replace("\\right", "")
    # |expr| → abs(expr) — сначала заменим ² на ^2
    s = s.replace("²", "^2").replace("³", "^3")
    # модуль
    if "|" in s:
        # заменить |...| на abs(...) — простой случай: один или два | в строке
        parts = s.split("|")
        if len(parts) in (3, 5):
            s = parts[0] + "abs(" + parts[1] + ")" + (parts[2] if len(parts) == 3 else "abs(" + parts[3] + ")" + parts[4])
        else:
            return None
    # √(expr) → sqrt(expr)
    s = re.sub(r"√\s*\(([^)]+)\)", r"sqrt(\1)", s)
    s = re.sub(r"√\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", s)
    # ^ → **
    s = s.replace("^", "**")
    # неявное умножение: 4sqrt → 4*sqrt, 3(  → 3*(, )( → )*(
    s = re.sub(r"(\d)\s*(sqrt|\()", r"\1*\2", s)
    s = re.sub(r"\)\s*\(", r")*(", s)
    # между переменной и скобкой: x( → x*(, a(x+b) → a*(x+b).
    # Имена функций не трогаем: sin( остаётся sin(, а не sin*(.
    s = re.sub(
        r"(?<!sin)(?<!cos)(?<!tan)(?<!ctg)(?<!sqrt)(?<!abs)(?<!log)(?<!exp)([a-z])\s*\(",
        r"\1*(",
        s,
    )
    # между )( и между числом-буквой: 2x → 2*x
    s = re.sub(r"(\d)\s*([a-z])", r"\1*\2", s)
    return s


def _sym_eval_at(expr_py: str, values: dict) -> float | None:
    return _safe_eval(expr_py, {**_FUNCS, "pi": math.pi, **values})


def _symbolically_equal(a: str | None, b: str | None, tol: float = 1e-4) -> bool | None:
    """True/False если обе строки — выражения; None если не получилось."""
    if not a or not b:
        return None
    pa = _sym_to_python(a)
    pb = _sym_to_python(b)
    if pa is None or pb is None:
        return None
    # должны содержать хотя бы одну переменную — иначе это числа (обрабатывается раньше)
    if not _sym_expr_vars(pa) and not _sym_expr_vars(pb):
        return None
    used = _sym_expr_vars(pa) | _sym_expr_vars(pb)
    # поддерживаем до 3 переменных
    if len(used) > 3:
        return None
    ok_points = 0
    total_points = 0
    for pt in _SYMB_TEST_POINTS:
        vals = {k: pt[k] for k in used if k in pt}
        if len(vals) != len(used):
            continue  # каких-то переменных нет в тестовой точке
        total_points += 1
        va = _sym_eval_at(pa, vals)
        vb = _sym_eval_at(pb, vals)
        if va is None or vb is None:
            continue
        if abs(va - vb) > tol * max(1.0, abs(va), abs(vb)):
            return False
        ok_points += 1
    if total_points == 0 or ok_points < 2:
        return None
    return ok_points == total_points



def _try_parse_set(s: str | None):
    """{2; 3} → {2.0, 3.0} | None, если эталон не в фигурных скобках."""
    if not s:
        return None
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return None
    inner = re.sub(r"[xy]\d?\s*=\s*", "", inner)
    parts = [p for p in re.split(r"[;,\s]+", inner) if p]
    if not parts:
        return None
    nums = set()
    for p in parts:
        v = _try_eval_numeric(p)
        if v is None:
            return None
        nums.add(round(v, 6))
    return nums


def _try_parse_set_student(s: str | None):
    """Ученик может писать {2;3}, 2;3, 2,3, 2 3, x1=2; x2=3, 2 и 3 — всё → set."""
    if not s:
        return None
    s = s.strip()
    s = s.replace("−", "-").replace("–", "-")
    for open_, close_ in (("{", "}"), ("[", "]"), ("(", ")")):
        if s.startswith(open_) and s.endswith(close_):
            s = s[1:-1].strip()
            break
    s = re.sub(r"[xy]\d?\s*=\s*", "", s)
    s = s.replace(" и ", ";").replace(" or ", ";")
    parts = [p for p in re.split(r"[;,\s]+", s) if p]
    if not parts:
        return None
    nums = set()
    for p in parts:
        v = _try_eval_numeric(p)
        if v is None:
            return None
        nums.add(round(v, 6))
    return nums



def _try_parse_interval(s):
    """Возвращает список компонент (возможно пустой = ∅) или None."""
    if not s:
        return None
    ns = _iv_norm(s)
    if not ns:
        return None
    if ns in ("r", "всечисла", "любоечисло", "(-inf,+inf)", "(-inf;+inf)", "(-inf,inf)", "(-inf;inf)"):
        return [(-_INF, False, _INF, False)]
    if ns in ("∅", "нетрешений", "пусто", "нет"):
        return []
    # префикс x in / x∈
    ns = re.sub(r"^x(in|∈)", "", ns)
    # объединение (∪ или U-латинская как разделитель)
    parts = re.split(r"∪|\bU\b|\bu\b", ns)
    result = []
    for p in parts:
        comp = _iv_component(p.strip())
        if comp is None:
            return None
        result.extend(comp)
    # валидация: lo < hi (для не-∞)
    for lo, lo_i, hi, hi_i in result:
        if lo != -_INF and hi != _INF and lo > hi + 1e-9:
            return None
    return result


def _intervals_equal(a, b, tol: float = 1e-6) -> bool:
    if len(a) != len(b):
        return False
    def _fix(p):
        lo, lo_i, hi, hi_i = p
        if lo == -_INF:
            lo_i = False
        if hi == _INF:
            hi_i = False
        return (lo, lo_i, hi, hi_i)
    a = sorted(_fix(p) for p in a)
    b = sorted(_fix(p) for p in b)
    for p, q in zip(a, b):
        if abs(p[0] - q[0]) > tol: return False
        if p[1] != q[1]: return False
        if abs(p[2] - q[2]) > tol: return False
        if p[3] != q[3]: return False
    return True



def _try_eval_numeric(s: str | None):
    """7/3, √50, 5√2, √(8), -√2, 2.5, 1+√2, 5π → float | None."""
    if not s:
        return None
    s = s.strip().lower()
    s = s.replace("−", "-").replace("–", "-").replace(",", ".")
    s = s.replace("\\,", "").replace("\\!", "").replace("\\;", "")
    # Смешанное число: -2 1/2 → -2.5
    m = _re.fullmatch(r"([-+]?\d+)\s+(\d+)\s*/\s*(\d+)", s)
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den == 0:
            return None
        frac = num / den
        return float(whole + frac if whole >= 0 else whole - frac)
    # π → pi
    s = s.replace("π", "pi")
    # √(N) → sqrt(N)
    s = _re.sub(r"√\s*\(([^)]+)\)", r"sqrt(\1)", s)
    # √N → sqrt(N)
    s = _re.sub(r"√\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", s)
    # ² → **2 (на случай если попадёт)
    s = s.replace("^", "**")
    # неявное умножение: 4sqrt(...) → 4*sqrt(...), 2(3) → 2*(3), )( → )*(
    s = _re.sub(r"(\d)\s*(sqrt|\()", r"\1*\2", s)
    s = _re.sub(r"\)\s*\(", r")*(", s)
    s = _re.sub(r"(\d)\s*([a-z])", r"\1*\2", s)
    return _safe_eval(s, {**_FUNCS, "pi": math.pi})


def normalize_answer(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = s.replace(",", ".")
    s = s.replace("$", "")
    s = s.replace("\\,", "").replace("\\!", "").replace("\\;", "")
    for a, b in _UNICODE_MAP:
        s = s.replace(a, b)
    s = re.sub(r"\s+", "", s)
    return s


def _as_number(s: str) -> float | None:
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def check_answer(student: str, expected: str) -> tuple[bool, str]:
    """
    Возвращает (is_correct, human_explanation).
    Если в expected есть '|' — пробует каждый вариант.
    """
    # 1) Множество корней — эталон в {a; b; c}, порядок не важен
    exp_set = _try_parse_set(expected)
    if exp_set is not None:
        stu_set = _try_parse_set_student(student)
        if stu_set is None:
            return False, "ожидаются корни через ; например: 2; 3"
        if stu_set == exp_set:
            return True, "множество корней совпадает"
        return False, "множество корней не совпадает"

    # 2) Пара (x; y) — эталон в (a; b), порядок важен
    exp_pair = _try_parse_pair(expected)
    if exp_pair is not None:
        stu_pair = _try_parse_pair(student)
        if stu_pair is not None and _pairs_equal(stu_pair, exp_pair):
            return True, "пара совпадает"
        # fallback: §24 (5; 7) — интервал
        exp_iv2 = _try_parse_interval(expected)
        stu_iv2 = _try_parse_interval(student)
        if exp_iv2 is not None and stu_iv2 is not None and _intervals_equal(stu_iv2, exp_iv2):
            return True, "интервал совпадает"
        return False, "не совпадает"

    # 3) Интервал / неравенство
    exp_iv = _try_parse_interval(expected)
    if exp_iv is not None:
        stu_iv = _try_parse_interval(student)
        if stu_iv is None:
            return False, "ожидается интервал/неравенство"
        if _intervals_equal(stu_iv, exp_iv):
            return True, "интервал совпадает"
        return False, "интервал не совпадает"

    variants = [v.strip() for v in expected.split("|") if v.strip()]
    if len(variants) > 1:
        for v in variants:
            ok, reason = _check_single(student, v)
            if ok:
                return True, f"{reason} (вариант)"
        return False, "не совпадает ни с одним вариантом"
    return _check_single(student, expected)


def _check_single(student: str, expected: str) -> tuple[bool, str]:
    ns, ne = normalize_answer(student), normalize_answer(expected)
    if not ns:
        return False, "пустой ответ"
    if ns == ne:
        return True, "точное совпадение"

    # числовое сравнение (простое)
    fs, fe = _as_number(student), _as_number(expected)
    if fs is not None and fe is not None and abs(fs - fe) < 1e-6:
        return True, "числовое совпадение"

    # числовое сравнение через eval (дроби, корни)
    vs, ve = _try_eval_numeric(student), _try_eval_numeric(expected)
    if vs is not None and ve is not None and abs(vs - ve) < 1e-4:
        return True, f"численное совпадение (~{ve:.4g})"

    # "x=2", "2°", "5 см" vs эталон "2"/"5".
    # Раньше здесь проходило "2, 5" против "2" — теперь требуем, чтобы
    # число было единственным в ответе (плюс необязательные x= и единицы).
    if fe is not None:
        m = re.fullmatch(
            r"\s*(?:[a-zа-я]\s*=\s*)?([-+]?\d+(?:[.,]\d+)?)\s*"
            r"(?:°|градус(?:ов|а)?|см|мм|м|кг|г|ч|мин|с|шт|раз)?\s*",
            student,
            re.IGNORECASE,
        )
        if m:
            value = float(m.group(1).replace(",", "."))
            if abs(value - fe) < 1e-9:
                return True, "число в ответе"

    # символьное сравнение формул
    sym = _symbolically_equal(student, expected)
    if sym is True:
        return True, "формулы эквивалентны"
    if sym is False:
        return False, "формулы не эквивалентны"

    # fuzzy
    ratio = SequenceMatcher(None, ns, ne).ratio()
    if ratio >= FUZZY_THRESHOLD:
        return True, f"похожий ответ ({ratio:.0%})"

    return False, "не совпадает"
