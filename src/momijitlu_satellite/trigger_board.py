DEFAULT_IP = "192.168.10.16"
DEFAULT_PORT = 4660
N_CH_IN = 4
N_CH_BUSY = 5

class TriggerBoard:
    def __init__(self, conf: dict) -> None:
        """ini

        Args:
            conf (dict): dict configuration
        """

def logic_to_lut(expr: str, n_in: int = N_CH_IN) -> int:
    """'CH1 and not CH4' -> LUT. bit i = 結果 (入力 = i の各 bit, CH1 = bit0)"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"cannot parse {expr!r}") from e
    names = {f"CH{k + 1}" for k in range(n_in)}

    def ev(node, gates):
        match node:
            case ast.Expression(body=b):
                return ev(b, gates)
            case ast.BoolOp(op=ast.And(), values=vs):
                return all(ev(v, gates) for v in vs)
            case ast.BoolOp(op=ast.Or(), values=vs):
                return any(ev(v, gates) for v in vs)
            case ast.UnaryOp(op=ast.Not(), operand=o):
                return not ev(o, gates)
            case ast.Name(id=name) if name.upper() in names:
                return gates[int(name[2:]) - 1]
        raise ValueError(f"not allowed: {ast.unparse(node)!r}")

    lut = 0
    for i in range(1, 2**n_in):          # i = 0 (入力なし) は FW が見ない
        gates = [bool(i >> k & 1) for k in range(n_in)]
        if ev(tree, gates):
            lut |= 1 << i
    if lut == 0:
        raise ValueError(f"{expr!r} never fires")
    return lut