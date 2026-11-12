"""Version 0.28: reproducible, SQL-safe scalar feature expressions.

The user supplies a small numeric expression over x0..x7 in the order of
input_field_ids. Every AST node and function is explicitly permitted. No eval,
attributes, SQL fragments, subqueries, Python functions or user-controlled
identifiers can reach the database. Scalar expressions use only current-row
values, and are safe for a prediction-time availability check.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Callable

FORMULA_VERSION = '2.8'
MAX_LENGTH = 240
MAX_AST_NODES = 42
MAX_AST_DEPTH = 12
ALLOWED_FUNCTIONS = {
    'abs': (1, 1), 'sqrt': (1, 1), 'log1p': (1, 1),
    'round': (1, 2), 'min': (2, 4), 'max': (2, 4),
    'clip': (3, 3), 'safe_div': (2, 2), 'coalesce': (2, 4),
}
_ALIAS_PATTERN = re.compile(r'x([0-7])\Z')


@dataclass(frozen=True)
class FormulaValidation:
    expression: str
    variables: tuple[str, ...]
    operations: int
    functions: tuple[str, ...]


def _parse(expression: str, field_count: int) -> tuple[ast.Expression, FormulaValidation]:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > MAX_LENGTH:
        raise ValueError('自定义公式长度须为 1—240 个字符')
    if not 1 <= field_count <= 8:
        raise ValueError('自定义公式需要 1—8 个数值输入字段')
    try:
        root = ast.parse(expression, mode='eval')
    except SyntaxError as exc:
        raise ValueError('公式语法错误，请使用 x0、x1 等输入变量') from exc
    nodes = list(ast.walk(root))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError('公式节点数超出限制')
    refs: set[str] = set()
    funcs: set[str] = set()
    operations = 0

    def visit(node: ast.AST, depth=0):
        nonlocal operations
        if depth > MAX_AST_DEPTH:
            raise ValueError('公式嵌套层级过深')
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.BinOp):
            if type(node.op) not in (ast.Add, ast.Sub, ast.Mult, ast.Div):
                raise ValueError('公式仅支持 +、-、*、/ 运算，不允许幂或位运算')
            operations += 1
            visit(node.left, depth + 1)
            visit(node.right, depth + 1)
            return
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in (ast.UAdd, ast.USub):
                raise ValueError('公式仅支持正负号')
            operations += 1
            return visit(node.operand, depth + 1)
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float) or not math.isfinite(node.value) or abs(node.value) > 1e12:
                raise ValueError('公式只能包含绝对值不超过 1e12 的有限数字')
            return
        if isinstance(node, ast.Name):
            match = _ALIAS_PATTERN.fullmatch(node.id)
            if match is None or int(match.group(1)) >= field_count:
                raise ValueError('公式只能引用选中的输入变量 x0…x7')
            refs.add(node.id)
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise ValueError('公式包含未授权的函数调用')
            lo, hi = ALLOWED_FUNCTIONS[node.func.id]
            if node.keywords or not lo <= len(node.args) <= hi:
                raise ValueError('公式函数的参数个数不正确')
            funcs.add(node.func.id)
            operations += 1
            for arg in node.args:
                visit(arg, depth + 1)
            return
        raise ValueError('公式含有不支持的表达式节点')

    visit(root)
    if not refs:
        raise ValueError('公式至少引用一个数据字段变量')
    return root, FormulaValidation(expression.strip(), tuple(sorted(refs)), operations, tuple(sorted(funcs)))


def validate_formula(expression: str, field_count: int) -> dict:
    _, info = _parse(expression, field_count)
    return {'valid': True, 'formula_version': FORMULA_VERSION,
            'expression': info.expression, 'variables': list(info.variables),
            'functions': list(info.functions), 'operations': info.operations,
            'supported_functions': sorted(ALLOWED_FUNCTIONS), 'execution': 'duckdb_whitelist_only'}


def compile_formula(expression: str, sql_columns: list[str], units: list[str | None] | None = None) -> str:
    """Translate an inspected AST into numeric-only SQL expression."""
    root, _ = _parse(expression, len(sql_columns))
    units = units or [None] * len(sql_columns)

    def visit(node: ast.AST):
        if isinstance(node, ast.Name):
            idx = int(node.id[1:])
            return f'TRY_CAST({sql_columns[idx]} AS DOUBLE)', units[idx]
        if isinstance(node, ast.Constant):
            return repr(node.value), None
        if isinstance(node, ast.UnaryOp):
            expr, unit = visit(node.operand)
            return '(' + ('-' if isinstance(node.op, ast.USub) else '+') + expr + ')', unit
        if isinstance(node, ast.BinOp):
            left, unit_left = visit(node.left)
            right, unit_right = visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if unit_left and unit_right and unit_left != unit_right:
                    raise ValueError('公式相加减的字段单位不同，请先执行单位换算')
                return f'({left} {"+" if isinstance(node.op, ast.Add) else "-"} {right})', unit_left or unit_right
            if isinstance(node.op, ast.Mult):
                return f'({left} * {right})', None
            return f'({left} / NULLIF({right}, 0))', None
        if isinstance(node, ast.Call):
            args = [visit(a)[0] for a in node.args]
            fn = node.func.id
            if fn == 'safe_div':return f'({args[0]} / NULLIF({args[1]}, 0))', None
            if fn == 'clip':return f'(CASE WHEN {args[1]} <= {args[2]} THEN LEAST(GREATEST({args[0]}, {args[1]}), {args[2]}) ELSE NULL END)', None
            if fn == 'log1p':return f'(CASE WHEN {args[0]} > -1 THEN LN(1 + {args[0]}) ELSE NULL END)', None
            if fn == 'sqrt':return f'(CASE WHEN {args[0]} >= 0 THEN SQRT({args[0]}) ELSE NULL END)', None
            if fn == 'abs':return f'ABS({args[0]})', None
            if fn == 'round':return f'ROUND({", ".join(args)})', None
            if fn == 'min':return f'LEAST({", ".join(args)})', None
            if fn == 'max':return f'GREATEST({", ".join(args)})', None
            if fn == 'coalesce':return f'COALESCE({", ".join(args)})', None
        raise ValueError('公式编译器不允许此表达式')

    sql, _ = visit(root.body)
    return sql
