"""Pure-stdlib Ed25519 VERIFY (RFC 8032). No pip, no cryptography, no pynacl.

Verification only — signing happens on M's machine with a real library.
"""
import hashlib

p = 2**255 - 19
q = 2**252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p-2, p) % p
I = pow(2, (p-1)//4, p)

def _recover_x(y, sign):
    if y >= p: return None
    xx = (y*y-1) * pow(d*y*y+1, p-2, p) % p
    x = pow(xx, (p+3)//8, p)
    if (x*x - xx) % p != 0: x = (x*I) % p
    if (x*x - xx) % p != 0: return None
    if (x & 1) != sign: x = p - x
    return x

def _edwards_add(P, Q):
    x1,y1,z1,t1 = P; x2,y2,z2,t2 = Q
    a = (y1-x1)*(y2-x2) % p; b = (y1+x1)*(y2+x2) % p
    c = t1*2*d*t2 % p;       e = z1*2*z2 % p
    f = b-a; g = e-c; h = e+c; i = b+a
    return (f*g % p, h*i % p, g*h % p, f*i % p)

def _scalarmult(P, e):
    if e == 0: return (0,1,1,0)
    Q = _scalarmult(P, e//2); Q = _edwards_add(Q,Q)
    return _edwards_add(Q,P) if e & 1 else Q

g_y = 4 * pow(5, p-2, p) % p
G = (_recover_x(g_y,0), g_y, 1, _recover_x(g_y,0)*g_y % p)

def _decompress(s):
    y = int.from_bytes(s, "little") & ((1<<255)-1)
    x = _recover_x(y, (s[31] >> 7) & 1)
    return None if x is None else (x, y, 1, x*y % p)

def _point_equal(P, Q):
    if (P[0]*Q[2] - Q[0]*P[2]) % p: return False
    return not (P[1]*Q[2] - Q[1]*P[2]) % p

def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """True iff `signature` is a valid Ed25519 signature. Never raises."""
    try:
        if len(public_key) != 32 or len(signature) != 64: return False
        A = _decompress(public_key)
        if A is None: return False
        R = _decompress(signature[:32])
        if R is None: return False
        S = int.from_bytes(signature[32:], "little")
        if S >= q: return False
        h = int.from_bytes(hashlib.sha512(signature[:32]+public_key+message).digest(), "little") % q
        return _point_equal(_scalarmult(G, S), _edwards_add(R, _scalarmult(A, h)))
    except Exception:
        return False
