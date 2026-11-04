"""Modelos estadísticos en Python puro (sin numpy ni scikit-learn).

* Regresión logística regularizada (IRLS/Newton)  -> probabilidad de ganar
* Regresión ridge                                  -> margen de puntos y total
* Mezcla de ambas + distribución normal del margen -> predicción final coherente
"""
from __future__ import annotations

import math
from operator import mul
from statistics import NormalDist

from .features import FEATURES_MODELO, FEATURES_TOTAL, GRUPOS

_N = NormalDist()
VIDA_MEDIA_DIAS = 1100.0       # los juegos antiguos pesan la mitad cada ~3 años
REJILLA_LAMBDA = (1.0, 15.0, 150.0, 1500.0)
REJILLA_MEZCLA = (0.0, 0.25, 0.5, 0.75, 1.0)


# ---- álgebra lineal mínima ------------------------------------------------
def resolver_spd(A: list[list[float]], b: list[float]) -> list[float]:
    """Resuelve A x = b con Cholesky (A simétrica definida positiva)."""
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = A[i][j] - sum(map(mul, L[i][:j], L[j][:j]))
            if i == j:
                L[i][i] = math.sqrt(max(s, 1e-12))
            else:
                L[i][j] = s / L[j][j]
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(map(mul, L[i][:i], y[:i]))) / L[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


def _gram(cols, w, lam):
    d = len(cols)
    H = [[0.0] * d for _ in range(d)]
    for j in range(d):
        wx = list(map(mul, w, cols[j]))
        for k in range(j, d):
            v = sum(map(mul, wx, cols[k]))
            H[j][k] = H[k][j] = v
    for j in range(1, d):          # el intercepto no se penaliza
        H[j][j] += lam
    return H


def ajustar_ridge(X, y, w, lam):
    """X: filas con intercepto en la columna 0."""
    cols = list(zip(*X))
    H = _gram(cols, w, lam)
    wy = list(map(mul, w, y))
    b = [sum(map(mul, c, wy)) for c in cols]
    return resolver_spd(H, b)


def ajustar_logit(X, y, w, lam, iteraciones=30):
    cols = list(zip(*X))
    d = len(cols)
    beta = [0.0] * d
    for _ in range(iteraciones):
        eta = [max(-30.0, min(30.0, sum(map(mul, beta, fila)))) for fila in X]
        p = [1.0 / (1.0 + math.exp(-e)) for e in eta]
        W = [wi * pi * (1.0 - pi) + 1e-9 for wi, pi in zip(w, p)]
        r = [wi * (yi - pi) for wi, yi, pi in zip(w, y, p)]
        g = [sum(map(mul, c, r)) for c in cols]
        for j in range(1, d):
            g[j] -= lam * beta[j]
        paso = resolver_spd(_gram(cols, W, lam), g)
        beta = [b + s for b, s in zip(beta, paso)]
        if max(abs(s) for s in paso) < 1e-7:
            break
    return beta


class Escalador:
    def __init__(self, X, w):
        n = len(X[0])
        sw = sum(w)
        self.media = [sum(map(mul, w, col)) / sw for col in zip(*X)]
        self.desv = []
        for j, col in enumerate(zip(*X)):
            m = self.media[j]
            var = sum(wi * (v - m) ** 2 for wi, v in zip(w, col)) / sw
            self.desv.append(math.sqrt(var) or 1.0)

    def z(self, fila):
        return [(v - m) / s for v, m, s in zip(fila, self.media, self.desv)]


class ModeloLineal:
    """Modelo lineal (tipo 'logit' o 'ridge') sobre variables estandarizadas."""

    def __init__(self, nombres, tipo, lam):
        self.nombres, self.tipo, self.lam = list(nombres), tipo, lam
        self.escalador = None
        self.beta = None

    @classmethod
    def ajustar(cls, filas, nombres, tipo, lam, pesos=None, objetivo=None):
        m = cls(nombres, tipo, lam)
        X = [[fi.f[n] for n in nombres] for fi in filas]
        w = pesos or [1.0] * len(filas)
        m.escalador = Escalador(X, w)
        Xz = [[1.0] + m.escalador.z(f) for f in X]
        y = [objetivo(fi) for fi in filas]
        m.beta = (ajustar_logit if tipo == "logit" else ajustar_ridge)(Xz, y, w, lam)
        return m

    def _z(self, f: dict):
        return self.escalador.z([f[n] for n in self.nombres])

    def lineal(self, f: dict) -> float:
        z = self._z(f)
        return self.beta[0] + sum(map(mul, self.beta[1:], z))

    def predecir(self, f: dict) -> float:
        e = self.lineal(f)
        if self.tipo == "logit":
            return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, e))))
        return e

    def contribuciones(self, f: dict):
        z = self._z(f)
        return self.beta[0], [(n, b * zi) for n, b, zi in zip(self.nombres, self.beta[1:], z)]


def _pesos(filas, vida_media=VIDA_MEDIA_DIAS):
    ref = filas[-1].fecha.toordinal()
    w = [0.5 ** ((ref - fi.fecha.toordinal()) / vida_media) for fi in filas]
    s = sum(w)
    return [x * len(w) / s for x in w]        # media 1 => lambda comparable entre tamaños


def logloss(y, p):
    eps = 1e-6
    return -sum(math.log(max(eps, min(1 - eps, pi))) if yi else math.log(max(eps, min(1 - eps, 1 - pi)))
                for yi, pi in zip(y, p)) / len(y)


def _mezcla(p_lr: float, margen: float, sigma: float, w: float) -> float:
    return w * p_lr + (1.0 - w) * _N.cdf(margen / sigma)


class Modelo:
    """Predicción final: probabilidad, margen, total y marcador estimado."""

    def __init__(self):
        self.lr = self.mg = self.tt = None
        self.sigma = 12.0
        self.sigma_total = 17.0
        self.mezcla = 0.5
        self.hiperparametros = {}
        self.n_entrenamiento = 0

    # -- entrenamiento --
    @classmethod
    def entrenar(cls, filas, vida_media=VIDA_MEDIA_DIAS) -> "Modelo":
        if len(filas) < 300:
            raise ValueError("Hay muy pocos partidos para entrenar (mínimo 300).")
        m = cls()
        m.n_entrenamiento = len(filas)
        w = _pesos(filas, vida_media)
        corte = int(len(filas) * 0.8)
        tr, va = filas[:corte], filas[corte:]
        w_tr = _pesos(tr, vida_media)

        def mejor(nombres, tipo, objetivo, puntaje):
            mejor_l, mejor_s = None, float("inf")
            for lam in REJILLA_LAMBDA:
                mod = ModeloLineal.ajustar(tr, nombres, tipo, lam, w_tr, objetivo)
                s = puntaje(mod)
                if s < mejor_s:
                    mejor_l, mejor_s, mejor_m = lam, s, mod
            return mejor_l, mejor_m

        y_va = [fi.gana_local for fi in va]
        lam_lr, mod_lr = mejor(
            FEATURES_MODELO, "logit", lambda fi: fi.gana_local,
            lambda mod: logloss(y_va, [mod.predecir(fi.f) for fi in va]))
        lam_mg, mod_mg = mejor(
            FEATURES_MODELO, "ridge", lambda fi: fi.margen,
            lambda mod: sum((mod.predecir(fi.f) - fi.margen) ** 2 for fi in va) / len(va))
        lam_tt, mod_tt = mejor(
            FEATURES_TOTAL, "ridge", lambda fi: fi.total,
            lambda mod: sum((mod.predecir(fi.f) - fi.total) ** 2 for fi in va) / len(va))

        res_mg = [fi.margen - mod_mg.predecir(fi.f) for fi in va]
        res_tt = [fi.total - mod_tt.predecir(fi.f) for fi in va]
        m.sigma = math.sqrt(sum(r * r for r in res_mg) / len(res_mg))
        m.sigma_total = math.sqrt(sum(r * r for r in res_tt) / len(res_tt))

        p_lr = [mod_lr.predecir(fi.f) for fi in va]
        mg = [mod_mg.predecir(fi.f) for fi in va]
        mejor_w, mejor_ll = 0.5, float("inf")
        for wm in REJILLA_MEZCLA:
            ll = logloss(y_va, [_mezcla(p, g, m.sigma, wm) for p, g in zip(p_lr, mg)])
            if ll < mejor_ll:
                mejor_w, mejor_ll = wm, ll
        m.mezcla = mejor_w

        # modelos finales con todos los datos
        m.lr = ModeloLineal.ajustar(filas, FEATURES_MODELO, "logit", lam_lr, w, lambda fi: fi.gana_local)
        m.mg = ModeloLineal.ajustar(filas, FEATURES_MODELO, "ridge", lam_mg, w, lambda fi: fi.margen)
        m.tt = ModeloLineal.ajustar(filas, FEATURES_TOTAL, "ridge", lam_tt, w, lambda fi: fi.total)
        m.hiperparametros = {"lambda_logit": lam_lr, "lambda_margen": lam_mg,
                             "lambda_total": lam_tt, "mezcla_logit": mejor_w,
                             "sigma_margen": round(m.sigma, 2)}
        return m

    # -- predicción --
    def predecir(self, f: dict, ajuste_local: float = 0.0, ajuste_visita: float = 0.0) -> dict:
        p_lr = self.lr.predecir(f)
        margen_mod = self.mg.predecir(f)
        p = _mezcla(p_lr, margen_mod, self.sigma, self.mezcla)
        p = min(0.985, max(0.015, p))
        margen = self.sigma * _N.inv_cdf(p)          # margen coherente con la probabilidad
        margen += ajuste_local - ajuste_visita       # ajuste manual (bajas, etc.)
        p = min(0.985, max(0.015, _N.cdf(margen / self.sigma)))
        total = self.tt.predecir(f)
        base, contrib = self.mg.contribuciones(f)
        por_var = dict(contrib)
        factores = sorted(
            ({"nombre": g, "puntos": round(sum(por_var[v] for v in vs), 2)} for g, vs in GRUPOS.items()),
            key=lambda d: -abs(d["puntos"]))
        return {
            "prob_local": p, "prob_visita": 1 - p,
            "margen": margen, "total": total,
            "pts_local": (total + margen) / 2, "pts_visita": (total - margen) / 2,
            "sigma": self.sigma, "sigma_total": self.sigma_total,
            "base_local": base, "factores": factores,
            "prob_logit": p_lr, "margen_ridge": margen_mod,
        }

    def prob_margen_mayor_que(self, margen: float, umbral: float) -> float:
        """P(margen real > umbral) con margen ~ Normal(margen, sigma)."""
        return 1.0 - _N.cdf((umbral - margen) / self.sigma)
