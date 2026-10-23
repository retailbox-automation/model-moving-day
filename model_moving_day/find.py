"""Find the LLM call sites of a Python repository by walking its AST (not by grep).

A call site is a call whose callee ends in a known client method (`chat.completions.create`, `responses.create`,
`messages.create`, `embeddings.create`, ...) or a litellm entry point. For each site we record the enclosing
function, the `model=` expression and, when it is a literal or a module-level string constant, its value.
Files under tests/, docs/, examples/ and similar are not scanned for sites; they are the behaviour checks.
"""
import ast, re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {"tests", "test", "docs", "doc", "examples", "example", "notebooks", "scripts", "cookbook", "benchmarks",
             "evals", ".git", ".venv", "venv", "node_modules", "build", "dist", "site-packages", "__pycache__"}
METHODS = {  # attribute-chain suffix -> (sdk, kind); sdk "openai" means any OpenAI-compatible client object
    "chat.completions.create": ("openai", "chat"),
    "chat.completions.parse": ("openai", "chat"),
    "beta.chat.completions.parse": ("openai", "chat"),
    "completions.create": ("openai", "completion"),
    "responses.create": ("openai", "responses"),
    "responses.parse": ("openai", "responses"),
    "embeddings.create": ("openai", "embedding"),
    "messages.create": ("anthropic", "chat"),
}
LITELLM = {"completion": "chat", "acompletion": "chat", "text_completion": "completion", "atext_completion": "completion",
           "responses": "responses", "aresponses": "responses", "embedding": "embedding", "aembedding": "embedding"}
MODEL_RE = re.compile(r"^(gpt-|o[1-9](-|$)|chatgpt-|claude-|gemini-|text-embedding-|davinci|babbage|mistral-|"
                      r"command-r|deepseek-|llama-?\d|qwen)", re.I)
ENV_RE = re.compile(r"MODEL|BASE_URL|API_BASE|ENDPOINT|LLM", re.I)


@dataclass
class CallSite:
    file: str
    line: int
    col: int
    func: str
    sdk: str
    api: str
    kind: str
    is_async: bool
    model_expr: str | None
    model_literal: str | None
    kwargs: list = field(default_factory=list)

    @property
    def id(self):
        return f"{self.file}:{self.line}"


@dataclass
class ModelLiteral:
    file: str
    line: int
    value: str


@dataclass
class EnvVar:
    file: str
    line: int
    name: str


@dataclass
class ScanResult:
    sites: list
    literals: list
    env_vars: list
    errors: list  # (file, message) for files that could not be parsed


def _chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _source_files(root, skip_dirs):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in skip_dirs for part in rel.parts[:-1]) or rel.name.startswith("test_") or rel.name == "conftest.py":
            continue
        yield p, rel.as_posix()


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel, constants, litellm_names):
        self.rel, self.constants, self.litellm_names = rel, constants, litellm_names
        self.stack, self.sites, self.awaited = [], [], set()

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    def visit_Await(self, node):
        if isinstance(node.value, ast.Call):
            self.awaited.add(id(node.value))
        self.generic_visit(node)

    def visit_Call(self, node):
        chain = _chain(node.func)
        hit = None
        dotted = ".".join(chain)
        for suffix, (sdk, kind) in METHODS.items():
            if dotted == suffix or dotted.endswith("." + suffix):
                hit = (sdk, suffix, kind)
                break
        if hit is None and chain:
            name = chain[-1]
            if (len(chain) == 2 and chain[0] == "litellm" and name in LITELLM) or (len(chain) == 1 and name in self.litellm_names):
                hit = ("litellm", name, LITELLM[name])
        if hit:
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            m = kw.get("model")
            expr = ast.unparse(m) if m is not None else None
            lit = None
            if isinstance(m, ast.Constant) and isinstance(m.value, str):
                lit = m.value
            elif isinstance(m, ast.Name) and m.id in self.constants:
                lit = self.constants[m.id]
            self.sites.append(CallSite(self.rel, node.lineno, node.col_offset, ".".join(self.stack) or "<module>",
                                       hit[0], hit[1], hit[2], id(node) in self.awaited or hit[1].startswith("a") and hit[0] == "litellm",
                                       expr, lit, sorted(kw)))
        self.generic_visit(node)


def _module_constants(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _litellm_imports(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "litellm":
            names |= {a.asname or a.name for a in node.names if a.name in LITELLM}
    return names


def _env_name(call):
    chain = _chain(call.func)
    if chain[-2:] in (["os", "getenv"], ["environ", "get"]) and call.args and isinstance(call.args[0], ast.Constant):
        return call.args[0].value
    return None


def scan(root, skip_dirs=SKIP_DIRS):
    root = Path(root)
    sites, lits, envs, errors = [], [], [], []
    for path, rel in _source_files(root, skip_dirs):
        try:
            tree = ast.parse(path.read_text(errors="replace"), filename=rel)
        except (SyntaxError, ValueError) as e:
            errors.append((rel, f"{type(e).__name__}: {e}"))
            continue
        v = _Visitor(rel, _module_constants(tree), _litellm_imports(tree))
        v.visit(tree)
        sites += v.sites
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and MODEL_RE.match(node.value) \
                    and len(node.value) < 60 and " " not in node.value:
                lits.append(ModelLiteral(rel, node.lineno, node.value))
            elif isinstance(node, ast.Call):
                name = _env_name(node)
                if isinstance(name, str) and ENV_RE.search(name):
                    envs.append(EnvVar(rel, node.lineno, name))
            elif isinstance(node, ast.Subscript) and _chain(node.value)[-2:] == ["os", "environ"] \
                    and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) and ENV_RE.search(node.slice.value):
                envs.append(EnvVar(rel, node.lineno, node.slice.value))
    return ScanResult(sites, lits, envs, errors)


def find_call_sites(root):
    return scan(root).sites


def find_model_literals(root):
    return scan(root).literals


def find_env_vars(root):
    return scan(root).env_vars
